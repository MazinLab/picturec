"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO: Do we want a 'confirm' ability with the command to make sure it sent?
 - I think no, because the only case I have seen infidelity with commands is if they're invalid (taken care of already)
 or there is a physical disconnect (IOError from serial port).

TODO: Add value caching? (self.output_mode = 'manual', self.curve_number = 1)

TODO JB: Much of the 960 and 921 code has overlap. I'd suggest (after agent becomes SerialDevice)

SimDevice(SerialDevice)
Sim960(SimDevice)
Sim921(SimDevice)

"""

import numpy as np
import logging
import time
import sys
import picturec.agent as agent
from picturec.pcredis import PCRedis, RedisError
import threading
import os
import picturec.util as util

DEVICE = '/dev/sim921'
REDIS_DB = 0
QUERY_INTERVAL = 1

SETTING_KEYS = ['device-settings:sim921:output-mode',
                'device-settings:sim921:manual-vout',
                'device-settings:sim921:curve-number',
                'device-settings:sim921:resistance-slope',
                'device-settings:sim921:resistance-range',
                'device-settings:sim921:resistance-offset',
                'device-settings:sim921:temp-slope',
                'device-settings:sim921:temp-offset',
                'device-settings:sim921:excitation-value',
                'device-settings:sim921:excitation-mode',
                'device-settings:sim921:time-constant']

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
                'device-settings:sim921:time-constant': {'command': 'TCON', 'vals': {0.3: '0', 1: '1', 3: '2', 10: '3',
                                                                                     30: '4', 100: '5', 300: '6'}},
                'device-settings:sim921:temp-offset': {'command': 'TSET', 'vals': [0.050, 40]},
                'device-settings:sim921:resistance-offset': {'command': 'RSET', 'vals': [1049.08, 63765.1]},
                'device-settings:sim921:temp-slope': {'command': 'VKEL', 'vals': [0, 1e-2]},
                'device-settings:sim921:resistance-slope': {'command': 'VOHM', 'vals': [0, 1e-5]},
                'device-settings:sim921:output-mode': {'command': 'AMAN', 'vals': {'scaled': '0', 'manual': '1'}},
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

    def __init__(self, port, baudrate=9600, timeout=0.1, scale_units='resistance', connect=True,
                 connect_mainframe=False, **kwargs):
        super().__init__(port, baudrate, timeout, name='sim921')

        if connect:
            self.connect(raise_errors=False)

        self.scale_units = scale_units
        self.kwargs = kwargs
        self.last_voltage = None
        self.last_monitored_values = None
        self._monitor_thread = None
        self.last_voltage_read = None
        self.last_temp_read = None
        self.last_resistance_read = None

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
            #TODO JB: same comments about the 960 and disconnects
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
        #TODO
        if mf_slot and mf_exit_string:
            self.send(f"CONN {mf_slot}, '{mf_exit_string}'")
        elif self.kwargs['mf_slot'] and self.kwargs['mf_exit_string']:
            self.send(f"CONN {self.kwargs['mf_slot']},'{self.kwargs['mf_exit_string']}'")
        else:
            log.critical("You've messed up a keyword for mainframe connection! Not connecting for your safety")

    def mainframe_disconnect(self, mf_exit_string=None):
        if mf_exit_string:
            self.send(f"{mf_exit_string}\n")
        elif self.kwargs['mf_exit_string']:
            self.send(f"{self.kwargs['mf_exit_string']}\n")

    def initialize_sim(self, db_read_func, dc_store_func=None, from_state='defaults'):
        if from_state.lower() == 'defaults':
            settings_to_load = db_read_func(DEFAULT_SETTING_KEYS)
            settings_used = 'defaults'
        elif (from_state.lower() == 'previous') or (from_state.lower() == 'last_state'):
            settings_to_load = db_read_func(SETTING_KEYS)
            settings_used = 'last'
        else:
            log.critical("Invalid initializtion mode requested! Using default settings.")
            settings_to_load = db_read_func(DEFAULT_SETTING_KEYS)
            settings_used = 'defaults'

        for setting, value in settings_to_load.items():
            if settings_used == 'defaults':
                setting = setting[8:]  # Chop off 'default:' from the beginning of the string.
            cmd = SimCommand(setting, value)
            log.debug(cmd.format_command())
            self.send(cmd.format_command())
            if dc_store_func:
                dc_store_func({setting: cmd.value})
            time.sleep(0.1)

    def read_temp(self):
        temp = self.query("TVAL?")
        self.last_temp_read = temp
        return temp

    def read_resistance(self):
        res = self.query("RVAL?")
        self.last_resistance_read = res

    def read_temp_and_resistance(self):
        return {'temperature': self.read_temp(), 'resistance': self.read_resistance()}

    def read_output_voltage(self):
        voltage = None
        if self.query("AMAN?") == "1":
            # TODO, not this is DEF not an info message if the mode isn't changing all the time.
            #  Talk about log spam every query interval!
            log.info("SIM921 voltage output is in manual mode!")
            voltage = self.query("AOUT?")
        elif self.query("AMAN?") == "0":
            log.info("SIM921 voltage output is in scaled mode!")
            voltage = float(self.query("VOHM?")) * float(self.query("RDEV?"))
        self.last_voltage_read = voltage
        return voltage

    def monitor(self, interval: float, monitor_func: (callable, tuple), value_callback: (callable, tuple) = None):
        """
        TODO JB: This is a first stab at a quasi-general purpose monitoring function that fixes some of the issues we
         discussed.
        Given a monitoring function (or is of the same) and either one or the same number of optional callback
        functions call the monitors every interval. If one callback it will get all the values in the order of the
        monitor funcs, if a list of the same number as of monitorables each will get a single value.

        Monitor functions may not return None.

        When there is a 1-1 correspondence the callback is not called in the event of a monitoring error.
        If a single callback is present for multiple monitor functions values that had errors will be sent as None.
        Function must accept as many arguments as monitor functions.
        """
        if not isinstance(monitor_func, (list, tuple)):
            monitor_func = (monitor_func,)
        if value_callback is not None and not isinstance(value_callback, (list, tuple)):
            value_callback = (value_callback,)
        if not (value_callback is None or len(monitor_func) == len(value_callback) or len(value_callback) == 1):
            raise ValueError('When specified, the number of callbacks must be one or the number of monitor functions')

        def f():
            while True:
                vals = []
                for func in monitor_func:
                    try:
                        vals.append(func())
                    except IOError as e:
                        log.error(f"Failed to poll {func}: {e}")
                        vals.append(None)

                if value_callback is not None:
                    if len(value_callback) > 1 or len(monitor_func) == 1:
                        for v, cb in zip(vals, value_callback):
                            if v is not None:
                                try:
                                    cb(v)
                                except Exception as e:
                                    log.error(f"Callback {cb} raised {e} when called with {v}.")
                    else:
                        cb = value_callback[0]
                        try:
                            cb(*vals)
                        except Exception as e:
                            log.error(f"Callback {cb} raised {e} when called with {v}.")

                time.sleep(interval)

        self._monitor_thread = threading.Thread(target=f, name='Monitor Thread')
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def _load_calibration_curve(self, curve_num: int, curve_type, curve_name: str, file:str=None):
        """
        This is an engineering function for the SIM921 device. In normal operation of the fridge, the user should never
        have to load a curve in. This should only ever be used if (1) a new curve becomes available, (2) the
        thermometer used by the SIM921 is changed out for a new one, or (3) the original curve becomes corrupted.
        Currently (21 July 2020) designed specifically to read in the LakeShore RX-102-A calibration curve, but can be
        modified without difficulty to take in other curves. The command syntax will not change for loading the curve
        onto the SIM921, only the np.loadtxt() and data manipulation of the curve data itself. As long as the curve
        is in a format where resistance[n] < resistance[n+1] for all points n on the curve, it can be loaded into the
        SIM921 instrument.
        """
        if curve_num not in (1, 2, 3):
            log.error(f"SIM921 only accepts 1, 2, or 3 as the curve number")
            return None

        CURVE_TYPE_DICT = {'linear': '0', 'semilogt': '1', 'semilogr': '2', 'loglog': '3'}
        if curve_type not in CURVE_TYPE_DICT.keys():
            log.error(f"Invalid calibration curve type for SIM921. Valid types are {CURVE_TYPE_DICT.keys()}")
            return None

        if file is None:
            import pkg_resources as pkg
            file = pkg.resource_filename('hardware.thermometry.RX-102A', 'RX-102A_Mean_Curve.tbl')

        log.info(f"Curve data at {file}")

        try:
            curve_data = np.loadtxt(file)
            temp_data = np.flip(curve_data[:, 0], axis=0)
            res_data = np.flip(curve_data[:, 1], axis=0)
        except OSError:
            log.error(f"Could not find curve data file.")
            raise ValueError(f"{file} couldn't be loaded.")
        except IndexError:
            raise ValueError(f"{file} couldn't be loaded.")

        log.info(f"Attempting to initialize curve {curve_num}, type {curve_type}")
        try:
            # curve_init_str = "CINI "+str(curve_num)+", "+str(CURVE_TYPE_DICT[curve_type]+", "+curve_name)
            self.send(f"CINI {curve_num}, {CURVE_TYPE_DICT[curve_type]}, {curve_name}")
            for t, r in zip(temp_data, res_data):
                # self.send("CAPT"+str(curve_num)+", "+str(r)+", "+str(t))
                self.send(f"CAPT {curve_num}, {r}, {t}")
                time.sleep(0.1)
        except IOError as e:
            raise e
        log.info(f"Successfully loaded curve {curve_num} - '{curve_name}'!")


if __name__ == "__main__":

    util.setup_logging()

    #TODO if the mainfram isn't going to be used in the field but is in the lab then it needs to be an argument to this
    # program, its not a good idea to need to dive into multiple code files and change defaults just to get something
    # into test mode in the lab
    # NS: Will update. -Make a flag for this program that sets connect-mainframe to true (or something akin to it)
    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)
    sim = SIM921Agent(port=DEVICE, baudrate=9600, timeout=0.1, connect_mainframe=True, **DEFAULT_MAINFRAME_KWARGS)

    try:
        info = sim.idn
        if not sim.manufacturer_ok() or not sim.model_ok():
            msg = f'Unsupported device: {info["manufacturer"]}/{info["model"]}'
            redis.store({STATUS_KEY: msg})
            log.critical(msg)
            sys.exit(1)
        redis.store({FIRMWARE_KEY: info['firmware'], MODEL_KEY: info['model'], SN_KEY: info['firmware']})
    except IOError as e:
        log.error(f"Serial error in querying SIM921 identification information: {e}")
        redis.store({FIRMWARE_KEY: '',
                     MODEL_KEY: '',
                     SN_KEY: ''})
        sys.exit(1)
    except RedisError as e:
        log.critical(f"Redis server error! {e}")
        sys.exit(1)

    # Ensure that the scaled output will be proportional to the resistance error. NOT the temperature error. The
    # resistance spans just over 1 order of magnitude (~1-64 kOhms) while temperature spans 4 (5e-2 - 4e2 K).
    sim.send("ATEM 0")
    atem = sim.query("ATEM?")
    if atem != '0':
        log.critical(f"Setting ATEM=0 failed, got '{atem}'. "
                     "Zero, indicating the voltage scale units are resistance, is required. DO NOT OPERATE! Exiting.")
        sys.exit(1)

    # Make sure that the excitation is turned on. If not successful, exit the program
    sim.send("EXON 1")
    exon = sim.query("EXON?")
    if exon != '1':
        log.critical(f"EXON=1 failed, got '{exon}'. Unable to enable excitation and unable to operate!")
        sys.exit(1)

    # TODO Is this functionally wise? Lets say you have a crash loop periodically through the night
    #    won't the settings then be bouncing between user and defaults? Does this violate the principal of not altering
    #    active settings without explicit user action?
    #  NS: Honestly I think the flip side is probably the best option. Using 'last' as the default case and then
    #  only using 'defaults' in the case everything is out of wack and we want to set it back to tried and true values.
    sim.initialize_sim(redis.read, redis.store, from_state='defaults')

    # ---------------------------------- MAIN OPERATION (The eternal loop) BELOW HERE ----------------------------------

    def callback(t, r, v):
        d = {}
        for k, val in zip([TEMP_KEY, RES_KEY, OUTPUT_VOLTAGE_KEY], (t, r, v)):
            if val is not None:  # TODO JB: Since we don't want to store bad data
                d[k] = val
        redis.store(d, timeseries=True)
    sim.monitor(QUERY_INTERVAL, (sim.read_temp, sim.read_resistance, sim.read_output_voltage), value_callback=callback)

    while True:
        try:
            for key, val in redis.listen(SETTING_KEYS):
                log.debug(f"sim921agent received {key}, {val}. Trying to send a command.")
                cmd = SimCommand(key, val)
                if cmd.valid_value():
                    try:
                        log.info(f'Here we would send the command "{cmd.format_command()}\\n"')
                        sim.send(f"{cmd.format_command()}")
                        redis.store({cmd.setting: cmd.value})
                        redis.store({STATUS_KEY: "OK"})
                    except IOError as e:
                        redis.store({STATUS_KEY: f"Error {e}"})
                        log.error(f"Some error communicating with the SIM921! {e}")
                else:
                    log.warning(f'Not a valid value. Can\'t send key:value pair "{key} / {val}" to the SIM921!')
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)
