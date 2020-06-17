"""
Author: Noah Swimmer 15 June 2020

Program to control ArduinoUNO that will measure the current through the ADR magnet by monitoring the
current-sensing resistor on the PIPER-designed HichCurrent Boost board (see picturec reference folder
for circuit drawing). Will log values to redis, will also act as a safeguard to tell the magnet current
control that the current is operating out of normal bounds.

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
    def __init__(self, port, redis=None, baudrate=115200, timeout=0.1):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)
        if redis is not None:
            hs_position = self.initialize_heat_switch(get_redis_value(redis, 'device-settings:currentduino:heatswitch'), redis)
            self.heat_switch_position = hs_position
        else:
            self.heat_switch_position = self.close_heat_switch()

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

    def open_heat_switch(self, redis=None):
        if redis is not None:
            pos = get_redis_value(redis, 'status:heatswitch')
            if pos == 'open':
                return {KEYS[3]: 'open'}
        else:
            self.send('o', connect=True)
            response = self.receive()
            if response == 'o':
                self.heat_switch_position = {KEYS[3]: 'open'}
                return self.heat_switch_position

    def close_heat_switch(self, redis=None):
        if redis is not None:
            pos = get_redis_value(redis, 'status:heatswitch')
            if pos == 'close':
                return {KEYS[3]: 'close'}
        else:
            self.send('c', connect=True)
            response = self.receive()
            if response == 'c':
                self.heat_switch_position = {KEYS[3]: 'close'}
                return self.heat_switch_position

    def initialize_heat_switch(self, position, redis=None):
        if (position == 'o') or (position == 'open'):
            status = self.open_heat_switch(redis)
        elif (position == 'c') or (position == 'close'):
            status = self.close_heat_switch(redis)
        else:
            status = self.close_heat_switch(redis)
        return status

    def run(self, redis, redis_ts):
        """
        While running properly, this will loop over and over
        """
        prev_redis_check_time = time.time()
        prev_current_query_time = time.time()
        while True:
            r_check_time = time.time()
            i_query_time = time.time()
            if (r_check_time - prev_redis_check_time) > .1:
                hs_desire = get_redis_value(redis, 'device-settings:currentduino:heatswitch')
                prev_redis_check_time = time.time()
                if hs_desire != self.heat_switch_position['device-settings:currentduino:heatswitch']:
                    if hs_desire == 'close':
                        self.close_heat_switch(redis)
                    elif hs_desire == 'open':
                        self.open_heat_switch(redis)
                else:
                    pass
            if (i_query_time - prev_current_query_time) > QUERY_INTERVAL:
                prev_current_query_time = time.time()
                try:
                    data = self.get_current_data()
                    store_high_current_board_current(redis_ts, data)
                    store_status(redis, 'OK')
                    store_high_current_board_status(redis, 'OK')
                except RedisError as e:
                    log.error(f"Redis error {e}")
                    sys.exit(1)
                except IOError as e:
                    log.error(f"Error {e}")
                    store_status(redis, f"Error {e}")


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    if 'status:highcurrentboard:current' not in redis_ts.keys('status:highcurrentboard:current'):
        redis_ts.create('status:highcurrentboard:current')
    return redis_ts


def get_redis_value(redis, key):
    try:
        return redis.get(key).decode("utf-8")
    except:
        return ''


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def store_firmware(redis):
    redis.set(FIRMWARE_KEY, CURRENTDUINO_VERSION)


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

    currentduino.run(redis, redis_ts)
