"""
Author: Noah Swimmer 15 June 2020

Program to control ArduinoUNO that will measure the current through the ADR magnet by monitoring the
current-sensing resistor on the PIPER-designed HighCurrent Boost board (see picturec reference folder
for circuit drawing). Will log values to redis, will also act as a safeguard to tell the magnet current
control that the current is operating out of normal bounds. NOTE: Redis/redistimeseries MUST be set up
for the currentduino to work.

TODO: - Add ability to compare current value from high current board ('status:highcurrentboard:current') to that
 of the magnet ('status:magnet:current') from the SIM960 - NOTE: This feels like higher level management
 - Test the heat switch touch signals to have an open/closed monitor.
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

REDIS_DB = 0
QUERY_INTERVAL = 1

KEYS = ['device-settings:currentduino:highcurrentboard',
        'device-settings:currentduino:heatswitch',
        'status:magnet:current',
        'status:heatswitch',
        'status:highcurrentboard:powered',
        'status:highcurrentboard:current']

STATUS_KEY = "status:device:currentduino:status"
FIRMWARE_KEY = "status:device:currentduino:firmware"
HEATSWITCH_STATUS_KEY = 'status:heatswitch'
HEATSWITCH_MOVE_KEY = 'device-settings:currentduino:heatswitch'

R1 = 11790  # Values for R1 resistor in magnet current measuring voltage divider
R2 = 11690  # Values for R2 resistor in magnet current measuring voltage divider

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


class Currentduino(object):
    def __init__(self, port, redis, redis_ts, baudrate=115200, timeout=0.1):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)
        time.sleep(1)
        self.redis = redis
        self.redis_ts = redis_ts
        self.heat_switch_position = None

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
        return {'status:highcurrentboard:current': current}

    def get_current_data(self):
        try:
            self.send('?', connect=True)
            response = self.receive()
            data = self.parse(response)
        except Exception as e:
            raise IOError(e)
        return data

    def open_heat_switch(self):
        try:
            current_position = get_redis_value(self.redis, HEATSWITCH_STATUS_KEY)
        except RedisError as e:
            getLogger(__name__).error(f"Redis error: {e}")
            return {HEATSWITCH_STATUS_KEY: "unknown"}
        if current_position[HEATSWITCH_STATUS_KEY] == 'open':
            return current_position
        else:
            try:
                self.send("o")
                confirm = self.receive()
                if confirm == "o":
                    return {HEATSWITCH_STATUS_KEY: "open"}
                else:
                    return {HEATSWITCH_STATUS_KEY: "unknown"}
            except Exception as e:
                raise IOError(e)

    def close_heat_switch(self):
        try:
            current_position = get_redis_value(self.redis, HEATSWITCH_STATUS_KEY)
        except RedisError as e:
            getLogger(__name__).error(f"Redis error: {e}")
            return {HEATSWITCH_STATUS_KEY: "unknown"}
        if current_position[HEATSWITCH_STATUS_KEY] == 'close':
            return current_position
        else:
            try:
                self.send("c")
                confirm = self.receive()
                if confirm == "c":
                    return {HEATSWITCH_STATUS_KEY: "close"}
                else:
                    return {HEATSWITCH_STATUS_KEY: "unknown"}
            except Exception as e:
                raise IOError(e)

    def initialize_heat_switch(self):
        try:
            desired_position = get_redis_value(self.redis, 'device-settings:currentduino:heatswitch')
            current_position = get_redis_value(self.redis, 'status:heatswitch')
        except RedisError as e:
            raise RedisError(e)

        getLogger(__name__).debug(f"Desired position is {desired_position} and currently the heat switch is {current_position}")

        if desired_position[HEATSWITCH_MOVE_KEY] == current_position[HEATSWITCH_STATUS_KEY]:
            getLogger(__name__).info(f"Initial heat switch position is: {current_position}")
            self.heat_switch_position = current_position
        else:
            if desired_position[HEATSWITCH_MOVE_KEY] == 'open':
                getLogger(__name__).info("Opening heat switch")
                self.heat_switch_position = self.open_heat_switch()
                getLogger(__name__).info(f"Heat switch set to {self.heat_switch_position}")
            elif desired_position[HEATSWITCH_MOVE_KEY] == 'close':
                getLogger(__name__).info("Closing heat switch")
                self.heat_switch_position = self.close_heat_switch()
                getLogger(__name__).info(f"Heat switch set to {self.heat_switch_position}")

        try:
            getLogger(__name__).debug(f"Storing heat switch position to redis: {self.heat_switch_position}")
            store_redis_data(self.redis, self.heat_switch_position)
        except RedisError as e:
            raise RedisError(e)

    def run(self):
        while True:
            try:
                data = self.get_current_data()
                store_redis_ts_data(self.redis_ts, data)
                store_high_current_board_status(self.redis, "OK")
            except RedisError as e:
                log.error(f"Redis error{e}")
                sys.exit(1)
            except IOError as e:
                log.error(f"Error {e}")
                store_status(self.redis, f"Error {e}")

            try:
                switch_pos = get_redis_value(self.redis, HEATSWITCH_MOVE_KEY)
                if switch_pos[HEATSWITCH_MOVE_KEY] == 'open':
                    store_redis_data(self.redis, self.open_heat_switch())
                elif switch_pos[HEATSWITCH_MOVE_KEY] == 'close':
                    store_redis_data(self.redis, self.close_heat_switch())
            except RedisError as e:
                log.error(f"Redis error{e}")
                sys.exit(1)
            except IOError as e:
                log.error(f"Error {e}")
                store_status(self.redis, f"Error {e}")

            time.sleep(QUERY_INTERVAL)


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    try:
        redis_ts.create('status:highcurrentboard:current')
    except RedisError:
        log.debug(f"KEY 'status:highcurrentboard:current' already exists")
        pass
    return redis_ts


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def store_firmware(redis, currentduino_version):
    redis.set(FIRMWARE_KEY, currentduino_version)


def get_redis_value(redis, key):
    try:
        val = redis.get(key).decode("utf-8")
    except:
        return None
    return val


def store_high_current_board_status(redis, status:str):
    redis.set('status:highcurrentboard:powered', status)


def store_redis_data(redis, data):
    for k, v in data.items():
        log.info(f"Setting key:value - {k}:{v}")
        redis.set(k, v)


def store_redis_ts_data(redis_ts, data):
    for k, v in data.items():
        log.info(f"Setting key:value - {k}:{v} at {int(time.time())}")
        redis_ts.add(key=k, value=v, timestamp='*')


if __name__ == "__main__":

    redis_ts = setup_redis_ts(host='localhost', port=6379, db=REDIS_DB)
    redis = setup_redis(host='localhost', port=6379, db=REDIS_DB)
    currentduino = Currentduino(port='/dev/currentduino', redis=redis, redis_ts=redis_ts, baudrate=115200, timeout=0.1)

    # Add grabbing firmware value here (or in connect function whenever we connect?)

    store_firmware(redis)
    time.sleep(1)

    currentduino.initialize_heat_switch()
    currentduino.run()
