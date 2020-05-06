"""
TODO: Make it possible to pass commands from the fridgeController to turn the HEMTs on or off
 - Allow the program to take command line input (or access things from a config file)
 - Start this program with systemd and get it up and running and restartable
 - Work with publish/subscribe to redis for hemt.enabled changes, and write the changes that are made as they are
 made. How do we want to confirm that commands have been successful and the change was made?
 - Make sleep/reconnect more robust
"""

import serial
import time, logging
from datetime import datetime
import numpy as np
from serial import SerialException
import walrus

START_MARKER = '<'
END_MARKER = '>'
REDIS_DB = 0

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
        self.redis = walrus.Walrus(host='localhost', port=6379, db=REDIS_DB)
        self.redis_ts = self.redis.time_series('status', [f'feedline{i+1}' for i in range(5)])
        self.setupSerial(port=port, baudrate=baudrate, timeout=timeout)

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

    def send(self, msg):
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
        msg = np.array(message.split(' ')[:-1])
        msg = np.split(message, 5)
        full_msg = [{f'feedline{i+1}:hemt:drain-voltage-bias': f'{2* ((msg[i][0] * (5.0/1023.0)) - 2.5)}',
                     f'feedline{i+1}:hemt:drain-current-bias': f'{msg[i][1] * (5.0/1023.0)}',
                     f'feedline{i+1}:hemt:gate-voltage-bias': f'{msg[i][2] * (5.0/1023.0)}'} for i in msg]
        return full_msg

    def write_to_redis(self, message):
        id = datetime.utcnow()
        for i, stream in enumerate(self.redis_ts):
            stream.add(message[i], id=id)

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
