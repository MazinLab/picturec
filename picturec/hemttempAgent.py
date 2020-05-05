"""
TODO: Make it possible to pass commands from the fridgeController to turn the HEMTs on or off
 - Allow the program to take command line input (or access things from a config file)
 - Start this program with systemd and get it up and running and restartable
 - Work with publish/subscribe to redis for hemt.enabled changes, and write the changes that are made as they are
 made. How do we want to confirm that commands have been successful and the change was made?
 - Redesign functions so that polling for the arduino being connected is always occuring and its only ever possible to
 send a message or receive one if the arduino is connected. Also add a deadtime after arduino is reconnected to allow
 for it to get setup and not immediately start getting pinged.
"""

import serial
import time, logging
from datetime import datetime
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
        self.redis_ts = self.redis.time_series('hemttemp.stream', ['hemt_biases', 'one.wire.temps'])
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
            self.ser.read()
        except SerialException:
            log.warning("Error occurred during connection. Port is not open")
            try:
                log.debug("Opening port")
                self.ser.open()
            except IOError:
                self.ser.close()
                self.ser = None
                log.warning("Error occurred in trying to open port. Check for disconnects")

    def disconnect(self):
        try:
            self.ser.close()
            self.ser = None
        except Exception as e:
            print(e)

    def send(self, msg):
        self.connect()
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

    def receive(self):
        self.connect()
        try:
            confirm = self.ser.readline().decode("utf-8").rstrip("\r\n")
            log.debug(f"read '{confirm}' from arduino")
            return confirm
        except (IOError, SerialException):
            self.disconnect()
            log.error("No port to read from!")
            return None

    def format_value(self, message):
        message = message.split(' ')
        if len(message) == 31:
            log.debug("Formatting HEMT bias values")
            pins = message[0::2]
            biasValues = message[1::2]
            msgtype = 'hemt.biases'
            msg = {k: v for k,v in zip(pins, biasValues)}

        elif len(message) == 25:
            log.debug("Formatting One-wire thermometer values")
            positions = message[0::2][-1]
            temps = message[1::2]
            msgtype = 'one.wire.temps'
            msg = {k: v for k, v in zip(positions, temps)}

        log.debug(f"Formatted message: {msg}")

        return msgtype, msg

    def run(self):
        prevTime = time.time()
        timeOfReconnect = 0
        while True:
            self.connect()
            if (time.time() - prevTime >= self.queryTime) and (time.time() - timeOfReconnect >= self.reconnectTime):
                print(f'{time.time()} querying...')
                log.debug("Sending Query")
                val = self.send("all")
                arduinoReply = self.receive()
                log.info(arduinoReply)
                prevTime = time.time()
                # t, m = self.format_value(arduinoReply)
                # log.debug(f"Sending {t} messages to redis")
                # if t == "hemt.biases":
                #     self.redis_ts.hemt_biases.add(m, id=datetime.utcnow())
                # if t == "one.wire.temps":
                #     self.redis_ts.one_wire_temps.add(m, id=datetime.utcnow())

if __name__ == "__main__":

    hemtduino = Hemtduino(port="/dev/hemtduino", baudrate=115200, timeout=.3)
    hemtduino.run()
