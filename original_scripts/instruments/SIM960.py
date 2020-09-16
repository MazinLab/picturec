"""
Class to control the Stanford Research Systems (SRS) SIM960 Analog PID controller.
This will be used to control the magnet current (via the HCBoost Board) in PICTURE-C.
TODO: Add in functionality to interface with SIM921 module.
TODO: Figure out setpoint
TODO: Add all functionality
"""

import serial
from serial.tools.list_ports import comports
import time
from time import sleep
import logging
from custom_logging import MyTimedRotatingFileHandler as trfHandler
from os.path import expanduser


class SIM960(serial.Serial):
    def __init__(self):
        """
        Initialize SIM921 instrument class. Uses serial.Serial base class for communication through USB port
        """
        self.setUpLog()
        serial_params = {'baudrate': 9600,
                         'timeout': 2,
                         'parity': serial.PARITY_NONE,
                         'bytesize': serial.EIGHTBITS,
                         'stopbits': serial.STOPBITS_ONE
                         }
        super(SIM960, self).__init__(port=None, **serial_params)
        self.isConnected = False
        self.idn = None
        self.instrument = "SIM960"

    def connect(self):
        """
        Manually open comport to communicate with SIM960. Initializes SIM960 for optimal values for PICTURE-C
        configuration.
        :return: None
        TODO: Determine the valid_port of the SIM960 when connecting.
        """
        self.log.debug(f"Attempting to connect to SIM960")
        valid_port = [(0x0403, 0x6001, "COM6")]

        for port_name in comports():
            if (port_name.vid, port_name.pid) in valid_port:
                self.port = port_name.device
                self.open()
                self.isConnected = True
                self.clearBuffers()
                self.initialize()
                self.idn = self.query("*IDN?")
                self.log.info(f"{self.instrument} connected")
                break
        else:
            self.log.error(f"Cannot find {self.instrument}. Maker sure {self.instrument} is connected and powered on.")

    def initialize(self):
        """
        Perform a reset for the SIM960 module. Then initialize values necessary for proper operation
        TODO: Find P, I, (maybe) D values and initialize them here.
        TODO: Start in manual output voltage mode at V_out = 0 V.
        TODO: OVERALL: FIGURE OUT HOW TO INITIALIZE SIM960
        :return: None
        """
        self.command("*RST")
        self.command("AMAN 0")
        self.command("MOUT 0")
        self.command("FLOW 0")
        self.command("LLIM 0")
        self.command("INPT 0")
        # self.command("SETP NUMBER_VALUE")  # DETERMINE SETPOINT!

        self.command("PCTL 1")  # Turn proportional control on
        self.command("ICTL 1")  # Turn integral control on
        self.command("DCTL 0")  # Turn derivative control off

        # self.command("GAIN NUMBER_VALUE")  # DETERMINE P VALUE (1.6e1 from ADR manual)
        # self.command("APOL (0 or 1)")  # DETERMINE DESIRED POLARITY BASED ON CONTROLLING VIA R, NOT T
        # self.command("INTG NUMBER_VALUE")  # DETERMINE I VALUE (0.2e0 from ADR manual)
        # self.command("DERV 0")  # Set D value to 0

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
        Writes command to SIM960 and encodes it to utf-8 so the device can interpret it
        :param: Command must be a str. Commands for this device can be found in the manual
        :return: None
        """
        if self.isConnected:
            cmd_str = str(command)+"\n"
            self.write(cmd_str.encode('utf-8'))
            self.log.debug(f"Command \'{cmd_str}\' sent")
        else:
            self.log.error(f"Cannot write command! {self.instrument} is not connected!")

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
        sh.setLevel(logging.INFO)
        fh = trfHandler("instruments.log", whenTo='m', intervals=1, directory=expanduser("~"))
        fh.setLevel(logging.DEBUG)
        filefmt = logging.Formatter('%(asctime)s - %(name)s %(levelname)s - %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
        fmt = logging.Formatter('%(asctime)s - %(name)s %(levelname)s - %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
        fh.setFormatter(filefmt)
        sh.setFormatter(fmt)
        self.log.addHandler(sh)
        self.log.addHandler(fh)

    def ramp(self, rate=5, soakTime=20):
        """
        Function to perform the ramp up, soak, and ramp down only.
        :param rate: Max rate for ADR ramp in mA/s (at higher voltages the current per applied voltage goes down).
                     Default is 5 mA/s
        :param soakTime: Time for magnet soak in minutes, default is 20
        """
        if rate > 5:
            rate = 5
        if rate < 0:  # not honestly sure on this one, do we want instead to
            rate = 1

        if self.query("AMAN?") != "0":
            self.command("AMAN 0")

        rate = rate / 1000  # convert rate in mA/s to A/s
        controlVoltage = 0
        counter = 0

        self.log.info(f"Starting ramp ({rate} A/s)")
        rampUpStart = time.time()
        while controlVoltage < 9.5:
            controlVoltage = rate * counter
            self.command("MOUT " + str(controlVoltage))
            counter += 1
            sleep(1)

        rampUpEnd = time.time()
        self.log.info(f"Ramp up took {rampUpEnd - rampUpStart} seconds")
        soakStart = time.time()

        self.log.info(f"Starting {soakTime} minute soak")
        sleep(soakTime * 60)  # convert soak to seconds for the sleep function
        soakEnd = time.time()
        self.log.info(f"Soak ended. Took {(soakEnd - soakStart) / 60} minutes")

        rampDownStart = time.time()
        self.log.info(f"Ramping down")

        while controlVoltage > 0:
            controlVoltage = rate * counter
            self.command("MOUT " + str(controlVoltage))
            counter -= 1
            sleep(1)

        rampDownEnd = time.time()
        self.log.info(f"Ramp down took {rampDownEnd - rampDownStart} seconds")
        self.log.info(f"Full ramp took {rampDownEnd - rampUpStart} seconds")

    def run_pid(self):
        """
        Switch from manual output mode to PID control mode. Ensure bumpless transfer (see manual) and start
        regulating.
        """

        if self.query("AMAN?") != "0":
            self.command("AMAN 0")

