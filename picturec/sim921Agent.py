"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO: - Create list of allowed commands
 - Decide if mainframe mode is worth using (I think it is for testing)
 - Associate keys with commands
"""

import serial
import numpy as np
from logging import getLogger
from serial import SerialException
import time

KEYS = ['device-settings:sim921:resistance-range',
        'device-settings:sim921:excitation-value',
        'device-settings:sim921:excitation-mode',
        'device-settings:sim921:time-constant',
        'device-settings:sim921:temp-offset',
        'device-settings:sim921:temp-slope',
        'device-settings:sim921:resistance-offset',
        'device-settings:sim921:resistance-slope',
        'device-settings:sim921:curve-profile',
        'device-settings:sim921:manual-vout',
        'device-settings:sim921:output-mode',
        'status:device:sim921:firmware',
        'status:device:sim921:status',
        'status:device:sim921:model',
        'status:device:sim921:sn',
        'status:device:sim921:sim960-vout']  # sim960-vout means the vout from sim921 to the sim960

STATUS_KEY = 'status:device:sim921:status'
FIRMWARE_KEY = 'status:device:sim921:firmware'
SERIALNO_KEY = 'status:device:sim921:sn'


class SIM921Agent(object):
    def __init__(self, port, redis, redis_ts, baudrate=9600, timeout=0.1, initialize=True, mainframe=False):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)
        time.sleep(1)
        self.redis = redis
        self.redis_ts = redis_ts

        if initialize:
            self.initialize_SIM921()

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
        msg = msg.strip().upper() + "\n"
        try:
            getLogger(__name__).debug(f"Writing message: {msg}")
            self.ser.write(msg.encode("utf-8"))
            getLogger(__name__).debug(f"Sent {msg} successfully")
        except (SerialException, IOError) as e:
            self.disconnect()
            getLogger(__name__).error(f"Send failed: {e}")
            raise e

    def receive(self):
        try:
            data = self.ser.readline().decode("utf-8").strip()
            getLogger(__name__).debug(f"read {data} from SIM921")
            return data
        except (IOError, SerialException) as e:
            self.disconnect()
            getLogger(__name__).debug(f"Send failed {e}")
            raise e

    def reset_sim(self):
        try:
            self.send("*RST")
        except IOError as e:
            raise e

    def command(self, command_msg: str):
        try:
            getLogger(__name__).debug(f"Sending command '{command_msg}' to SIM921")
            self.send(command_msg)
        except IOError as e:
            raise e

    def query(self, query_msg: str):
        try:
            getLogger(__name__).debug(f"Querying '{query_msg}' from SIM921")
            self.send(query_msg)
            response = self.receive()
        except Exception as e:
            raise IOError(e)
        return response

    def set_sim_value(self, setting: str, value: str):
        """
        Setting param must be one of the valid setting commands. Value must be a legal value to send to the SIM921 as
        laid out in its manual, pages 2-9 to 2-15 (picturec/hardware/thermometry/SRS-SIM921-ResistanceBridge-Manual.pdf)

        For example, to set the resistance range to 20 kOhm: setting='RANG', value='6'
        """
        set_string = setting + " " + value
        try:
            self.command(set_string)
        except IOError as e:
            raise e

    def set_resistance_range(self, value):
        """
        Command the SIM921 to go to a new resistance range.
        RANGE_DICT has the desired values as keys and command codes as values
        """
        RANGE_DICT = {20e-3: '0', 200e-3: '1', 2: '2', 20: '3', 200: '4',
                      2e3: '5', 20e3: '6', 200e3: '7', 2e6: '8', 20e6: '9'}

        if value in RANGE_DICT.keys():
            getLogger(__name__).debug(f"{value} Ohms is a valid value. Setting SIM921 resistance range to {value} Ohms")
            try:
                self.set_sim_value("RANG", RANGE_DICT[value])
                getLogger(__name__).info(f"Resistance range successfully set to {value} Ohms.")
            except IOError as e:
                raise e
        else:
            getLogger(__name__).warning(f"{value} Ohms is not a valid value for SIM921 resistance range.")

    def set_excitation_value(self, value):
        """
        Command the SIM921 to go to a new excitation value.
        EXCITATION_DICT has the desired values as keys and command codes as values
        NOTE: A value of 0 (code -1) means that the excitation is off.
        """
        EXCITATION_DICT = {0: '-1', 3e-6: '0', 10e-6: '1', 30e-6: '2', 100e-6: '3',
                           300e-6: '4', 1e-3: '5', 3e-3: '6', 10e-3: '7', 30e-3: '8'}

        if value in EXCITATION_DICT.keys():
            getLogger(__name__).debug(f"{value} V is a valid value. Setting SIM921 excitation value to {value} V")
            try:
                self.set_sim_value("EXON", "1")
                self.set_sim_value("EXCI", EXCITATION_DICT[value])
                getLogger(__name__).info(f"Excitation successfully set to {value} V.")
            except IOError as e:
                raise e
        else:
            getLogger(__name__).warning(f"{value} V is not a valid value for SIM921 excitation value.")

    def set_excitation_mode(self, mode='voltage'):
        EXCITATION_MODES = {'passive': '0',
                            'current': '1',
                            'voltage': '2',
                            'power': '3'}
        mode = mode.lower()
        if mode in EXCITATION_MODES.keys():
            getLogger(__name__).debug(f"'{mode}' is a valid mode. Setting SIM921 excitation mode to {mode}")
            try:
                self.set_sim_value("MODE", mode)
            except IOError as e:
                raise IOError
        else:
            getLogger(__name__).warning(f"'{mode}' is not a valid excitation mode on the SIM921.")


    def turn_excitation_off(self):
        try:
            getLogger(__name__).info(f"Turning excitation off")
            self.set_sim_value("EXON", "0")
        except IOError as e:
            raise e

    def set_temperature_offset(self, value):
        t_min = 0.005
        t_max = 40

        if value < t_min:
            getLogger(__name__).info(f"{value} K is too low for an offset value. Setting offset T to {t_min} K.")
            value = t_min
        elif value > t_max:
            getLogger(__name__).info(f"{value} K is too high for an offset value. Setting offset T to {t_max} K.")
            value = t_max

        try:
            getLogger(__name__).info(f"Setting offset temperature to {value} K.")
            self.set_sim_value("TSET", str(value))
        except IOError as e:
            raise e

    def set_resistance_offset(self, value):
        r_min = 1049.08
        r_max = 63765.1

        if value < r_min:
            getLogger(__name__).info(f"{value} Ohms is too low for an offset value. Setting offset R to {r_min} Ohms.")
            value = r_min
        elif value > r_max:
            getLogger(__name__).info(f"{value} Ohms is too high for an offset value. Setting offset R to {r_max} Ohms.")
            value = r_max

        try:
            getLogger(__name__).info(f"Setting offset resistance to {value} Ohms.")
            self.set_sim_value("RSET", str(value))
        except IOError as e:
            raise e

    def set_analog_output_scale(self, mode, value):
        a_min = 0
        if value < a_min:
            value = a_min

        if mode == 'temperature':
            a_max = 1e-2  # V/K
            a = value if (value < a_max) else a_max
            try:
                getLogger(__name__).info(f"Setting analog output scale for temperature to {a} V/K.")
                self.set_sim_value("VKEL", str(a))
            except IOError as e:
                raise e
        elif mode == 'resistance':
            a_max = 1e-5  # V/Ohm
            a = value if (value < a_max) else a_max
            try:
                getLogger(__name__).info(f"Setting analog output scale for resistance to {a} V/Ohm.")
                self.set_sim_value("VOHM", str(a))
            except IOError as e:
                raise e
        else:
            getLogger(__name__).warning(f"'{mode}' is not a valid analog output scale mode. "
                                        f"Valid options are temperature or resistance.")

    def turn_manual_output_on(self):
        try:
            getLogger(__name__).info("Turning analog output mode to manual.")
            self.set_sim_value("AMAN", "1")
        except IOError as e:
            raise e

    def turn_scaled_output_on(self):
        try:
            getLogger(__name__).info("Turning analog output mode to scaled.")
            self.set_sim_value("AMAN", "0")
        except IOError as e:
            raise e

    def set_analog_output_manual_voltage(self, value):
        v_min = -10
        v_max = 10
        if value > v_min:
            getLogger(__name__).warning(f"SIM921 can't output voltage below {v_min} V!")
            value = v_min
        elif value < v_max:
            getLogger(__name__).warning(f"SIM921 can't output voltage above {v_min} V!")
            value = v_max

        try:
            getLogger(__name__).info(f"Setting manual output voltage to {value} V.")
            self.set_sim_value("AOUT", str(value))
        except IOError as e:
            raise e

    def set_analog_output_scale_units(self, units):
        UNITS_DICT = {'temperature': '1',
                      'resistance': '0'}
        if units in UNITS_DICT.keys():
            try:
                getLogger(__name__).info(f"Setting scaled output to use {units} units.")
                self.set_sim_value("ATEM", UNITS_DICT[units])
            except IOError as e:
                raise e
        else:
            getLogger(__name__).warning(f"Invalid unit! Cannot set analog output scale to {units} units!")

    def choose_calibration_curve(self, curve_number):
        valid_curves = [1, 2, 3]
        if curve_number in valid_curves:
            try:
                getLogger(__name__).info(f"Setting the SIM921 to use calibration curve {curve_number}."
                                         f"For more information on curve use 'CINI? <curve_number>'.")
                self.set_sim_value("CURV", str(curve_number))
            except IOError as e:
                raise e
        else:
            getLogger(__name__).warning(f"{curve_number} is not a valid curve number for the SIM921!")

    def load_calibration_curve(self, curve_num, curve_type, path_to_curve="../hardware/thermometry/RX-102A/RX-102A_Mean_Curve.tbl"):
        valid_curves = [1, 2, 3]
        CURVE_TYPE_DICT = {'linear': '0',
                           'semilogt': '1',
                           'semilogr': '2',
                           'loglog': '3'}

        try:
            curve_data = np.loadtxt(path_to_curve)
            temp_data = np.flip(curve_data[:, 0], axis=0)
            res_data = np.flip(curve_data[:, 1], axis=0)
        except Exception:
            raise ValueError(f"{path_to_curve} couldn't be loaded.")

        try:
            for t, r in zip(temp_data, res_data):
                self.command("CAPT"+str(curve_num)+", "+str(r)+", "+str(t))
                time.sleep(0.2)
        except IOError as e:
            raise e


    def initialize_SIM921(self):
        getLogger(__name__).info(f"Initializing SIM921")
        try:
            self.reset_sim()

            self.set_resistance_range(20e3)
            self.set_excitation_value(100e-6)
            self.set_excitation_mode('voltage')

            self.set_temperature_offset(0.100)
            self.set_analog_output_scale('temperature', 1e-2)

            self.set_resistance_offset(19400.5)
            self.set_analog_output_scale('resistance', 1e-5)

            self.set_analog_output_manual_voltage(0)
            self.turn_manual_output_on()
            self.set_analog_output_scale_units('resistance')

            self.load_calibration_curve(1, 'linear', '../hardware/thermometry/RX-102A/RX-102A_Mean_Curve.tbl')
            self.choose_calibration_curve(1)

            self.command("DTEM 1")  # This value is... probably not necessary. This is just if the screen of the SIM921
            # shows temperature (1) or resistance (0). For convenience, we set it to 1 here during testing.

        except IOError as e:
            getLogger(__name__).debug(f"Initialization failed: {e}")
            raise e
