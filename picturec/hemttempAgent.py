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
import walrus

START_MARKER = '<'
END_MARKER = '>'
REDIS_DB = 0

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class Hemtduino(serial.Serial):
    def __init__(self, port, baudrate, timeout=None, queryTime=1):
        super(Hemtduino, self).__init__(baudrate=baudrate, timeout=timeout)
        self.port = port
        self.queryTime = queryTime
        try:
            self.open()
            log.debug(f"port{self.port} connection established")
            self.close()
        except (serial.SerialException, IOError):
            log.error(f"port {self.port} unavailable")

        self.redis = walrus.Walrus(host='localhost', port=6379, db=REDIS_DB)
        self.redis_ts = self.redis.time_series('hemttemp.stream', ['hemt_biases', 'one.wire.temps'])

    def _reset(self):
        self.setDTR(False)
        time.sleep(0.5)
        self.setDTR(True)

    def _arduino_receive(self, dodisconnect=True):
        dataStarted = False
        messageComplete = False
        dataBuffer = ""

        while True:
            if self.in_waiting > 0 and not messageComplete:
                x = self.read().decode("utf-8")
                log.debug(x)

                if dataStarted:
                    if x is not END_MARKER:
                        dataBuffer += x
                    else:
                        dataStarted = False
                        messageComplete = True
                elif x == START_MARKER:
                    dataStarted = True
                    dataBuffer = ""

            elif messageComplete:
                messageComplete = False
                break
            else:
                dataBuffer = "XXX"
                break

        if dodisconnect:
            self.close()
        return dataBuffer

    def arduino_ping(self):
        log.debug("Waiting for Arduino")
        self._arduino_send("ping")

        msg = self._arduino_receive()
        log.debug(msg)

    def _arduino_send(self, command, doconnect=True):
        if doconnect:
            try:
                self.open()
                time.sleep(1)
                cmdWMarkers = START_MARKER
                cmdWMarkers += command
                cmdWMarkers += END_MARKER

                log.debug("Writing...")
                self.write(cmdWMarkers.encode("utf-8"))
                time.sleep(0.5)
            except (serial.SerialException, IOError):
                log.error("Port is inaccessible!")

    def format_value(self, message):
        message = message.split(' ')
        if len(message) == 31:
            log.debug("Formatting HEMT bias values")
            pins = message[0::2][-1]
            biasValues = message[1::2]
            msgtype = 'hemt.biases'
            msg = {k: v for k,v in zip(pins, biasValues)}

        elif len(message) == 25:
            log.debug("Formatting One-wire thermometer values")
            positions = message[0::2][-1]
            temps = message[1::2]
            msgtype = 'one.wire.temps'
            msg = {k: v for k, v in zip(positions, temps)}

        return msgtype, msg

    def run(self):
        self.arduino_ping()
        prevTime = time.time()

        while True:
            if time.time() - prevTime >= self.queryTime:
                log.debug("Sending Query")
                self._arduino_send("all")
                arduinoReply = self._arduino_receive()
                log.info(arduinoReply)
                prevTime = time.time()
                t, m = self.format_value(arduinoReply)
                log.debug(f"Sending {t} messages to redis")
                if t == "hemt.biases":
                    self.redis_ts.hemt_biases.add(m, id=datetime.utcnow())
                if t == "one.wire.temps":
                    self.redis_ts.one_wire_temps.add(m, id=datetime.utcnow())


if __name__ == "__main__":

    hemtduino = Hemtduino(port="/dev/ttyS9", baudrate=9600, timeout=1)
    hemtduino.run()
