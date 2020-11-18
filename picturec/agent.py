"""
Author: Jeb Bailey

"""
from logging import getLogger
import serial
import threading
import time


def escapeString(string):
    """
    Takes a string and escapes newline characters so they can be logged and display the newline characters in that string
    """
    return string.replace('\n', '\\n').replace('\r', '\\r')


class SerialDevice:
    def __init__(self, port, baudrate=115200, timeout=0.1, name=None, terminator='\n'):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.name = name if name else self.port
        self.terminator = terminator
        self._rlock = threading.RLock()

    def _preconnect(self):
        """
        Override to perform an action immediately prior to connection.
        Function should raise IOError if the serial device should not be opened.
        """
        pass

    def _postconnect(self):
        """
        Override to perform an action immediately after connection. Default is to sleep for twice the timeout
        Function should raise IOError if there are issues with the connection.
        Function will not be called if a connection can not be established or already exists.
        """
        time.sleep(2*self.timeout)

    def _predisconnect(self):
        """
        Override to perform an action immediately prior to disconnection.
        Function should raise IOError if the serial device should not be opened.
        """
        pass

    def connect(self, reconnect=False, raise_errors=True):
        """
        Connect to a serial port. If reconnect is True, closes the port first and then tries to reopen it. First asks
        the port if it is already open. If so, returns nothing and allows the calling function to continue on. If port
        is not already open, first attempts to create a serial.Serial object and establish the connection.
        Raises an IOError if the serial connection is unable to be established.
        """
        if reconnect:
            self.disconnect()

        try:
            if self.ser.isOpen():
                return
        except Exception:
            pass

        getLogger(__name__).debug(f"Connecting to {self.port} at {self.baudrate}")
        try:
            self._preconnect()
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
            self._postconnect()
            getLogger(__name__).info(f"port {self.port} connection established")
            return True
        except (serial.SerialException, IOError) as e:
            self.ser = None
            getLogger(__name__).error(f"Conntecting to port {self.port} failed: {e}")
            if raise_errors:
                raise e
            return False

    def disconnect(self):
        """
        First closes the existing serial connection and then sets the ser attribute to None. If an exception occurs in
        closing the port, log the error but do not raise.
        """
        try:
            self._predisconnect()
            self.ser.close()
            self.ser = None
        except Exception as e:
            getLogger(__name__).info(f"Exception during disconnect: {e}")

    def format_msg(self, msg:str):
        """Subclass may implement to apply hardware specific formatting"""
        return f"{msg}{self.terminator}"

    def send(self, msg: str, connect=True):
        """
        Send a message to a serial port. If connect is True, try to connect to the serial port before sending the
        message. Formats message according to the class's format_msg function before attempting to write to serial port.
        If IOError or SerialException occurs, first disconnect from the serial port, then log and raise the error.
        """
        with self._rlock:
            if connect:
                self.connect()

            msg = self.format_msg(msg)

            try:
                getLogger(__name__).debug(f"Sending '{escapeString(msg)}'")  # Note: '' allows clear logging of empty sends
                self.ser.write(msg.encode("utf-8"))
            except (serial.SerialException, IOError) as e:
                self.disconnect()
                getLogger(__name__).error(f"Send failed: {e}")
                raise e

    def receive(self):
        """
        Receives a message from a serial port. Assumes that the message consists of a single line. If a message is
        received, decode it and strip it of any newline characters. In the case of an error or serialException,
        disconnects from the serial port and raises an IOError.
        """
        with self._rlock:
            try:
                data = self.ser.readline().decode("utf-8").strip()
                getLogger(__name__).debug(f"read {escapeString(data)} from {self.name}")
                return data
            except (IOError, serial.SerialException) as e:
                self.disconnect()
                getLogger(__name__).debug(f"Send failed {e}")
                raise IOError(e)

    def query(self, cmd: str, **kwargs):
        """
        Send command and wait for a response, kwargs passed to send, raises only IOError
        """
        with self._rlock:
            try:
                self.send(cmd, **kwargs)
                return self.receive()
            except Exception as e:
                raise IOError(e)
