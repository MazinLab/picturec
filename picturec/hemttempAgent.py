"""
Author: Noah Swimmer, 1 April 2020

Program for controlling ArduinoMEGA that monitors the bias voltages and currents of the transistors
of the cryogenic HEMT amplifiers in the PITCURE-C cryostat. Will log the values directly to redis database
for the fridge monitor to store/determine that the amplifiers are working properly.

TODO: - Add error checking to determine if the HEMT bias values are out of acceptable ranges
 - Add HEMT rack temperature reporting. It might make the most sense to do it on this Arduino
 since it is another mindless reading operation
 - Add 'device-settings:hemtduino:hemts-enabled' key to be used in error checking to determine
 if HEMTS should be ON or OFF (this will go along with the 'status:feedline:hemt:powered' key)
 - Is this where we want to add the HEMT S/N values to 'register' them?
 - Make key creation more intuitive (instead of searching if it already exists, just handle the
 exception for a pre-existing key)
"""

import serial
import sys
import time
import logging
from logging import getLogger
from datetime import datetime
import numpy as np
from serial import SerialException
from redis import RedisError
from redis import Redis
from redistimeseries.client import Client

REDIS_DB = 0
QUERY_INTERVAL = 3

HEMT_VALUES = ['gate-voltage-bias', 'drain-current-bias', 'drain-voltage-bias']
KEYS = [f"status:feedline{5-i}:hemt:{j}" for i in range(5) for j in HEMT_VALUES]
KEY_DICT = {msg_idx: key for (msg_idx, key) in zip(np.arange(0, 15, 1), KEYS)}
STATUS_KEY = "status:device:hemtduino:status"
FIRMWARE_KEY = "status:device:hemtduino:firmware"

class Hemtduino(object):
    def __init__(self, port, baudrate=115200, timeout=0.1):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)

    def connect(self, reconnect=False, raise_errors=True):
        if reconnect:
            self.disconnect()

        try:
            if self.ser.isOpen():
                return
        except Exception:
            pass

        getLogger(__name__).debug(f"Connecting to {self.port} at {self.baudrate}")
        try:
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
            time.sleep(.2)
            getLogger(__name__).debug(f"port {self.port} connection established")
            return True
        except (SerialException, IOError) as e:
            self.ser = None
            getLogger(__name__).error(f"Connecting to port {self.port} failed: {e}", exc_info=True)
            if raise_errors:
                raise e
            else:
                return False

    def disconnect(self):
        try:
            self.ser.close()
            self.ser = None
        except Exception as e:
            getLogger(__name__).info(f"Exception durring disconnect: {e}")

    def send(self, msg: str, connect=True):
        if connect:
            self.connect()
        try:
            getLogger(__name__).debug(f"Writing message: {msg}")
            self.ser.write(msg.encode("utf-8"))
            getLogger(__name__).debug(f"Sent {msg} successfully")
        except (SerialException, IOError) as e:
            self.disconnect()
            getLogger(__name__).error(f"Send failed: {e}")
            raise e

    def receive(self):
        try:
            data = self.ser.readline().decode("utf-8").strip()
            getLogger(__name__).debug(f"read {data} from arduino")
            if data[-1] != '?':
                raise IOError('Protocol violation')
            return data
        except (IOError, SerialException) as e:
            self.disconnect()
            getLogger(__name__).debug(f"Send failed: {e}")
            raise e

    def parse(self, response):
        if response[-1] == '?':
            response = response[:-2]
        try:
            values = list(map(float, response.strip().split(' ')))
            pvals = [val * (5/1023) if i % 3 else 2 * ((val * (5/1023)) - 2.5) for i, val in enumerate(values)]
            ret = {key: v for key, v in zip(KEYS, pvals)}
        except Exception as e:
            raise ValueError(f"Error parsing response data: {response}")
        return ret

    def get_hemt_data(self):
        try:
            self.send('?', connect=True)
            response = self.receive()
            data = self.parse(response)
        except Exception as e:
            raise IOError(e)

        return data


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    for key in KEYS:
        try:
            redis_ts.create(key)
        except RedisError:
            getLogger(__name__).debug(f"KEY '{key}' already exists")
            pass

    return redis_ts


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def store_firmware(redis, hemtduino_version):
    redis.set(FIRMWARE_KEY, hemtduino_version)


def store_hemt_data(redis_ts, data):
    for k, v in data.items():
        redis_ts.add(key=k, value=v, timestamp='*')


if __name__ == "__main__":

    logging.basicConfig()
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)

    hemtduino = Hemtduino(port="/dev/hemtduino", baudrate=115200)
    hemtduino.connect()
    redis_ts = setup_redis_ts(host='localhost', port=6379, db=REDIS_DB)
    redis = setup_redis(host='localhost', port=6379, db=REDIS_DB)

    # Add grabbing firmware value here (or in connect function whenever we connect?)

    store_firmware(redis)
    time.sleep(1)

    while True:
        try:
            data = hemtduino.get_hemt_data()
            store_hemt_data(redis_ts, data)
            store_status(redis, 'OK')
        except RedisError as e:
            log.error(f"Redis error {e}")
            sys.exit(1)
        except IOError as e:
            log.error(f"Error {e}")
            store_status(redis, f"Error {e}")

        time.sleep(QUERY_INTERVAL)
