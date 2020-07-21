"""
Author: Noah Swimmer, 21 July 2020

"""

import serial
import numpy as np
from logging import getLogger
from serial import SerialException
import time
from redis import Redis, RedisError
from redistimeseries.client import Client
import sys

SETTING_KEYS = ['device-settings:sim960:mode',
                'device-settings:sim960:vout-min-limit',
                'device-settings:sim960:vout-max-limit',
                'device-settings:sim960:pid',
                'device-settings:sim960:pid-p',
                'device-settings:sim960:pid-i',
                'device-settings:sim960:pid-d',
                'device-settings:sim960:setpoint-mode',
                'device-settings:sim960:pid-control-vin-setpoint',
                'device-settings:sim960:ramp-rate',
                'device-settings:sim960:ramp-enable',
                'device-settings:sim960:vout-value']

DEFAULT_SETTING_KEYS = ['default:device-settings:sim960:mode',
                        'default:device-settings:sim960:vout-min-limit',
                        'default:device-settings:sim960:vout-max-limit',
                        'default:device-settings:sim960:pid',
                        'default:device-settings:sim960:pid-p',
                        'default:device-settings:sim960:pid-i',
                        'default:device-settings:sim960:pid-d',
                        'default:device-settings:sim960:setpoint-mode',
                        'default:device-settings:sim960:pid-control-vin-setpoint',
                        'default:device-settings:sim960:ramp-rate',
                        'default:device-settings:sim960:ramp-enable',
                        'default:device-settings:sim960:vout-value']

OUTPUT_VOLTAGE_KEY = 'status:device:sim960:hcfet-control-voltage'
INPUT_VOLTAGE_KEY = 'status:device:sim921:sim960-vout'
MAGNET_CURRENT_KEY = 'status:magnet:current'  # To get the current from the sim960. We will need to run a calibration
# test to figure out what the output voltage to current conversion is.
MAGNET_STATE_KEY = 'status:magnet:state'
HEATSWITCH_STATUS_KEY = 'status:heatswitch'
HC_BOARD_CURRENT = 'status:highcurrentboard:current'

TS_KEYS = [OUTPUT_VOLTAGE_KEY, INPUT_VOLTAGE_KEY, MAGNET_CURRENT_KEY,
           MAGNET_STATE_KEY, HEATSWITCH_STATUS_KEY, HC_BOARD_CURRENT]

STATUS_KEY = 'status:device:sim921:status'
MODEL_KEY = 'status:device:sim921:model'
FIRMWARE_KEY = 'status:device:sim921:firmware'
SERIALNO_KEY = 'status:device:sim921:sn'

COMMAND_DICT = {}

class SIM960Agent(object):
    def __init__(self, port, redis, redis_ts, baudrate=, timeout=0.1, initialize=True):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)
        time.sleep(.5)
        self.redis = redis
        self.redis_ts = redis_ts

        if initialize:
            self.initialize_sim()
        else:
            self.read_default_settings()


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    for key in TS_KEYS:
        try:
            redis_ts.create(key)
        except RedisError:
            getLogger(__name__).debug(f"KEY '{key}' already exists")
            pass

    return redis_ts


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def get_redis_value(redis, key):
    try:
        val = redis.get(key).decode("utf-8")
    except RedisError as e:
        getLogger(__name__).error(f"Error accessing {key} from redis: {e}")
        return None
    return val


def store_sim921_status(redis, status: str):
    redis.set(STATUS_KEY, status)


def store_sim921_id_info(redis, info):
    redis.set(MODEL_KEY, info[0])
    redis.set(SERIALNO_KEY, info[1])
    redis.set(FIRMWARE_KEY, info[2])


def store_redis_data(redis, data):
    for k, v in data.items():
        getLogger(__name__).info(f"Setting key:value - {k}:{v}")
        redis.set(k, v)


def store_redis_ts_data(redis_ts, data):
    for k, v in data.items():
        getLogger(__name__).info(f"Setting key:value - {k}:{v} at {int(time.time())}")
        redis_ts.add(key=k, value=v, timestamp='*')