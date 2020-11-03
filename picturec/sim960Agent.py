"""
Author: Noah Swimmer, 21 July 2020

NOTE: Unlike the SIM921, the SIM960 supports different baudrates. These need to be tested outside of the mainframe
before settling on the most appropriate one.

TODO: Measure output voltage-to-current conversion. Should be ~1 V/A (from the hc boost board)
TODO: Also measure magnet-current-to-currentduino-measurement conversion (does the currentduino report the same thing we
 measure with an ammeter?)

TODO: Consider how to most effectively store magnet current data (conversion from SIM960 output voltage?) and magnet
 state/safety checks (should this be done in a monitoring loop in the sim960 agent or from a fridge manager?)
"""

import numpy as np
import logging
import time
import sys
import picturec.agent as agent
from picturec.pcredis import PCRedis, RedisError
import threading
import os

REDIS_DB = 0
QUERY_INTERVAL = 1

SETTING_KEYS = ['device-settings:sim960:mode',
                'device-settings:sim960:vout-value',
                'device-settings:sim960:vout-min-limit',
                'device-settings:sim960:vout-max-limit',
                'device-settings:sim960:pid-p:enabled',
                'device-settings:sim960:pid-i:enabled',
                'device-settings:sim960:pid-d:enabled',
                'device-settings:sim960:pid-p:value',
                'device-settings:sim960:pid-i:value',
                'device-settings:sim960:pid-d:value',
                'device-settings:sim960:setpoint-mode',
                'device-settings:sim960:pid-control-vin-setpoint',
                'device-settings:sim960:setpoint-ramp-rate',
                'device-settings:sim960:setpoint-ramp-enable']

                # TODO: Magnet ramp rate/enable is not a sim-setting! Magnet ramp should be run elsewhere. Safety
                #  checks may(?) be performed here.
                # 'device-settings:sim960:magnet-ramp-rate',
                # 'device-settings:sim960:magnet-ramp-enable']


default_key_factory = lambda key: f"default:{key}"
DEFAULT_SETTING_KEYS = [default_key_factory(key) for key in SETTING_KEYS]


OUTPUT_VOLTAGE_KEY = 'status:device:sim960:hcfet-control-voltage'  # Set by 'MOUT' in manual mode, monitored by 'OMON?' always
INPUT_VOLTAGE_KEY = 'status:device:sim921:sim960-vout'  # This is the output from the sim921 to the sim960 for PID control
MAGNET_CURRENT_KEY = 'status:magnet:current'  # To get the current from the sim960. We will need to run a calibration
# test to figure out what the output voltage to current conversion is.
MAGNET_STATE_KEY = 'status:magnet:state'  # OFF | RAMPING | SOAKING | QUENCH (DON'T QUENCH!)
HEATSWITCH_STATUS_KEY = 'status:heatswitch'  # Needs to be read to determine its status, and set by the sim960agent during
# normal operation so it's possible to run the ramp appropriately
HC_BOARD_CURRENT = 'status:highcurrentboard:current'  # Current from HC Boost board.


TS_KEYS = [OUTPUT_VOLTAGE_KEY, INPUT_VOLTAGE_KEY, MAGNET_CURRENT_KEY,
           MAGNET_STATE_KEY, HEATSWITCH_STATUS_KEY, HC_BOARD_CURRENT]


STATUS_KEY = 'status:device:sim960:status'
MODEL_KEY = 'status:device:sim960:model'
FIRMWARE_KEY = 'status:device:sim960:firmware'
SN_KEY = 'status:device:sim960:sn'


DEFAULT_MAINFRAME_KWARGS = {'mf_slot': 5, 'mf_exit_string': 'xyz'}


