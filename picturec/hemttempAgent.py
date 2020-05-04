"""
TODO: Un-hardcode commands from testing
TODO: Make it possible to pass commands from the fridgeController to turn the HEMTs on or off
TODO: Decide whether we want polling to be mindless and just done on an interval (preferable) or if we want it to also
 support a 'refresh' functionality.
TODO: Program in IOError and SerialError handling to account for unplugging/bad data/etc.
TODO: Allow the program to take command line input (or access things from a config file)
TODO: Start this program with systemd and get it up and running and restartable
TODO: Work with publish/subscribe to redis for hemt.enabled changes, and write the changes that are made as they are
 made. How do we want to confirm that commands have been successful and the change was made?
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
    def __init__(self, port, baudrate=115200, timeout=None, queryTime=1):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.queryTime = queryTime
        self.message_sent = False
        self.redis = walrus.Walrus(host='localhost', port=6379, db=REDIS_DB)
        self.redis_ts = self.redis.time_series('hemttemp.stream', ['hemt_biases', 'one.wire.temps'])
        self.setupSerial(port=port, baudrate=baudrate, timeout=timeout)

    def setupSerial(self, port, baudrate=115200, timeout=1):
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
            assert self.ser.isOpen()
            return "o"
        except AssertionError:
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
        self.message_sent = False
        connected = self.connect()
        if connected is "o":
            cmdWMarkers = START_MARKER
            cmdWMarkers += msg
            cmdWMarkers += END_MARKER
            try:
                log.debug(f"Writing message: {cmdWMarkers}")
                self.ser.write(cmdWMarkers.encode("utf-8"))
                time.sleep(.3)
                self.message_sent = True
            except (IOError, SerialException) as e:
                self.disconnect()
        else:
            log.warning("Trying to write to an unopened port!")


    def receive(self):
        connect = self.connect()
        self.message_received = False
        if connect == "o":
            try:
                confirm = self.ser.readline().decode("utf-8").rstrip("\r\n")
                self.message_received = True if confirm is not ('' or None) else False
                print(self.message_received)
                return confirm
            except (IOError, SerialException):
                self.disconnect()
                log.error("No port to read from!")
        else:
            return None

    def arduino_ping(self):
        log.debug("Waiting for Arduino")
        self.send("ping")

        msg = self.receive()
        log.debug(msg)

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
        self.arduino_ping()
        prevTime = time.time()

        while True:
            if time.time() - prevTime >= self.queryTime:
                log.debug("Sending Query")
                self.send("all")
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
