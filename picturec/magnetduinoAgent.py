"""
Author: Noah Swimmer 15 June 2020

Program to control ArduinoUNO that will measure the current through the ADR magnet by monitoring the
current-sensing resistor on the PIPER-designed HichCurrent Boost board (see picturec reference folder
for circuit drawing). Will log values to redis, will also act as a safeguard to tell the magnet current
control that the current is operating out of normal bounds.

TODO: - Make key creation more intuitive (instead of searching if it already exists, just handle the
 exception for a pre-existing key)
"""

import serial
from serial import SerialException
import sys
import time
import logging
from logging import getLogger
from datetime import datetime
from redis import RedisError
from redis import Redis
from redistimeseries.client import Client

CURRENTDUINO_VERSION = "0.1"
REDIS_DB = 0
QUERY_INTERVAL = 1

KEYS = ['device-settings:currentduino:highcurrentboard', 'device-settings:currentduino:heatswitch',
        'status:magnet:current', 'status:heatswitch', 'status:highcurrentboard:powered',
        'status:highcurrentboard:current']
STATUS_KEY = "status:device:currentduino:status"
FIRMWARE_KEY = "status:device:currentduino:firmware"

R1 = 11790  # Values for R1 resistor in magnet current measuring voltage divider
R2 = 11690  # Values for R2 resistor in magnet current measuring voltage divider


class Currentduino(object):
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
            getLogger(__name__).debug(f"writing message: {msg}")
            self.ser.write(msg.encode("utf-8"))
            getLogger(__name__).debug(f"Sent {msg} successfully")
        except (SerialException, IOError) as e:
            self.disconnect()
            getLogger(__name__).error(f"Send failed: {e}")
            # raise e

    def receive(self):
        try:
            data = self.ser.readline().decode("utf-8").strip()
            getLogger(__name__).debug(f"read {data} from arduino")
            return data
        except (SerialException, IOError) as e:
            self.disconnect()
            getLogger(__name__).debug(f"Send failed: {e}")
            # raise e

    def parse(self, response):
        if response[-1] == '?':
            readValue = float(response.split(' ')[0])
        try:
            current = (readValue * (5.0 / 1023.0) * ((R1 + R2) / R2))
        except Exception:
            raise ValueError(f"Couldn't convert {response.split(' ')[0]} to float")
        return {KEYS[5]: current}

    def get_current_data(self):
        try:
            self.send('?', connect=True)
            response = self.receive()
            data = self.parse(response)
        except Exception as e:
            raise IOError(e)
        return data


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    if 'status:highcurrentboard:current' not in redis_ts.keys('status:highcurrentboard:current'):
        redis_ts.create('status:highcurrentboard:current')
    return redis_ts


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def store_firmware(redis):
    redis.set(FIRMWARE_KEY, CURRENTDUINO_VERSION)


def store_heat_switch_status(redis, status:str):
    redis.set(KEYS[3], status)


def store_high_current_board_status(redis, status:str):
    redis.set(KEYS[4], status)


def store_high_current_board_current(redis_ts, data):
    for k, v in data.items():
        redis_ts.add(key=k, value=v, timestamp='*')


if __name__ == "__main__":

    logging.basicConfig()
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)

    currentduino = Currentduino(port='/dev/curremtduino', baudrate=115200)
    currentduino.connect()
