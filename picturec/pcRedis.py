"""
Author: Noah Swimmer 29 June 2020

A wrapper class to make using redis with PICTURE-C easier.

TODO: - Add function to create keys (and their rules if necessary) in redistimeseries
 - Figure out how to handle redis connection errors (specifically 'server closed connection')
 - Consider making pubsub object a PCRedis class attribute (may be convenient)
"""

from redis import Redis as _Redis
from redis import RedisError
from redistimeseries.client import Client as _Client
import logging
import time
import sys



class PCRedis(object):
    def __init__(self, host='localhost', port=6379, db=0, timeseries=True, create_ts_keys=tuple()):
        self.redis = _Redis(host, port, db, socket_keepalive=True)
        self.redis_ts = _Client(host, port, db, socket_keepalive=True) if timeseries else None
        self.create_keys(create_ts_keys, timeseries=True)
        self.ps = None  # Will be used for PubSub connections

    def create_keys(self, keys, timeseries=True):
        for k in keys:
            try:
                if timeseries:
                    self.redis_ts.create(k)
                else:
                    raise NotImplementedError('Only creation of ts keys implemented')
            except RedisError:
                logging.getLogger(__name__).debug(f"'{k}' already exists")

    def store(self, data, timeseries=False):
        """ Given a dictionary or iterable of key value pairs store them into redis. Store into timeseries if
        timeseries is set
        If only given 1 key:value pair, must be a dictionary.
        If given multiple key:value pairs, it should be a dictionary {'key1':'val1', 'key2':'val2', ...} but can also
        be a list of lists (('key1','val1'),('key2',val2')). Using a non-dictionary is not preferred
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
        """"""
        pass

    def publish(self, *keys):
        """"""
        pass

    def read(self, keys, return_dict=True):
        """
        Given a iterable of keys read them from redis. Returns a dict of k,v pairs unless return_dict is false,
        then returns a list of values alone in the same order as the keys.
        """
        vals = [self.redis.get(k).decode("utf-8") for k in keys]
        return vals if not return_dict else {k: v for k, v in zip(keys, vals)}

    def ps_subscribe(self, keys: list, ignore_sub_msg=False):
        logging.getLogger(__name__).info(f"Subscribing redis to {keys}")
        try:
            logging.getLogger(__name__).debug(f"Initializing redis pubsub object")
            self.ps = self.redis.pubsub(ignore_subscribe_messages=ignore_sub_msg)
            [self.ps.subscribe(key) for key in keys]
            logging.getLogger(__name__).info(f"Subscribed to: {self.ps.channels}")
        except RedisError as e:
            self.ps = None
            logging.getLogger(__name__).warning(f"Cannot create and subscribe to redis pubsub. Check to make sure redis is running! {e}")
            raise e

    def ps_unsubscribe(self):
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
