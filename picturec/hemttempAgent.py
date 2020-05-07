"""
TODO: Make it possible to pass commands from the fridgeController to turn the HEMTs on or off
 - Allow the program to take command line input (or access things from a config file)
 - Start this program with systemd and get it up and running and restartable
 - Work with publish/subscribe to redis for hemt.enabled changes, and write the changes that are made as they are
 made. How do we want to confirm that commands have been successful and the change was made?
 - Make sleep/reconnect more robust
 - Use redistimeseries instead of walrus
"""

import serial
import time, logging
from datetime import datetime
import numpy as np
from serial import SerialException
import walrus
import redis
from redistimeseries.client import Client

START_MARKER = '<'
END_MARKER = '>'
REDIS_DB = 0
HEMT_VALUES = ['gate-voltage-bias', 'drain-current-bias', 'drain-voltage-bias']
KEYS = [f"status:feeline{i+1}:hemt:{j}" for i in range(5) for j in HEMT_VALUES]

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

class Hemtduino(object):
    def __init__(self, port, baudrate=115200, timeout=None, queryTime=1, reconnectTime=5):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.queryTime = queryTime
        self.reconnectTime = reconnectTime
        self.setupRedis()
        self.setupSerial(port=port, baudrate=baudrate, timeout=timeout)

    def setupRedis(self):
        self.redis = Client(host='localhost', port=6379, db=REDIS_DB)
        redis_keys = self.redis.keys('status:*:hemt:*')
        redis_keys = [k.decode('utf-8') for k in redis_keys]
        [self.redis.create(key) for key in KEYS if key not in redis_keys]

    def setupSerial(self, port, baudrate=115200, timeout=1):
        log.debug(f"Setting up serial port {port}")
        self.ser = serial.Serial(baudrate=baudrate, timeout=timeout)
        self.ser.port = port
        try:
            self.ser.open()
            log.debug(f"port {self.ser.port} connection established")
        except (serial.SerialException, IOError):
            log.error(f"port {self.ser.port} unavailable")

    def connect(self):
        if self.ser is None:
            self.setupSerial(self.port, self.baudrate, self.timeout)
        try:
            x = self.ser.read().decode("utf-8")
            if (x == '') or (x == "#"):
                return "o"
            else:
                self.ser.close()
                self.ser = None
                log.warning("Arduino in unstable state!")
                return "c"
        except SerialException:
            log.warning("Error occurred during connection. Port is not open")
            try:
                log.debug("Opening port")
                self.ser.open()
                return "o"
            except IOError:
                self.ser.close()
                self.ser = None
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
        con = self.connect()
        if con == 'o':
            cmdWMarkers = START_MARKER
            cmdWMarkers += msg
            cmdWMarkers += END_MARKER
            try:
                log.debug(f"Writing message: {cmdWMarkers}")
                confirm = self.ser.write(cmdWMarkers.encode("utf-8"))
                time.sleep(.3)
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
        con = self.connect()
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

    def format_message(self, message):
        msg = np.array(message.split(' ')[:-1], dtype=float)
        msg = np.split(msg, 5)
        full_msg = [{f'feedline{5-i}:hemt:drain-voltage-bias': f'{2 * ((j[0] * (5.0/1023.0)) - 2.5)}',
                     f'feedline{5-i}:hemt:drain-current-bias': f'{j[1] * (5.0/1023.0)}',
                     f'feedline{5-i}:hemt:gate-voltage-bias': f'{j[2] * (5.0/1023.0)}'} for i,j in enumerate(msg)]
        return full_msg

    def write_to_redis(self, message):
        id = datetime.utcnow()
        self.redis_ts.feedline1.add(message[4], id=id)
        self.redis_ts.feedline2.add(message[3], id=id)
        self.redis_ts.feedline3.add(message[2], id=id)
        self.redis_ts.feedline4.add(message[1], id=id)
        self.redis_ts.feedline5.add(message[0], id=id)

    def run(self):
        prevTime = time.time()
        timeofDisconnect = 0
        timeOfReconnect = 0
        while True:
            connected = True if self.connect() == "o" else False
            if (time.time() - prevTime >= self.queryTime) and connected:
                if timeofDisconnect is not 0:
                    log.info("Sleeping and waiting to reconnect")
                    timeOfReconnect = time.time()
                    time.sleep(10)
                    timeofDisconnect = 0
                print(f'{time.time()} querying...')
                log.debug("Sending Query")
                self.send("all")
                arduinoConfirmReply = self.receive()
                log.info(arduinoConfirmReply)
                arduinoInfo = self.receive()
                log.info(arduinoInfo)
                prevTime = time.time()
                formattedInfo = self.format_message(arduinoInfo)
                self.write_to_redis(formattedInfo)
            if not connected:
                timeofDisconnect = time.time()


if __name__ == "__main__":
    hemtduino = Hemtduino(port="/dev/hemtduino", baudrate=115200, timeout=.03, reconnectTime=10)
    hemtduino.run()
