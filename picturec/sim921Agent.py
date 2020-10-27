"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO: Engineering functions: Loading curve, primarily.

TODO: Updating settings! Essentially, stay "in contract" with redis.
"""

import numpy as np
import logging
import time
import sys
import picturec.agent as agent
from picturec.pcredis import PCRedis, RedisError
import threading

REDIS_DB = 0
QUERY_INTERVAL = 10

SETTING_KEYS = ['device-settings:sim921:resistance-range',
                'device-settings:sim921:excitation-value',
                'device-settings:sim921:excitation-mode',
                'device-settings:sim921:time-constant',
                'device-settings:sim921:temp-offset',
                'device-settings:sim921:temp-slope',
                'device-settings:sim921:resistance-offset',
                'device-settings:sim921:resistance-slope',
                'device-settings:sim921:curve-number',
                'device-settings:sim921:manual-vout',
                'device-settings:sim921:output-mode']

# TODO: Consider if default keys are even necessary
# A note -> in any case, the only time default keys are necessary are for when settings must be initialized on the
# device that's being controlled
default_key_factory = lambda key: f"default:{key}"
DEFAULT_SETTING_KEYS = [default_key_factory(key) for key in SETTING_KEYS]


TEMP_KEY = 'status:temps:mkidarray:temp'
RES_KEY = 'status:temps:mkidarray:resistance'
OUTPUT_VOLTAGE_KEY = 'status:device:sim921:sim960-vout'


TS_KEYS = [TEMP_KEY, RES_KEY, OUTPUT_VOLTAGE_KEY]


STATUS_KEY = 'status:device:sim921:status'
MODEL_KEY = 'status:device:sim921:model'
FIRMWARE_KEY = 'status:device:sim921:firmware'
SN_KEY = 'status:device:sim921:sn'

DEFAULT_MAINFRAME_KWARGS = {'mf_slot': 2, 'mf_exit_string': 'xyz'}

COMMAND_DICT = {'device-settings:sim921:resistance-range': {'command': 'RANG', 'vals': {20e-3: '0', 200e-3: '1', 2: '2',
                                                                                        20: '3', 200: '4', 2e3: '5',
                                                                                        20e3: '6', 200e3: '7',
                                                                                        2e6: '8', 20e6: '9'}},
                'device-settings:sim921:excitation-value': {'command': 'EXCI', 'vals': {0: '-1', 3e-6: '0', 10e-6: '1',
                                                                                        30e-6: '2', 100e-6: '3',
                                                                                        300e-6: '4', 1e-3: '5',
                                                                                        3e-3: '6', 10e-3: '7', 30e-3: '8'}},
                'device-settings:sim921:excitation-mode': {'command': 'MODE', 'vals': {'passive': '0', 'current': '1',
                                                                                       'voltage': '2', 'power': '3'}},
                'device-settings:sim921:temp-offset': {'command': 'TSET', 'vals': [0.050, 40]},
                'device-settings:sim921:resistance-offset': {'command': 'RSET', 'vals': [1049.08, 63765.1]},
                'device-settings:sim921:temp-slope': {'command': 'VKEL', 'vals': [0, 1e-2]},
                'device-settings:sim921:resistance-slope': {'command': 'VOHM', 'vals': [0, 1e-5]},
                'device-settings:sim921:output-mode': {'command': 'AMAN', 'vals': {'scaled': '1', 'manual': '0'}},
                'device-settings:sim921:manual-vout': {'command': 'AOUT', 'vals': [-10, 10]},
                'device-settings:sim921:curve-number': {'command': 'CURV', 'vals': {1: '1', 2: '2', 3: '3'}},
                }


log = logging.getLogger(__name__)


class SimCommand(object):
    def __init__(self, redis_setting, value):
        self.value = value

        if redis_setting not in COMMAND_DICT.keys():
            raise ValueError('Mapping dict or range tuple required')

        self.setting = redis_setting
        self.command = COMMAND_DICT[self.setting]['command']
        setting_vals = COMMAND_DICT[self.setting]['vals']

        if isinstance(setting_vals, dict):
            self.mapping = setting_vals
            self.range = None
            mapping_type = type(list(self.mapping.keys())[0])
            try:
                if mapping_type == str:
                    self.value = str(self.value)
                elif (mapping_type == float) or (mapping_type == int):
                    self.value = float(self.value)
            except ValueError as e:
                log.warning(f"The value sent was not the correct type! {e}")
        elif isinstance(setting_vals, list):
            self.range = setting_vals
            self.mapping = None
            self.value = float(self.value)

    def valid_value(self):
        if self.range is not None:
            return self.range[0] <= self.value <= self.range[1]
        else:
            return self.value in self.mapping.keys()

    def format_command(self):
        if self.valid_value():
            if self.range is not None:
                return f"{self.command} {self.value}"
            else:
                return f"{self.command} {self.mapping[self.value]}"
        else:
            log.info(f"Trying to set the SIM921 to an invalid value! Setting {self.setting} to {self.value}")


class SIM921Agent(agent.SerialAgent):

    def __init__(self, port, baudrate=9600, timeout=0.1, scale_units='resistance', connect_mainframe=False, **kwargs):
        super().__init__(port, baudrate, timeout, name='sim921')

        self.scale_units = scale_units

        self.connect(raise_errors=False)

        self.kwargs = kwargs

        if connect_mainframe:
            if (int(self.kwargs['mf_slot']) in (np.arange(7)+1)) and self.kwargs['mf_exit_string']:
                self.mainframe_disconnect()
                log.info(f"Connected to {self.idn}, going down the chain to connect to SIM921")
                time.sleep(1)
                self.mainframe_connect()
                time.sleep(1)
                log.info(f"Now connected to {self.idn}")
                # self.mainframe_connect()
                # time.sleep(1)
            else:
                raise IOError(f"Invalid configuration of slot ({self.kwargs['mf_slot']}) "
                              f"and exit string {self.kwargs['mf-exit-string']} for SIM900 mainframe!")

    def reset_sim(self):
        """
        Send a reset command to the SIM device. This should not be used in regular operation, but if the device is not
        working it is a useful command to be able to send.
        BE CAREFUL - This will reset certain parameters which are set for us to read out the thermometer in the
        PICTURE-C cryostat (as of 2020, a LakeShore RX102-A).
        If you do perform a reset, it will then be helpful to restore the 'default settings' which we have determined
        to be the optimal to read out the hardware we have.
        """
        try:
            log.info(f"Resetting the SIM921!")
            self.send("*RST")
        except IOError as e:
            raise e

    def format_msg(self, msg: str):
        return f"{msg.strip().upper()}{self.terminator}"

    @property
    def idn(self):
        """
        Queries the SIM921 for its ID information.
        Raise IOError if serial connection isn't working or if invalid values are received
        ID return string is "<manufacturer>,<model>,<instrument serial>,<firmware versions>"
        Format of return string is "s[25],s[6],s[9],s[6-8]"
        :return: Dict
        """
        try:
            id_msg = self.query("*IDN?")
            manufacturer, model, sn, firmware = id_msg.split(",")  # See manual page 2-20
            firmware = float(firmware[3:])
            return {'manufacturer': manufacturer,
                    'model': model,
                    'sn': sn,
                    'firmware': firmware}
        except IOError as e:
            if 'mf_disconnect_string' in self.kwargs.keys():
                self.mainframe_disconnect()
            log.error(f"Serial error: {e}")
            raise e
        except ValueError as e:
            if 'mf_disconnect_string' in self.kwargs.keys():
                self.mainframe_disconnect()
            log.error(f"Bad firmware format: {firmware}. Error: {e}")
            raise IOError(f"Bad firmware format: {firmware}. Error: {e}")

    def manufacturer_ok(self):
        return self.idn['manufacturer'] == "Stanford_Research_Systems"

    def model_ok(self):
        return self.idn['model'] == "SIM921"

    def mainframe_connect(self, mf_slot=None, mf_exit_string=None):
        if mf_slot and mf_exit_string:
            self.send(f"CONN {mf_slot}, '{mf_exit_string}'")
        elif self.kwargs['mf_slot'] and self.kwargs['mf_exit_string']:
            self.send(f"CONN {self.kwargs['mf_slot']}, '{self.kwargs['mf_exit_string']}'")
        else:
            log.critical("You've messed up a keyword for mainframe connection! Not connecting for your safety")

    def mainframe_disconnect(self, mf_exit_string=None):
        if mf_exit_string:
            self.send(f"{mf_exit_string}\n")
        elif self.kwargs['mf_exit_string']:
            self.send(f"{self.kwargs['mf_exit_string']}\n")

    def read_temp_and_resistance(self):
        temp = self.query("TVAL?")
        res = self.query("RVAL?")

        values = {'temperature': temp, 'resistance':res}

        return values

    def read_output_voltage(self):
        voltage = self.query("AOUT?")

        return voltage

    def monitor_temp(self, interval, value_callback=None):
        def f():
            while True:
                last_monitored_values = None
                try:
                    self.last_monitored_values = self.read_temp_and_resistance()
                    last_monitored_values = self.last_monitored_values
                except IOError as e:
                    log.error(f"Error: {e}")

                if value_callback is not None and last_monitored_values is not None:
                    try:
                        value_callback(self.last_monitored_values)
                    except RedisError as e:
                        log.error(f"Unable to store temperature and resistance due to redis error: {e}")

                time.sleep(interval)

        self._monitor_thread = threading.Thread(target=f, name='Temperature and Resistance Monitoring Thread')
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def monitor_output_voltage(self, interval, value_callback=None):
        def f():
            while True:
                last_voltage = None
                try:
                    self.last_voltage = self.read_output_voltage()
                    last_voltage = self.last_voltage
                except IOError as e:
                    log.error(f"Error: {e}")

                if value_callback is not None and last_voltage is not None:
                    try:
                        value_callback(self.last_voltage)
                    except RedisError as e:
                        log.error(f"Unable to store temperature and resistance due to redis error: {e}")

                time.sleep(interval)

        self._voltage_monitor_thread = threading.Thread(target=f, name='Voltage Monitoring Thread')
        self._voltage_monitor_thread.daemon = True
        self._voltage_monitor_thread.start()

#     def _load_calibration_curve(self, curve_num: int, curve_type, curve_name: str, file:str=None):
#         """
#         This is an engineering function for the SIM921 device. In normal operation of the fridge, the user should never
#         have to load a curve in. This should only ever be used if (1) a new curve becomes available, (2) the
#         thermometer used by the SIM921 is changed out for a new one, or (3) the original curve becomes corrupted.
#         Currently (21 July 2020) designed specifically to read in the LakeShore RX-102-A calibration curve, but can be
#         modified without difficulty to take in other curves. The command syntax will not change for loading the curve
#         onto the SIM921, only the np.loadtxt() and data manipulation of the curve data itself. As long as the curve
#         is in a format where resistance[n] < resistance[n+1] for all points n on the curve, it can be loaded into the
#         SIM921 instrument.
#         """
#         if file is None:
#             #TODO use package resources and a curve to resource name golbal dict to lookup the path do that it works
#             # with pip installation.
#             # e.g. (needs refining)
#             import pkg_resources as pkg
#             CURVE_DICT = {'RX-102A_Mean_Curve':'RX-102A_Mean_Curve.tbl'}
#             path_to_curve = pkg.resource_filename('hardware/thermometry/RX-102A', CURVE_DICT[curve_name])
#         else:
#             path_to_curve = file
#
#         #All three of these things look like globals or things that should be programmatically generated
#         CURVE_NUMBER_KEY = 'device-settings:sim921:curve-number'
#         valid_curves = [1, 2, 3]
#         CURVE_TYPE_DICT = {'linear': '0',
#                            'semilogt': '1',
#                            'semilogr': '2',
#                            'loglog': '3'}
#
#         if curve_num in valid_curves:
#             log.debug(f"Curve {curve_num} is valid and can be initialized.")
#         else:
#             log.warning(f"Curve {curve_num} is NOT valid. Not initializing any curve")
#             return False
#
#         if curve_type in CURVE_TYPE_DICT.keys():
#             log.debug(f"Curve type {curve_type} is valid and can be initialized.")
#         else:
#             log.warning(f"Curve type {curve_type} is NOT valid. Not initializing any curve")
#             return False
#
#         try:
#             curve_init_str = "CINI "+str(curve_num)+", "+str(CURVE_TYPE_DICT[curve_type]+", "+curve_name)
#             self.command(curve_init_str)
#         except IOError as e:
#             raise e
#
#         try:
#             curve_data = np.loadtxt(path_to_curve)
#             temp_data = np.flip(curve_data[:, 0], axis=0)
#             res_data = np.flip(curve_data[:, 1], axis=0)
#         except Exception:
#             raise ValueError(f"{path_to_curve} couldn't be loaded.")
#
#         try:
#             for t, r in zip(temp_data, res_data):
#                 self.command("CAPT"+str(curve_num)+", "+str(r)+", "+str(t))
#                 time.sleep(0.1)
#         except IOError as e:
#             raise e
#
#         try:
#             store_redis_data(self.redis, {CURVE_NUMBER_KEY: curve_num})
#         except RedisError as e:
#             raise e

