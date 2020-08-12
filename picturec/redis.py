"""
Author: Noah Swimmer 29 June 2020

A wrapper class to make using redis with PICTURE-C easier.

TODO: Add getting/setting function for key:value pairs
 Add function to create keys (and their rules if necessary) in redistimeseries
"""

from redis import Redis as _Redis
from redis import RedisError
from redistimeseries.client import Client as _Client
import logging

class PCRedis(object):
    def __init__(self, host='localhost', port=6379, db=0, timeseries=True, create_ts_keys=tuple()):
        self.redis = _Redis(host, port, db)
        self.redis_ts = _Client(host, port, db) if timeseries else None
        self.create_keys(create_ts_keys, timeseries=True)

    def create_keys(self, keys, timeseries=True):
        for k in keys:
            try:
                if timeseries:
                    self.redis_ts.create(k)
                else:
                    raise NotImplementedError('Only creation of ts keys implemented')
            except RedisError:
                logging.getLogger(__name__).debug(f"'{k}' already exists")

    def create_ts_keys(self, keys):
        """
        If they do not exist, create keys that are needed
        TODO: Think about if this should be in the instantiation of the PCRedis class so all timeseries keys will
         be guaranteed to exist if the picturec redis wrapper class is in use
        """

    def store(self, data, timeseries=False):
        """ Given a dictionary or iterable of key value pairs store them into redis. Store into timeseries if
        timeseries is set
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

    def read(self, keys, return_dict=True):
        """
        Given a iterable of keys read them from redis. Returns a dict of k,v pairs unless return_dict is false,
        then returns a list of values alone in the same order as the keys.

        If a key is missing from redis TODO will happen.
        """
        vals = [self.redis.get(k).decode("utf-8") for k in keys]
        return vals if not return_dict else {k: v for k, v in zip(keys, vals)}