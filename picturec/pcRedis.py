"""
Author: Noah Swimmer 29 June 2020

A wrapper class to conveniently use redis-py and redistimeseries with PICTURE-C. This includes but is not limited to
inter-program communication (using pubsub), information storage (of device settings), and data storage (thermometry,
current, etc.).
"""

from redis import Redis as _Redis
from redis import RedisError
from redistimeseries.client import Client as _Client
import logging
import time
import sys


class PCRedis(object):
    """
    The PCRedis class is the wrapper created for use in the PICTURE-C control software. A host, port, and database (db)
    must be specified to the PCRedis.redis client.
    Optionally, with the timeseries keyword, a PCRedis.redistimeseries
    client can also be created. This will use the same host, port, and db. Redistimeseries extends redis' capabilities
    with a module to allow easy time series data storage, instead of creating homemade ways to do that same thing.
    Redistimeseries keys should be created with the PCRedis object. Unlike normal redis keys, they must be created
    explicitly and should be done at the each program's start for clarity and ease.
    """
    def __init__(self, host='localhost', port=6379, db=0, timeseries=True, create_ts_keys=tuple()):
        self.redis = _Redis(host, port, db, socket_keepalive=True)
        self.redis_ts = _Client(host, port, db, socket_keepalive=True) if timeseries else None
        self.create_keys(create_ts_keys, timeseries=True)
        self.ps = None  # Redis pubsub object. None until initialized, used for inter-program communication

    def create_keys(self, keys, timeseries=True):
        """
        Given a list of keys, create them in the redis database for PICTURE-C.
        :param keys: List of strings that will be used as redis keys. These come from the PICTURE-C schema and each
        program will have (or import) a list of keys necessary for its successful operation.
        :param timeseries: Bool. If True will create redistimeseries keys. If the keys have been created already it will
        handle the error that redis raises and allow the program to continue running.
        It should not be a fatal error to try to create keys that have already been created. If this is attempted, it
        will be logged, but will not cause anything to break.
        If False will raise NotImplementedError.
        TODO: Decide if this should always be the case. Redis keys that are not for timeseries do not need to be
         created explicitly. They are created the first time that key is stored with a value.
        :return: None
        """
        for k in keys:
            try:
                if timeseries:
                    self.redis_ts.create(k)
                else:
                    raise NotImplementedError('Only creation of ts keys implemented')
            except RedisError:
                logging.getLogger(__name__).debug(f"'{k}' already exists")

    def store(self, data, timeseries=False):
        """
        Function for storing data in redis. This is a wrapper that allows us to store either type of redis key:value
        pairs (timeseries or 'normal').
        :param data: Dict/Iterable.
        If only given 1 key:value pair, must be a dict
        If given multiple key:value pairs, SHOULD be a dict {'k1':'v1', 'k2':'v2', ...} but can be a list of lists
        (('k1','v1'), ('k2','v2'), ...) although that is not preferred.
        :param timeseries: Bool
        If True: uses redis_ts.add() and uses the automatic UNIX timestamp generation keyword (timestamp='*')
        If False: uses redis.set() and stores the keys normally
        :return: None
        """
        generator = data.items() if isinstance(data, dict) else iter(data)
        if timeseries:
            if self.redis_ts is None:
                raise RuntimeError('No redis timeseries connection')
            for k, v in generator:
                logging.getLogger(__name__).info(f"Setting key:value - {k}:{v} at {int(time.time())}")
                self.redis_ts.add(key=k, value=v, timestamp='*')
        else:
            for k, v in generator:
                logging.getLogger(__name__).info(f"Setting key:value - {k}:{v}")
                self.redis.set(k, v)

    def subscribe(self, *keys):
        """
        Not yet implemented. Following docstring should be thought of as a brainstorm.
        A wrapper for creating and subscribing to a redis pubsub object in the case one
        is desired outside of the PCRedis object.
        e.g. instead of instantiating a PCRedis object and using self.ps, instantiating PCRedis object and creating a
        pubsub object externally ps_obj = PCRedis.subscribe
        """
        pass

    def publish(self, *keys):
        """
        Not yet implemented. Following docstring should be thought of as a brainstorm.
        A wrapper to enable easy publishing to a redis pubsub channel. Unlike subscribing, there's no overhead in
        doing a publish action, you can publish anytime to any channel without regard to if it is subscribed to.
        There is not necessarily a need to return anything from redis.publish(), but calling publish() returns the
        number of pubsub objects that are subscribed to the channel that was just published to. This COULD be useful
        """
        pass

    def read(self, keys, return_dict=True):
        """
        Function for reading values from corresponding keys in the redis database.
        :param keys: List. If the key being searched for exists, will return the value, otherwise returns an empty string
        :param return_dict: Bool
        If True returns a dict with matching key:value pairs
        If False returns a list whose elements correspond to the input keys list. (Not recommended if you have more
        than one key you are looking for the value of)
        :return: Dict. {'key1':'value1', 'key2':'value2', ...}
        """
        vals = [self.redis.get(k).decode("utf-8") for k in keys]
        return vals if not return_dict else {k: v for k, v in zip(keys, vals)}

    def _ps_subscribe(self, channels: list, ignore_sub_msg=False):
        """
        Function which will create a redis pubsub object (in self.ps) and subscribe to the keys given. It will also
        raise an error if there is a problem connecting to redis. This will occur either because the redis-server is not
        started or because the host/port was given incorrectly.
        :param channels: List of channels to subscribe to (even if only one channel is being subscribed to)
        :param ignore_sub_msg: Bool
        If True: No message will be sent upon the initial subscription to the channel(s)
        If False: For each channel subscribed to, a message with message['type']='subscribe' will be received.
        :return: None. Will raise an error if the program cannot communicate with redis.
        """
        logging.getLogger(__name__).info(f"Subscribing redis to {channels}")
        try:
            logging.getLogger(__name__).debug(f"Initializing redis pubsub object")
            self.ps = self.redis.pubsub(ignore_subscribe_messages=ignore_sub_msg)
            [self.ps.subscribe(key) for key in channels]
            logging.getLogger(__name__).info(f"Subscribed to: {self.ps.channels}")
        except RedisError as e:
            self.ps = None
            logging.getLogger(__name__).warning(f"Cannot create and subscribe to redis pubsub. Check to make sure redis is running! {e}")
            raise e

    def _ps_unsubscribe(self):
        """
        Unsubscribe from all of the channels that self.ps is currently subscribed to. Sets self.ps to None
        :return: No return. Will raise an error if the program cannot communicate with redis.
        TODO: Determine if there is a need for only unsubscribing from single channels
        """
        try:
            self.ps.unsubscribe()
            self.ps = None
        except RedisError as e:
            logging.getLogger(__name__).warning(f"Some new error with redis. Check the logs and try restaring! {e}")
            raise e

    def ps_listen(self, keys: list, message_handler, status_key=None, loop_interval=0.001, ignore_sub_msg=False):
        try:
            self.ps_subscribe(keys=keys, ignore_sub_msg=ignore_sub_msg)
        except RedisError as e:
            logging.getLogger(__name__).warning(f"Redis can't subscribe to {keys}. Check to make sure redis is running")
            raise e

        while True:
            try:
                msg = self.ps.get_message()
                if msg:
                    if msg['type'] == 'message':
                        logging.getLogger(__name__).info(f"Redis pubsub client received a message")
                        message_handler(msg)
                    elif msg['type'] == 'subscribe':
                        logging.getLogger(__name__).debug(f"Redis pubsub received subscribe message:\n {msg}")
                    else:
                        logging.getLogger(__name__).info(f"New type of message received! You're on your own now:\n {msg}")
                    if status_key:
                        self.store({status_key: 'okay'})
            except RedisError as e:
                logging.getLogger(__name__).warning(f"Exception in pubsub operation has occurred! Check to make sure "
                                                    f"redis is still running! {e}")
                raise e
            except IOError as e:
                logging.getLogger(__name__).error(f"Error: {e}")
                if status_key:
                    self.store({status_key: f"Error: {e}"})
            time.sleep(loop_interval)


    def handler(self, message):
        """
        Default pubsub message handler. Just prints the message received by the redis pubsub object. Will be overwritten
        in each of the agents, so that command messages can be handled however they need to be.
        """
        print(f"Default message handler: {message}")
