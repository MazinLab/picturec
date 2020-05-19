"""
TODO: Write more complete docstring
"""

import serial
import time
import logging
from datetime import datetime
import numpy as np
from serial import SerialException
from redistimeseries.client import Client

REDIS_DB = 0
HEMT_VALUES = ['gate-voltage-bias', 'drain-current-bias', 'drain-voltage-bias']
KEYS = [f"status:feedline{5-i}:hemt:{j}" for i in range(5) for j in HEMT_VALUES]
KEY_DICT = {msg_idx:key for (msg_idx,key) in zip(np.arange(0,15,1),KEYS)}

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

class Hemtduino(object):
    def __init__(self, port, baudrate=115200, timeout=0.01, query_interval=1, redis_db=0):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.query_interval = query_interval
        self.setup_redis(host='localhost', port=6379, db=redis_db)
        self.connect(port=port, baudrate=baudrate, timeout=timeout)
        self.last_sent_char = None

    def setup_redis(self, host='localhost', port=6379, db=0):
        self.redis = Client(host=host, port=port, db=db)
        redis_keys = self.redis.keys('status:*:hemt:*')
        redis_keys = [k.decode('utf-8') for k in redis_keys]
        [self.redis.create(key) for key in KEYS if key not in redis_keys]

    def connect(self, port, baudrate, timeout):
        if self.ser is None:
            log.debug(f"Setting up serial port {port}")
            try:
                self.ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
                log.debug(f"port {self.port} connection established")
                return "o"
            except (serial.SerialException, IOError):
                log.error(f"port {self.port} unavailable")
                return "c"
        else:
            try:
                x = self.ser.read().decode("utf-8")
                if x == '' or x == ' ':
                    return "o"
                else:
                    self.disconnect()
                    log.warning("Arduino in unstable response state")
                    return "c"
            except SerialException:
                log.warning("Error occurred during connection. Port is not open")
                try:
                    log.debug("Opening port")
                    self.ser.open()
                    return "o"
                except IOError:
                    self.disconnect()
                    log.warning("Error occurred in trying to open port. Check for disconnects")
                    return "c"

    def disconnect(self):
        try:
            self.ser.close()
            self.ser = None
        except Exception as e:
            print(e)

    def send(self, msg=None, con='c'):
        msg = '' if msg is None else msg
        if con == 'o':
            try:
                log.debug(f"Writing message: {msg}")
                confirm = self.ser.write(msg.encode("utf-8"))
                self.last_sent_char = msg
                self.expect_response = True
                log.debug(f"Sent {msg}")
                time.sleep(.2)
                return confirm
            except (IOError, SerialException) as e:
                print(e)
                self.disconnect()
                log.warning("Trying to write to an unopened port!")
                return None
        else:
            log.debug("No writing to an unavailable port")
            return None

    def receive(self, con='c'):
        if con == 'o':
            try:
                confirm = self.ser.readline().decode("utf-8").rstrip("\r\n")
                log.debug(f"read '{confirm}' from arduino")
                if (len(confirm) == 0) or confirm[-1] != self.last_sent_char:
                    self.disconnect()
                    self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
                    return None
                else:
                    return confirm
            except (IOError, SerialException):
                self.disconnect()
                log.error("No port to read from!")
                return None
        else:
            log.debug("No reading from an unavailable port")
            return None

    def query(self, msg):
        connected = self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
        self.send(msg, connected)
        return self.receive(connected)

    def format_message(self, reply_message):
        if reply_message == '' or reply_message is None:
            log.warning('Empty message from arduino')
            return None
        else:
            message = reply_message.split(' ')
            if message[-1] == self.last_sent_char:
                message = message[:-1]
                message = np.array(message[1:], dtype=float) if message[0] == '' else np.array(message, dtype=float)
                if len(message) != len(KEYS):
                    log.warning('only a partial message was received from the arduino')
                    return None
                else:
                    log.info(f'Message to format: {message}')
                    for i, val in enumerate(message):
                        if i % 3 == 0:
                            message[i] = 2 * ((val * (5/1023)) - 2.5)
                        else:
                            message[i] = val * (5/1023)
                    final_message = {key: value for (key, value) in zip(KEYS, message)}
                    return final_message
            else:
                log.warning('Message was received but it was nonsense')
                return None

    def send_to_redis(self, msg):
        timestamp = int(datetime.timestamp(datetime.utcnow()))
        if msg is not None:
            for k in KEYS:
                log.debug(f"Writing {msg[k]} to key {k} at {timestamp}")
                self.redis.add(key=k, value=msg[k], timestamp=timestamp)
        else:
            log.info("no valid message received from arduino, logging problem")

    def run(self):
        prev_time = time.time()
        while True:
            if time.time() - prev_time >= self.query_interval:
                connected = True if self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout) == "o" else False
                if connected:
                    log.debug('connected and querying...')
                    arduino_reply = self.query('h')
                    log.info(f"Received {arduino_reply}")
                    to_redis = self.format_message(arduino_reply)
                    self.send_to_redis(to_redis)
                    self.last_sent_char = None
                else:
                    log.debug('not connected, wait to poll again')
                    self.send_to_redis(None)
                prev_time = time.time()


if __name__ == "__main__":
    hemtduino = Hemtduino(port="/dev/hemtduino", baudrate=115200, timeout=.03, redis_db=REDIS_DB)
    hemtduino.run()
