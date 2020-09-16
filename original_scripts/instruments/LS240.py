"""
Class to control LakeShore 240-2p temperature monitor. MeasureLink software was used to program the calibration curves
for the PICTURE-C tank thermometers.
TODO: *Possibly* add functionality to add in curves directly from this script, a loadcurve function similar to SIM921
"""

import serial
from serial.tools.list_ports import comports
from time import sleep
import numpy as np
import logging
from custom_logging import MyTimedRotatingFileHandler as trfHandler
from os.path import expanduser


class LS240(serial.Serial):
    def __init__(self):
        """
        Initialize LS240 instrument class. Uses serial.Serial base class for communication through USB port
        """
        self.setUpLog()
        self.serial_params = {'baudrate': 115200,
                         'timeout': 2,
                         'parity': serial.PARITY_NONE,
                         'bytesize': serial.EIGHTBITS}

        super(LS240, self).__init__(port=None, **self.serial_params)
        self.isConnected = False
        self.idn = None
        self.instrument = "LS240"

    def connect(self):
        """
        Manually open comport for communication with LS240.
        :return: None
        """
        self.log.debug(f"Attempting to connect to {self.instrument}")
        valid_id_combos = [(0x1FB9, 0x0205)]  # 240 Module

        for port_name in comports():
            print(port_name.vid, port_name.pid)
            if (port_name.vid, port_name.pid) in valid_id_combos:
                self.port = port_name.device
                self.open()
                self.isConnected = True
                self.clearBuffers()
                self.idn = self.query("*IDN?")
                self.log.info(f"{self.instrument} connected")
                break
        else:
            self.log.error(f"Cannot find {self.instrument}. Look at device manager and make sure the COM port is there."
                           f" Also make sure that MeasureLINK for the 240 Series is not currently connected to the "
                           f"240 module.")

    def disconnect(self):
        """
        Manually disconnect from comport LS240 is using.
        :return: None
        """
        self.close()
        self.log.debug(f"{self.instrument} disconnected")

    def clearBuffers(self):
        """
        Clears data buffers to prevent messages that were sent/received that weren't processed properly from messing up
        the data stream
        :return: None
        """
        self.reset_input_buffer()
        self.reset_output_buffer()
        self.log.debug("Buffers cleared")
        sleep(.1)

    def command(self, command_string):
        """
        Writes command to SIM921 and encodes it to utf-8 so the device can interpret it
        :param: Command must be a str. Commands for this device can be found in the manual
        :return: None
        """
        if self.isConnected:
            cmd_str = str(command_string)+"\n"
            self.write(cmd_str.encode('utf-8'))
            self.log.debug(f"Command \'{cmd_str}\' sent")
        else:
            self.log.error(f"Cannot write command! {self.instrument} is not connected!")

    def query(self, query_string):
        """
        Writes command to SIM921 and listens for an answer. Used for specific situations where a response is desired
        :param: Same as command function, str or str-like
        :return: Response to query
        """
        if self.isConnected:
            self.command(query_string)
            response = self.readline().decode('ascii').rstrip('\r\n')
            if response:
                self.log.debug(f"Query for \'{query_string}\' returned")
            else:
                self.log.error(f"No response from {self.instrument}")
            return response
        else:
            self.log.error(f"Query failed! {self.instrument} is not connected!")
            return 0

    def identify_model(self):
        """
        Identifies the model of the LS240.
        :return: 2 or 8, depending on which number of channels one has.
        """
        if self.isConnected:
            return int(self.query("*IDN?")[14])
        else:
            return 2

    def enabled_channels(self):
        """
        Determines which of the 2 or 8 channels are enabled and have curves loaded in.
        :return: List of channels (indexed from 1 to n, n = 2 or 8) with curves loaded
        """
        channels = self.identify_model()
        enabled = []

        if self.isConnected:
            for channel in range(1, channels + 1):
                if self.query("INTYPE? " + str(channel))[10] == '1':
                    enabled.append(int(channel))
        else:
            enabled = [1, 2]

        return enabled

    def query_temperatures_all(self):
        """
        Returns a temperature reading for each of the enabled channels
        :return: Array of length of enabled channels, each element is the temperature reading from a given channel
        """
        query_channels = self.enabled_channels()

        readings = []

        for channel in query_channels:
            readings.append(float(self.query("KRDG? " + str(channel))))

        return np.array(readings)

    def setUpLog(self):
        """
        Set up logging to write data to an instrument log file and to the shell
        :return: None
        """
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        fh = trfHandler("instruments.log", whenTo='m', intervals=1, directory=expanduser("~"))
        filefmt = logging.Formatter('%(asctime)s - %(name)s %(levelname)s - %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
        fmt = logging.Formatter('%(asctime)s - %(name)s %(levelname)s - %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
        fh.setFormatter(filefmt)
        sh.setFormatter(fmt)
        self.log.addHandler(sh)
        self.log.addHandler(fh)