if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)
    sim921 = SIM921Agent(port='/dev/sim921', baudrate=9600, timeout=0.1, connect_mainframe=True, **DEFAULT_MAINFRAME_KWARGS)

    try:
        sim921_info = sim921.idn
        if not sim921.manufacturer_ok():
            redis.store({STATUS_KEY: f'Unsupported manufacturer: {sim921_info["manufacturer"]}'})
            sys.exit(1)
        if not sim921.model_ok():
            redis.store({STATUS_KEY: f'Unsupported model: {sim921_info["model"]}'})
            sys.exit(1)
        redis.store({FIRMWARE_KEY: sim921_info['firmware'],
                     MODEL_KEY: sim921_info['model'],
                     SN_KEY: sim921_info['firmware']})
    except IOError as e:
        log.error(f"Serial error in querying SIM921 identification information: {e}")
        redis.store({FIRMWARE_KEY: '',
                     MODEL_KEY: '',
                     SN_KEY: ''})
        sys.exit(1)
    except RedisError as e:
        log.critical(f"Redis server error! {e}")
        sys.exit(1)

    # For each loop, update the sim settings if they need to, read and store the thermometry data, read and store the
    # SIM921 output voltage, update the status of the program, and handle any potential errors that may come up.

    store_temp_res_func = lambda x: redis.store({TEMP_KEY: x['temperature'], RES_KEY: x['resistance']}, timeseries=True)
    sim921.monitor_temp(QUERY_INTERVAL, value_callback=store_temp_res_func)

    store_voltage_func = lambda x: redis.store({OUTPUT_VOLTAGE_KEY: x}, timeseries=True)
    sim921.monitor_output_voltage(QUERY_INTERVAL, value_callback=store_voltage_func)

    # TODO: Determine how to properly treat the ATEM (and EXON). Talk with Jeb about scheme for it. For what it's worth
    #  they should always be the same, unless there is a major change in system (new thermometer).
    sim921.send("ATEM 0")
    unit = sim921.query("ATEM?")
    if unit == '0':
        log.critical(f"Unit query response was {0}. Analog output voltage scale units are resistance")
    elif unit == '1':
        log.critical(f"Unit query response was {1}. Analog output voltage scale units are temperature. DO NOT OPERATE"
                     f" IN THIS MODE")
        sys.exit(1)

    sim921.send("EXON 1")
    exon = sim921.query("EXON?")
    if exon == '1':
        log.critical(f"EXON query response was {0}. Excitation is on!")
    elif exon == '0':
        log.critical(f"EXON query response was {1}. Excitation is off,ou won't be able to operate in this mode!")
        sys.exit(1)

    while True:
        try:
            for key, val in redis.listen(SETTING_KEYS):
                log.debug(f"sim921agent received {key}, {val}. Trying to send a command.")
                cmd = SimCommand(key, val)
                if cmd.valid_value():
                    try:
                        log.info(f'Here we would send the command "{cmd.format_command()}\\n"')
                        # sim921.send(f"{cmd.format_command()}")
                        # redis.store({cmd.setting: cmd.value})
                        # redis.store({STATUS_KEY: "OK"})
                    except IOError as e:
                        redis.store({STATUS_KEY: f"Error {e}"})
                        log.error(f"Some error communicating with the SIM921! {e}")
                else:
                    log.warning(f'Not a valid value. Can\'t send key:value pair "{key} / {val}" to the SIM921!')
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)