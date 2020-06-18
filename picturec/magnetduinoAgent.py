"""
Author: Noah Swimmer 15 June 2020

Program to control ArduinoUNO that will measure the current through the ADR magnet by monitoring the
current-sensing resistor on the PIPER-designed HichCurrent Boost board (see picturec reference folder
for circuit drawing). Will log values to redis, will also act as a safeguard to tell the magnet current
control that the current is operating out of normal bounds. NOTE: Redis/redistimeseries MUST be set up
for the currentduino to work.

TODO: - Make key creation more intuitive (instead of searching if it already exists, just handle the
 exception for a pre-existing key)
 - Add interaction between redis and currentduino (to enable heat switch control)
 - Add ability to compare current value from high current board ('status:highcurrentboard:current') to that
 of the magnet ('status:magnet:current') from the SIM960
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

CURRENTDUINO_VERSION = "0.2"
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
    def __init__(self, port, redis, redis_ts, baudrate=115200, timeout=0.1):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)
        self.redis = redis
        self.redis_ts = redis_ts
        self.heat_switch_position = get_redis_value(self.redis, 'status:heatswitch')
        # self.initialize_heat_switch()

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

    def open_heat_switch(self):
        current_position = get_redis_value(self.redis, KEYS[3])
        if current_position[KEYS[3]] == 'open':
            getLogger(__name__).debug(f"Heat switch was already open")
            return {KEYS[3]: 'open'}
        else:
            try:
                self.send("o")
                confirm = self.receive()
                self.heat_switch_position = {KEYS[3]: 'open'}
                store_heat_switch_status(self.redis, {KEYS[3]: 'open'})
            except RedisError as e:
                getLogger(__name__).error(f"Redis error: {e}")
                return {KEYS[3]: 'open'}
            except Exception as e:
                getLogger(__name__).error(f"Error: {e}")
                return {KEYS[3]: get_redis_value(self.redis, KEYS[3])}

    def close_heat_switch(self):
        current_position = get_redis_value(self.redis, KEYS[3])
        if current_position[KEYS[3]] == 'open':
            getLogger(__name__).debug(f"Heat switch was already close")
            return {KEYS[3]: 'close'}
        else:
            try:
                self.send("c")
                confirm = self.receive()
                self.heat_switch_position = {KEYS[3]: 'close'}
                store_heat_switch_status(self.redis, {KEYS[3]: 'close'})
            except RedisError as e:
                getLogger(__name__).error(f"Redis error: {e}")
                return {KEYS[3]: 'close'}
            except Exception as e:
                getLogger(__name__).error(f"Error: {e}")
                return {KEYS[3]: get_redis_value(self.redis, KEYS[3])}


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    try:
        redis_ts.create('status:highcurrentboard:current')
    except RedisError:
        # log.debug(f"KEY 'status:highcurrentboard:current' already exists")
        pass
    return redis_ts


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def store_firmware(redis):
    redis.set(FIRMWARE_KEY, CURRENTDUINO_VERSION)


def get_redis_value(redis, key):
    try:
        return {key: redis.get(key).decode("utf-8")}
    except:
        return {key: 'NO-VALUE'}


def store_heat_switch_status(redis, data):
    for k, v in data.items():
        redis.set(k, v)


def store_high_current_board_status(redis, status:str):
    redis.set(KEYS[4], status)


def store_high_current_board_current(redis_ts, data):
    for k, v in data.items():
        redis_ts.add(key=k, value=v, timestamp='*')


if __name__ == "__main__":

    logging.basicConfig()
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)

    redis_ts = setup_redis_ts(host='localhost', port=6379, db=REDIS_DB)
    redis = setup_redis(host='localhost', port=6379, db=REDIS_DB)
    currentduino = Currentduino(port='/dev/curremtduino', redis=redis, baudrate=115200, timeout=0.1)
    currentduino.connect()

    store_firmware(redis)
    time.sleep(1)
