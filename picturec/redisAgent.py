"""
Author: Noah Swimmer 29 June 2020

A wrapper class to make using redis with PICTURE-C easier.

TODO: Add getting/setting function for key:value pairs
 Add function to create keys (and their rules if necessary) in redistimeseries
"""

from redis import Redis
from redistimeseries.client import Client
import logging

class PictureCRedis(object):
    def __init__(self, host='localhost', port=6379, db=0):
        self.redis = self.setup_redis(host=host, port=port, db=db)
        self.redis_ts = self.setup_redis_ts(host=host, port=port, db=db)

    def setup_redis(self, host, port, db):
        redis = Redis(host, port, db)
        return redis

    def setup_redis_ts(self, host, port, db):
        redis_ts = Client(host, port, db)
        return redis_ts

    def create_ts_keys(self, keys):
        """
        If they do not exist, create keys that are needed
        TODO: Think about if this should be in the instantiation of the PictureCRedis class so all timeseries keys will
         be guaranteed to exist if the picturec redis wrapper class is in use
        """