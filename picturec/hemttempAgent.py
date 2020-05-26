"""
"""

import serial
import time
import logging
from logging import getLogger
from datetime import datetime
import numpy as np
from serial import SerialException
from redistimeseries.client import Client

HEMTDUINO_VERSION = "0.1"
REDIS_DB = 0

HEMT_VALUES = ['gate-voltage-bias', 'drain-current-bias', 'drain-voltage-bias']
KEYS = [f"status:feedline{5-i}:hemt:{j}" for i in range(5) for j in HEMT_VALUES]
KEY_DICT = {msg_idx: key for (msg_idx, key) in zip(np.arange(0, 15, 1), KEYS)}
STATUS_KEY = "status:device:hemtduino:status"
FIRMWARE_KEY = "status:device:hemtduino:firmware"

class Hemtduino(object):
    def __init__(self, port, baudrate=115200, timeout=.1):
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
            getLogger(__name__).error(f"Send failed {e}")
            # raise e

    # def receive(self, con='c'):
    #     if con == 'o':
    #         try:
    #             confirm = self.ser.readline().decode("utf-8").rstrip("\r\n")
    #             log.debug(f"read '{confirm}' from arduino")
    #             if (len(confirm) == 0) or confirm[-1] != self.last_sent_char:
    #                 self.disconnect()
    #                 self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
    #                 return None
    #             else:
    #                 return confirm
    #         except (IOError, SerialException):
    #             self.disconnect()
    #             log.error("No port to read from!")
    #             return None
    #     else:
    #         log.debug("No reading from an unavailable port")
    #         return None
    #
    # def query(self, msg):
    #     connected = self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
    #     self.send(msg, connected)
    #     return self.receive(connected)
    #
    # def format_message(self, reply_message):
    #     if reply_message == '' or reply_message is None:
    #         log.warning('Empty message from arduino')
    #         return None
    #     else:
    #         message = reply_message.split(' ')
    #         if message[-1] == self.last_sent_char:
    #             message = message[:-1]
    #             message = np.array(message[1:], dtype=float) if message[0] == '' else np.array(message, dtype=float)
    #             if len(message) != len(KEYS):
    #                 log.warning('only a partial message was received from the arduino')
    #                 return None
    #             else:
    #                 log.info(f'Message to format: {message}')
    #                 for i, val in enumerate(message):
    #                     if i % 3 == 0:
    #                         message[i] = 2 * ((val * (5/1023)) - 2.5)
    #                     else:
    #                         message[i] = val * (5/1023)
    #                 final_message = {key: value for (key, value) in zip(KEYS, message)}
    #                 return final_message
    #         else:
    #             log.warning('Message was received but it was nonsense')
    #             return None
    #
    # def send_to_redis(self, msg):
    #     timestamp = int(datetime.timestamp(datetime.utcnow()))
    #     if msg is not None:
    #         for k in KEYS:
    #             log.debug(f"Writing {msg[k]} to key {k} at {timestamp}")
    #             self.redis.add(key=k, value=msg[k], timestamp=timestamp)
    #     else:
    #         log.info("no valid message received from arduino, logging problem")
    #
    # def run(self):
    #     prev_time = time.time()
    #     while True:
    #         if time.time() - prev_time >= self.query_interval:
    #             connected = True if self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout) == "o" else False
    #             if connected:
    #                 log.debug('connected and querying...')
    #                 arduino_reply = self.query('h')
    #                 log.info(f"Received {arduino_reply}")
    #                 to_redis = self.format_message(arduino_reply)
    #                 self.send_to_redis(to_redis)
    #             else:
    #                 log.debug('not connected, wait to poll again')
    #                 self.send_to_redis(None)
    #             prev_time = time.time()


def setup_redis(host='localhost', port=6379, db=0):
    redis = Client(host=host, port=port, db=db)
    redis_keys = redis.keys('status:*:hemt:*')
    redis_keys = [k.decode('utf-8') for k in redis_keys]
    [redis.create(key) for key in KEYS if key not in redis_keys]
    return redis


def store_status(redis, status):
    redis.write(STATUS_KEY, status)


def store_firmware(redis):
    redis.write(FIRMWARE_KEY, HEMTDUINO_VERSION)


def store_hemt_data(redis, data, timestamp):
    for k, v in data.items():
        redis.add(key=k, value=v, timestamp=timestamp)


if __name__ == "__main__":

    logging.basicConfig()
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)

    hemtduino = Hemtduino(port="/dev/hemtduino", baudrate=115200)
    redis = setup_redis(host='localhost', port=6379, db=REDIS_DB)

    store_firmware(redis)