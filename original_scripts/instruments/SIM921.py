
"""
Class to control the Stanford Research Systems (SRS) SIM921 AC Resistance Bridge. Used for reading out device stage
temperature in PICTURE-C. The current thermometer in the fridge is an RX-102A resistor from LakeShore Cryotronics.
"""

import serial
from serial.tools.list_ports import comports
from time import sleep
import numpy as np
import logging
from os.path import expanduser


class SIM921(serial.Serial):
    def __init__(self):
        """
        Initialize SIM921 instrument class. Uses serial.Serial base class for communication through USB port
        """
        # self.setUpLog()
        serial_params = {'baudrate': 9600,
                         'timeout': 2,
                         'parity': serial.PARITY_NONE,
                         'bytesize': serial.EIGHTBITS,
                         'stopbits': serial.STOPBITS_ONE
                         }
        super(SIM921, self).__init__(port=None, **serial_params)
        self.isConnected = False
        self.idn = None
        self.instrument = "SIM921"

    def connect(self):
        """
        Manually open comport to communicate with SIM921. Also initializes SIM921 to proper values to ensure least noisy
        measurements based on parameters from SIM 921 manual.
        :return: None
        """
        self.log.debug(f"Attempting to connect to SIM921")
        valid_port = [(0x0403, 0x6001, "COM5")]

        for port_name in comports():
            if (port_name.vid, port_name.pid) in valid_port:
                self.log.info(f"{port_name.vid}, {port_name.pid}, {port_name.device}")
                self.port = port_name.device
                self.open()
                self.isConnected = True
                self.clearBuffers()
                self.initialize()
                self.idn = self.query("*IDN?")
                self.log.info(f"{self.instrument} connected")
                break
        else:
            self.log.error(f"Cannot find SIM921. Make sure {self.instrument} is connected and powered on.")

    def initialize(self):
        """
        Set all parameters necessary for readout of the device thermometer. Keep the analog output at a low value for
        when R-R_offset is high
        :return: None
        """

        self.log.debug(f"Initializing {self.instrument}")

        # Perform a device reset (CURVE is not altered by this command) and set resistance range and excitation level
        self.command("*RST")  # Reset. Makes sure any changed settings are reset. They will be properly set below
        sleep(.1)
        self.command("RANG 6")  # Set resistance range. (6 = 20 kOhm, comparable to thermometer R @ 100 mK)
        sleep(.1)
        self.command("EXCI 2")  # Set excitation value. (2 = 30 microV) - Range & Excitation chosen to minimize noise
        sleep(.1)

        # Set T and R "offset" values (T=100 mK and R=19.4005 kOhm @ 100 mK from curve)
        self.command("TSET 0.1")  # Temperature value that we will run the PID loop at
        sleep(.1)
        self.command("RSET 19400.5")  # Resistance value of RX-102A @ 100 mK
        sleep(.1)

        # Set analog output scale for both temperature and resistance curves
        self.command("VKEL 1e-2")  # Scale for analog output in V/K - i.e. Output = (1e-2 V/K)*(T-T_offset)
        sleep(.1)
        self.command("VOHM 1e-5")  # Scale for analog output in V/Ohm - Output = (1e-5 V/Ohm) * (R-R_offset)
        sleep(.1)

        # Make sure analog output manual mode is off. Set manual value to 0 V in the case it somehow gets switched on.
        # Set Analog output to resistance, not temperature. Set display to temperature, not resistance.
        self.command("DTEM 1")  # Set the temperature mode to ON
        sleep(.1)
        self.command("ATEM 0")  # Turn Analog Temperature OFF. When OFF analog output is proportional to R.
        sleep(.1)               # When ON analog output is proportional to T.
        self.command("AMAN 0")  # Turn Manual Analog Output Off. When ON, user specifies output voltage. When OFF,
        sleep(.1)               # Analog output voltage is set from the measurement error
        self.command("AOUT 0")  # Set the manual analog output voltage to 0 (For safety just in case, to not send high V to SIM960)
        sleep(.1)

        # Curve should by default be set to curve 1 (PICTURE-C RX-102A calibration), but sets it if it was changed
        if self.query("CURV?") != '1':
            self.command("CURV 1")  # Set the curve to the PICTURE-C RX-102A curve that has already been loaded on

    def disconnect(self):
        """
        Manually disconnect from comport SIM921 is using. Typically only used after monitoring is fully done.
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

    def command(self, command):
        """
        Writes command to SIM921 and encodes it to utf-8 so the device can interpret it
        :param: Command must be a str. Commands for this device can be found in the manual
        :return: None
        """
        if self.isConnected:
            cmd_str = str(command)+"\n"
            self.write(cmd_str.encode('utf-8'))
            self.log.debug(f"Command {cmd_str} sent")
        else:
            self.log.error(f"Cannot write command, {self.instrument} is not connected!")

    def query(self, command):
        """
        Writes command to SIM921 and listens for an answer. Used for specific situations where a response is desired
        :param: Same as command function, str or str-like
        :return: Response to query with end characters stripped
        """
        if self.isConnected:
            self.command(command)
            response = self.readline().decode('ascii').rstrip('\r\n')
            if response:
                self.log.debug(f"Query for \'{command}\' returned")
            else:
                self.log.error(f"No response from {self.instrument}")
            return response
        else:
            self.log.error(f"Query failed! {self.instrument} is not connected!")
            return 0

    def setUpLog(self):
        """
        Set up logging to write data to an instrument log file and to the shell
        :return: None
        """
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        fmt = logging.Formatter('%(asctime)s - %(name)s %(levelname)s - %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
        sh.setFormatter(fmt)
        self.log.addHandler(sh)

    def loadCurve(self, curveNum, curveType, curveName, curveData):
        """
        Load a curve onto the SIM921. For PICTURE-C RX-102A curve the settings are curveNum = 1, curveType = 0 (linear),
        curveName = PIC-C RX-102A. Data is stored in "RX-102A_Mean_Curve.tbl": column 0 is T (in K) and column 1 is R
        (in Ohms).
        :param curveNum: 1,2, or 3
        :param curveType: 0 (Linear), 1 (Semilog T), 2 (Semilog R)
        :param curveName: ID string for curve
        :param curveData: Data for the given sensor. Currently coded to take in PICTURE-C RX-102A data. In principle the
        command for adding a point to the sensor calibration is "CAPT i,f,g" with i = curveNum, f = sensor value,
        g = temperature value. Curve points must be added in increasing f.
        :return: None
        """

        curveLen = len(curveData)
        exist_cmd = "CINI? "+str(curveNum)
        exist_curve = self.query(exist_cmd)
        if curveLen == int(exist_curve.split(',')[2]):
            self.log.info(f"Curve {curveNum} has already been loaded in")
        else:
            self.log.info(f"Loading curve {curveNum} on to {self.instrument}")
            cmd = "CINI "+str(curveNum)+", "+str(curveType)+", "+str(curveName)
            self.command(cmd)

            tData = curveData[:, 0]
            rData = curveData[:, 1]

            if rData[0] > rData[-1]:
                tData = np.flip(tData, axis=0)
                rData = np.flip(rData, axis=0)

            for t, r in zip(tData, rData):
                dataCmd = "CAPT "+str(curveNum)+", "+str(r)+", "+str(t)
                self.command(dataCmd)
                sleep(.2)

            cmd2 = "CINI? "+str(curveNum)
            curveInfo = self.query(cmd2)
            curveLenCheck = int(curveInfo.split(',')[2])

            if curveLenCheck == curveLen:
                self.log.info(f"Curve {curveNum} was loaded successfully onto {self.instrument}")
            else:
                self.log.error(f"Curve {curveNum} was not loaded successfully onto {self.instrument}")
