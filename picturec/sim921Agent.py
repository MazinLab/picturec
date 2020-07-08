"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO: - Create list of allowed commands
 - Create list of redis keys which should be used by the agent for proper operation
 - Decide if mainframe mode is worth using (I think it is for testing)
 - Ensure proper message formatting
 - Rewrite curve loading
"""

import serial
from time import sleep
import numpy as np
from logging import getLogger
from serial import SerialException


class SIM921Agent(object):
    def __init__(self, port, baudrate=9600, timeout=0.1, ):
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
            getLogger(__name__).error(f"Conntecting to port {self.port} failed: {e}")
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

    def send(self, msg:str, connect=True):
        if connect:
            self.connect()
        msg = self._format_message(msg)
        try:
            getLogger(__name__).debug(f"Writing message: {msg}")
            self.ser.write(msg.encode("utf-8"))
            getLogger(__name__).debug(f"Sent {msg} successfully")
        except (SerialException, IOError) as e:
            self.disconnect()
            getLogger(__name__).error(f"Send failed: {e}")
            # raise e

    def receive(self):
        try:
            data = self.ser.readline().decode("utf-8").strip()
            getLogger(__name__).debug(f"read {data} from SIM921")
            return data
        except (IOError, SerialException) as e:
            self.disconnect()
            getLogger(__name__).debug(f"Send failed {e}")
            # raise e

    def _format_message(self, msg:str):
        return msg.strip().upper() + "\n"

    def reset_sim(self):
        self.send("*RST")