COMMAND_DICT = {'device-settings:sim960:mode': {'command': 'AMAN', 'vals': {'manual': '0', 'pid': '1'}},
                'device-settings:sim960:vout-value': {'command': 'MOUT', 'vals': [-10, 10]},
                'device-settings:sim960:vout-min-limit': {'command': 'LLIM', 'vals': [-10, 10]},
                'device-settings:sim960:vout-max-limit': {'command': 'ULIM', 'vals': [-10, 10]},
                'device-settings:sim960:setpoint-mode': {'command': 'INPT', 'vals': {'internal': '0', 'external': '1'}},
                'device-settings:sim960:pid-control-vin-setpoint': {'command': 'SETP', 'vals': [-10, 10]},
                'device-settings:sim960:pid-p:value': {'command': 'GAIN', 'vals': [-1e3, -1e-1]},
                'device-settings:sim960:pid-i:value': {'command': 'INTG', 'vals': [1e-2, 5e5]},
                'device-settings:sim960:pid-d:value': {'command': 'DERV', 'vals': [0, 1e1]},
                'device-settings:sim960:setpoint-ramp-enable': {'command': 'RAMP', 'vals': {'off': '0', 'on': '1'}},  # Note: Internal setpoint ramp, NOT magnet ramp
                'device-settings:sim960:setpoint-ramp-rate': {'command': 'RATE', 'vals': [1e-3, 1e4]},  # Note: Internal setpoint ramp rate, NOT magnet ramp
                'device-settings:sim960:pid-p:enabled': {'command': 'PCTL', 'vals': {'off': '0', 'on': '1'}},
                'device-settings:sim960:pid-i:enabled': {'command': 'ICTL', 'vals': {'off': '0', 'on': '1'}},
                'device-settings:sim960:pid-d:enabled': {'command': 'DCTL', 'vals': {'off': '0', 'on': '1'}},
                # TODO: turn apol and flow to 1-off commands at the beginning of main.
                'APOL': {'vals': {'negative': '0', 'positive': '1'}},
                'FLOW': {'vals': {'none': '0', 'rts': '1', 'xon': '2'}}
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
            log.info(f"Trying to set the SIM960 to an invalid value! Setting {self.setting} to {self.value}")


class SIM960Agent(agent.SerialAgent):
    def __init__(self, port, baudrate=9600, timeout=0.1, polarity='negative', connect=True,
                 connect_mainframe=False, **kwargs):
        super().__init__(port, baudrate, timeout, name='sim960')

        if connect:
            self.connect(raise_errors=False)

        self.polarity = polarity
        self.kwargs = kwargs
        self.last_input_voltage = None
        self.last_output_voltage = None

        if connect_mainframe:
            if (int(self.kwargs['mf_slot']) in (np.arange(7)+1)) and self.kwargs['mf_exit_string']:
                self.mainframe_disconnect()
                log.info(f"Connected to {self.idn}, going down the chain to connect to SIM960")
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
            log.info(f"Resetting the SIM960!")
            self.send("*RST")
        except IOError as e:
            raise e

    def format_msg(self, msg: str):
        return f"{msg.strip().upper()}{self.terminator}"

    @property
    def idn(self):
        """
        Queries the SIM960 for its ID information.
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
        return self.idn['model'] == "SIM960"

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

    def read_input_voltage(self):
        input = self.query("MMON?")

        return input

    def read_output_voltage(self):
        output = self.query("OMON?")

        return output

    def monitor_input_voltage(self, interval, value_callback=None):
        def f():
            while True:
                last_input_voltage = None
                try:
                    self.last_input_voltage = self.read_input_voltage()
                    last_input_voltage = self.last_input_voltage
                except IOError as e:
                    log.error(f"Error: {e}")

                if value_callback is not None and last_input_voltage is not None:
                    try:
                        value_callback(self.last_input_voltage)
                    except:
                        log.error(f"Unable to store input voltage due to redis error: {e}")

                time.sleep(interval)

        self._input_voltage_monitor_thread = threading.Thread(target=f, name='Input Voltage Monitor Thread')
        self._input_voltage_monitor_thread.daemon = True
        self._input_voltage_monitor_thread.start()

    def monitor_output_voltage(self, interval, value_callback=None):
        def f():
            while True:
                last_output_voltage = None
                try:
                    self.last_output_voltage = self.read_output_voltage()
                    last_output_voltage = self.last_output_voltage
                except IOError as e:
                    log.error(f"Error: {e}")

                if value_callback is not None and last_output_voltage is not None:
                    try:
                        value_callback(self.last_output_voltage)
                    except:
                        log.error(f"Unable to store out voltage due to redis error: {e}")

                time.sleep(interval)

        self._output_voltage_monitor_thread = threading.Thread(target=f, name='Input Voltage Monitor Thread')
        self._output_voltage_monitor_thread.daemon = True
        self._output_voltage_monitor_thread.start()

if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)
    sim960 = SIM960Agent(port='/dev/sim921', baudrate=9600, timeout=0.1, connect_mainframe=True, **DEFAULT_MAINFRAME_KWARGS)

    try:
        sim960_info = sim960.idn
        if not sim960.manufacturer_ok():
            redis.store({STATUS_KEY: f'Unsupported manufacturer: {sim960_info["manufacturer"]}'})
            sys.exit(1)
        if not sim960.model_ok():
            redis.store({STATUS_KEY: f'Unsupported model: {sim960_info["model"]}'})
            sys.exit(1)
        redis.store({FIRMWARE_KEY: sim960_info['firmware'],
                     MODEL_KEY: sim960_info['model'],
                     SN_KEY: sim960_info['firmware']})
    except IOError as e:
        log.error(f"Serial error in querying SIM921 identification information: {e}")
        redis.store({FIRMWARE_KEY: '',
                     MODEL_KEY: '',
                     SN_KEY: ''})
        sys.exit(1)
    except RedisError as e:
        log.critical(f"Redis server error! {e}")
        sys.exit(1)

    # Set polarity to negative here. This is a non-redis controlled setting (not modifiable during normal operation).
    sim960.send("APOL 0")
    polarity = sim960.query("APOL?")
    if polarity == '0':
        log.info(f"Polarity query returned 0. PID loop polarity is negative")
    elif polarity == '1':
        log.critical(f"Polarity query returned 1. PID loop polarity is positive.")
        sys.exit(1)
    else:
        log.error(f"An unexpected value for polarity was returned. Please restart the program!")
        sys.exit(1)

    sim960.initialize_sim(redis.read, redis.store, from_state='defaults')

    # ---------------------------------- MAIN OPERATION (The eternal loop) BELOW HERE ----------------------------------

    store_input_voltage_func = lambda x: redis.store({INPUT_VOLTAGE_KEY: x}, timeseries=True)
    sim960.monitor_input_voltage(QUERY_INTERVAL, value_callback=store_input_voltage_func)

    store_output_voltage_func = lambda x: redis.store({OUTPUT_VOLTAGE_KEY: x}, timeseries=True)
    sim960.monitor_output_voltage(QUERY_INTERVAL, value_callback=store_output_voltage_func)

    # Below : A version of the store output voltage where we also store the magnet current.
    #  TODO: (1) Apply this. (2) Figure out where to add in magnet state checking (in the sim960 or elsewhere?)
    # OUTPUT_TO_CURRENT_FACTOR = 1 # V/A (TODO: Measure this value)
    # store_output_voltage_and_magnet_current_func = lambda x: redis.store({OUTPUT_VOLTAGE_KEY: x, MAGNET_CURRENT_KEY: x * OUTPUT_TO_CURRENT_FACTOR}, timeseries=True)
    # sim960.monitor_output_voltage(QUERY_INTERVAL, value_callback=store_output_voltage_func)

    while True:
        try:
            for key, val in redis.listen(SETTING_KEYS):
                log.debug(f"sim960agent received {key}, {val}. Trying to send a command.")
                cmd = SimCommand(key, val)
                if cmd.valid_value():
                    try:
                        log.info(f'Here we would send the command "{cmd.format_command()}\\n"')
                        sim960.send(f"{cmd.format_command()}")
                        redis.store({cmd.setting: cmd.value})
                        redis.store({STATUS_KEY: "OK"})
                    except IOError as e:
                        redis.store({STATUS_KEY: f"Error {e}"})
                        log.error(f"Some error communicating with the SIM960! {e}")
                else:
                    log.warning(f'Not a valid value. Can\'t send key:value pair "{key} / {val}" to the SIM960!')
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)
