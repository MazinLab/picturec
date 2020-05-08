"""
TODO: Make it possible to pass commands from the fridgeController to turn the HEMTs on or off
 - Allow the program to take command line input (or access things from a config file)
 - Start this program with systemd and get it up and running and restartable
 - Work with publish/subscribe to redis for hemt.enabled changes, and write the changes that are made as they are
 made. How do we want to confirm that commands have been successful and the change was made?
 - Use redistimeseries instead of walrus
 - Add formatting/writing to redis to run function
"""

import serial
import time, logging
from datetime import datetime
import numpy as np
from serial import SerialException
import redis
from redistimeseries.client import Client

REDIS_DB = 0
HEMT_VALUES = ['gate-voltage-bias', 'drain-current-bias', 'drain-voltage-bias']
KEYS = [f"status:feeline{5-i}:hemt:{j}" for i in range(5) for j in HEMT_VALUES]

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
        self.expect_response = False
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
            except (serial.SerialException, IOError):
                log.error(f"port {self.port} unavailable")
        else:
            try:
                if self.expect_response:
                    x = self.ser.read().decode("utf-8")
                    self.expect_response = False
                    self.last_sent_char = None
                    if x == self.last_sent_char:
                        return "o"
                    else:
                        self.disconnect()
                        log.warning("Arduino in unstable response state")
                        return "c"
                else:
                    x = self.ser.read().decode("utf-8")
                    if x == '':
                        return "o"
                    else:
                        self.disconnect()
                        log.warning("Arduino may be unopened")
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

    def send(self, msg=None):
        msg = '' if msg is None else msg
        con = self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
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

    def receive(self):
        con = self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
        if con == 'o':
            try:
                confirm = self.ser.readline().decode("utf-8").rstrip("\r\n")
                log.debug(f"read '{confirm}' from arduino")
                return confirm
            except (IOError, SerialException):
                self.disconnect()
                log.error("No port to read from!")
                return None
        else:
            log.debug("No reading from an unabailable port")
            return None

    def run(self):
        prevTime = time.time()
        while True:
            if (time.time() - prevTime >= self.query_interval):
                connected = True if self.connect(port=self.port, baudrate=self.baudrate, timeout=self.timeout) == "o" else False
                if connected:
                    log.debug('connected and querying...')
                    self.send('H')  # H for HEMTs
                    arduino_reply = self.receive()
                    log.info(f"Received {arduino_reply}")
                    self.last_sent_char = None
                else:
                    log.debug('not connected, wait to poll again')
                prevTime = time.time()

    # def jog(self):
    #     prevTime = time.time()
    #     timeofDisconnect = 0
    #     timeOfReconnect = 0
    #     while True:
    #         connected = True if self.connect() == "o" else False
    #         if (time.time() - prevTime >= self.query_interval) and connected:
    #             if timeofDisconnect is not 0:
    #                 log.info("Sleeping and waiting to reconnect")
    #                 timeOfReconnect = time.time()
    #                 time.sleep(10)
    #                 timeofDisconnect = 0
    #             print(f'{time.time()} querying...')
    #             log.debug("Sending Query")
    #             self.send("all")
    #             arduinoConfirmReply = self.receive()
    #             log.info(arduinoConfirmReply)
    #             arduinoInfo = self.receive()
    #             log.info(arduinoInfo)
    #             prevTime = time.time()
    #         if not connected:
    #             timeofDisconnect = time.time()


if __name__ == "__main__":
    hemtduino = Hemtduino(port="/dev/hemtduino", baudrate=115200, timeout=.03, redis_db=REDIS_DB)
    hemtduino.run()
