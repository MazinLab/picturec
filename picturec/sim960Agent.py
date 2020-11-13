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
import picturec.util as util

DEVICE = '/dev/sim921'

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
                }


log = logging.getLogger(__name__)


class SimCommand(object):
    def __init__(self, redis_setting, value):
        """
        Initializes a SimCommand. Takes in a redis device-setting:* key and desired value an evaluates it for its type,
        the mapping of the command, and appropriately sets the mapping|range for the command. If the setting is not
        supported, raise a ValueError.
        """
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
        """Return True or False if the desired value to command is valid or not."""
        if self.range is not None:
            return self.range[0] <= self.value <= self.range[1]
        else:
            return self.value in self.mapping.keys()

    def __str__(self):
        return self.format_command()

    def format_command(self):
        """
        Returns a string that can then be sent to format_msg in SIM960Agent for appropriate command syntax. Logs an
        error in the case the value is not valid and does not return anything.
        # TODO: Return None for an invalid command?
        """
        if self.valid_value():
            if self.range is not None:
                return f"{self.command} {self.value}"
            else:
                return f"{self.command} {self.mapping[self.value]}"
        else:
            log.info(f"Trying to set the SIM960 to an invalid value! Setting {self.setting} to {self.value} is not allowed")


class SIM960Agent(agent.SerialAgent):
    def __init__(self, port, baudrate=9600, timeout=0.1, polarity='negative', connect=True,
                 connect_mainframe=False, **kwargs):
        """
        Initializes SIM960 agent. First hits the superclass (SerialAgent) init function. Then sets class variables which
        will be used in normal operation. If connect mainframe is True, attempts to connect to the SIM960 via the SIM900
        in mainframe mode. Raise IOError if an invalid slot or exit string is given (or if no exit string is given).
        """
        super().__init__(port, baudrate, timeout, name='sim960')

        if connect:
            self.connect(raise_errors=False)

        self.polarity = polarity
        self.kwargs = kwargs
        self.last_input_voltage = None
        self.last_output_voltage = None
        self._voltage_monitor_thread = None

        if connect_mainframe:
            """If an IOError occurs it is raised, which will stop the program. If mainframe mode is desired and cannot
            be set up, then the rest of the program will not be able to work properly."""
            if int(self.kwargs['mf_slot']) in range(1, 9) and self.kwargs['mf_exit_string']:
                try:
                    self.mainframe_disconnect(self.kwargs['mf_exit_string'])
                    log.info(f"Connected to {self.idn}, going down the chain to connect to SIM960")
                    time.sleep(1)
                    self.mainframe_connect(self.kwargs['mf_slot'], self.kwargs['mf_exit_string'])
                    time.sleep(1)
                    log.info(f"Now connected to {self.idn}")
                    # self.mainframe_connect()
                    # time.sleep(1)
                except IOError:
                    log.error(f"Unable to communicate with SIM mainframe or module to properly connect or disconnect")
                    raise IOError(f"Unable to communicate with SIM mainframe or module to properly connect or disconnect")
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
        """Overwrite format_msg() from superclass. Formats message to send to Sim960 by ensuring uppercase characters
        followed by \n terminator"""
        return f"{msg.strip().upper()}{self.terminator}"

    @property
    def idn(self):
        """
        Queries the SIM960 for its ID information.
        Raise IOError if serial connection isn't working or if invalid values are received
        ID return string is "<manufacturer>,<model>,<instrument serial>,<firmware versions>"
        Format of return string is "s[25],s[6],s[9],s[6-8]"
        Raises IOError if query has a problem sending/receiving information to port. Raises ValueError in case the
        message is garbage and cant be read (mismatched baudrates, partial string, etc.)
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
            # Note: In the case of mainframe connection. If this error occurs, reinitialization automatically calls
            # mainframe_disconnect first. Because the exit string should not ever need to change, this will fix any
            # broken connections. Additionally, in the case of mainframe operation, and IOError should never happen
            # here. It would always be much earlier (during connect) or later (in normal operation).
            log.error(f"Serial error: {e}")
            raise e
        except ValueError as e:
            if 'mf_disconnect_string' in self.kwargs.keys():
                self.mainframe_disconnect()
            log.error(f"Bad firmware format: {firmware}. Error: {e}")
            raise IOError(f"Bad firmware format: {firmware}. Error: {e}")

    def manufacturer_ok(self):
        """Return True or False if the manufacturer from the idn query is valid or not"""
        return self.idn['manufacturer'] == "Stanford_Research_Systems"

    def model_ok(self):
        """Return True or False if the SIM model from the idn query is valid or not"""
        return self.idn['model'] == "SIM960"

    def mainframe_connect(self, mf_slot:int=None, mf_exit_string:str=None):
        """Takes in a mainframe slot and mainframe exit string. If both are present, it will send the mainframe
        connection string. Otherwise it will inform the user that they haven't specified a proper value. Log and raise
        IOError if it occurs"""
        if mf_slot and mf_exit_string:
            try:
                self.send(f"CONN {mf_slot},'{mf_exit_string}'")
            except IOError as e:
                log.error(f"Unable to connect to the SIM960 in mainframe mode: {e}")
                raise e
        else:
            log.critical("A keyword for connecting to the SIM960 in the SIM900 mainframe is missing! No command sent")


    def mainframe_disconnect(self, mf_exit_string:str=None):
        """Takes in a mainframe exit string. If both are present, it will send the mainframe connection string.
        Otherwise it will inform the user that they haven't specified a proper variable. Log and raise
        IOError if it occurs. NOTE: Does not check to make sure it is the correct exit_string."""
        if mf_exit_string:
            try:
                self.send(f"{mf_exit_string}\n")
            except IOError as e:
                log.error(f"Unable to disconnect from the SIM960 in mainframe mode due to a serial error: {e}")
                raise e
        else:
            log.critical(f"Cannot disconnect from mainframe without an exit string! Please specify.")

    def initialize_sim(self, db_read_func, db_store_func=None, from_state='default'):
        '''
        Function that can initialize the SIM960 from the default setting keys or the last stored values of the setting
        keys. If db_store_func is not None, then after sending a command to the SIM960, store the updated setting in
        the redis DB. from_state can be 'default' or 'last'. Default should only be used at the before any SIM960
        operation. After operation has begun, use 'last' to restore the settings that were previously stored.
        '''
        if from_state.lower() == 'default':
            settings_to_load = db_read_func(DEFAULT_SETTING_KEYS)
        elif from_state.lower() == 'last':
            settings_to_load = db_read_func(SETTING_KEYS)
        else:
            log.critical("Invalid initializtion mode requested! Using default settings.")
            settings_to_load = db_read_func(DEFAULT_SETTING_KEYS)

        for setting, value in settings_to_load.items():
            cmd = SimCommand(setting.lstrip('default:'), value)
            log.debug(cmd)
            self.send(cmd.format_command())
            if db_store_func:
                db_store_func({cmd.setting: cmd.value})
            time.sleep(0.1)

    def read_input_voltage(self):
        """Read the voltage being sent to the input monitor of the SIM960 from the SIM921"""
        return self.query("MMON?")

    def read_output_voltage(self):
        """Report the voltage at the output of the SIM960. In manual mode, this will be explicitly controlled using MOUT
        and in PID mode this will be the value set by the function Output = P(e + I * int(e) + D * derv(e)) + Offset"""
        return self.query("OMON?")

    def monitor_voltages(self, interval, input_value_callback=None, output_value_callback=None):
        """Create and start a thread to handle voltage monitoring for the input and output voltages. In the case of any
        IOErrors, do not break the thread, simply log the error that was seen. If there are input or output value
        callback functions, call them (these are typically used to store the values to the redis DB)."""
        def f():
            while True:
                last_input_voltage = None
                last_output_voltage = None
                try:
                    self.last_input_voltage = self.read_input_voltage()
                    last_input_voltage = self.last_input_voltage
                except IOError as e:
                    log.error(f"Error: {e}")

                try:
                    self.last_output_voltage = self.read_output_voltage()
                    last_output_voltage = self.last_output_voltage
                except IOError as e:
                    log.error(f"Error: {e}")

                if input_value_callback is not None and last_input_voltage is not None:
                    try:
                        input_value_callback(self.last_input_voltage)
                    except Exception as e:
                        log.error(f"Unable to store input voltage due to error: {e}")

                if output_value_callback is not None and last_output_voltage is not None:
                    try:
                        output_value_callback(self.last_output_voltage)
                    except Exception as e:
                        log.error(f"Unable to store out voltage due to redis error: {e}")

                time.sleep(interval)

        self._voltage_monitor_thread = threading.Thread(target=f, name='Input Voltage Monitor Thread')
        self._voltage_monitor_thread.daemon = True
        self._voltage_monitor_thread.start()


if __name__ == "__main__":

    util.setup_logging()

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)
    sim = SIM960Agent(port=DEVICE, baudrate=9600, timeout=0.1, connect_mainframe=True,
                      **DEFAULT_MAINFRAME_KWARGS)

    try:
        info = sim.idn
        if not sim.manufacturer_ok() or not sim.model_ok():
            msg = f'Unsupported device: {info["manufacturer"]}/{info["model"]}'
            redis.store({STATUS_KEY: msg})
            log.critical(msg)
            sys.exit(1)
        redis.store({FIRMWARE_KEY: info['firmware'], MODEL_KEY: info['model'], SN_KEY: info['firmware']})
    except IOError as e:
        log.critical(f"Query SIM960 ID failed: {e}")
        redis.store({FIRMWARE_KEY: '', MODEL_KEY: '', SN_KEY: ''})
        sys.exit(1)
    except RedisError as e:
        log.critical(f"Redis server error! {e}")
        sys.exit(1)

    # Set polarity to negative here. This is a non-redis controlled setting (not modifiable during normal operation).
    sim.send("APOL 0")
    polarity = sim.query("APOL?")
    if polarity != '0':
        log.critical(f"Polarity query returned {polarity}. Setting PID loop polarity to negative failed.")
        sys.exit(1)

    # TODO Is this functionally wise? Lets say you have a crash loop periodically through the night
    #    won't the settings then be bouncing between user and defaults? Does this violate the principal of not altering
    #    active settings without explicit user action?
    #  Response - Honestly I think the flip side is probably the best option. Using 'last' as the default case and then
    #  only using 'defaults' in the case everything is out of wack and we want to set it back to tried and true values.
    sim960.initialize_sim(redis.read, redis.store, from_state='defaults')
    sim.initialize_sim(redis.read, redis.store, from_state='defaults')
    # ---------------------------------- MAIN OPERATION (The eternal loop) BELOW HERE ----------------------------------

    OUTPUT_TO_CURRENT_FACTOR = 1  # V/A (TODO: Measure this value)
    store_input = lambda x: redis.store({INPUT_VOLTAGE_KEY: x}, timeseries=True)
    store_output = lambda x: redis.store({OUTPUT_VOLTAGE_KEY: x,
                                          MAGNET_CURRENT_KEY: x * OUTPUT_TO_CURRENT_FACTOR},
                                         timeseries=True)
    sim.monitor_voltages(QUERY_INTERVAL, input_value_callback=store_input, output_value_callback=store_output)
    #  TODO: Figure out where to add in magnet state checking (in the sim960 or elsewhere?)

    while True:
        try:
            for key, val in redis.listen(SETTING_KEYS):
                log.debug(f"sim960agent received {key}, {val}. Trying to send a command.")
                cmd = SimCommand(key, val)
                if cmd.valid_value():
                    try:
                        log.info(f'Sending command "{cmd}"')  #TODO if you want to explicityy show non-printables in the
                        #  msg then use a stinrg function to escape them either in __str__ or str(cmd).XXXX
                        sim.send(f"{cmd.format_command()}")
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
