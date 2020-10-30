"""
Author: Noah Swimmer, 21 July 2020

NOTE: Unlike the SIM921, the SIM960 supports different baudrates. These need to be tested outside of the mainframe
before settling on the most appropriate one.

TODO: Figure out appropriate handling of 'device-settings:sim960:pid'
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
                'device-settings:sim960:setpoint-ramp-enable',
                'device-settings:sim960:ramp-rate',
                'device-settings:sim960:ramp-enable']


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


STATUS_KEY = 'status:device:sim921:status'
MODEL_KEY = 'status:device:sim921:model'
FIRMWARE_KEY = 'status:device:sim921:firmware'
SERIALNO_KEY = 'status:device:sim921:sn'


DEFAULT_MAINFRAME_KWARGS = {'mf_slot': 5, 'mf_exit_string': 'xyz'}


COMMAND_DICT = {'device-settings:sim960:mode': {'command': 'AMAN', 'vals': {'manual': '0', 'pid': '1'}},
                'device-settings:sim960:vout-value': {'command': 'MOUT', 'vals': [-10, 10]},
                'device-settings:sim960:vout-min-limit': {'command': 'LLIM', 'vals': [-10, 10]},
                'device-settings:sim960:vout-max-limit': {'command': 'ULIM', 'vals': [-10, 10]},
                'device-settings:sim960:setpoint-mode': {'command': 'INPT', 'vals': {'internal': '0', 'external': '1'}},
                'device-settings:sim960:pid-control-vin-setpoint': {'command': 'SETP', 'vals': [-10, 10]},
                'device-settings:sim960:pid-p:value': {'command': 'GAIN', 'vals': [-1e3, -1e-1]},
                'device-settings:sim960:pid-i:value': {'command': 'INTG', 'vals': [1e-2, 5e5]},
                'device-settings:sim960:pid-d:value': {'command': 'DERV', 'vals': [1e-6, 1e1]},
                'device-settings:sim960:setpoint-ramp-enable': {'command': 'RAMP', 'vals': {'off': '0', 'on': '1'}},
                'device-settings:sim960:setpoint-ramp-rate': {'command': 'RATE', 'vals': [1e-3, 1e4]},
                'device-settings:sim960:pid-p:enabled': {'command': 'PCTL', 'vals': {'off': '0', 'on': '1'}},
                'device-settings:sim960:pid-i:enabled': {'command': 'ICTL', 'vals': {'off': '0', 'on': '1'}},
                'device-settings:sim960:pid-d:enabled': {'command': 'DCTL', 'vals': {'off': '0', 'on': '1'}},
                'APOL': {'vals': {'negative': '0', 'positive': '1'}},
                'FLOW': {'vals': {'none': '0', 'rts': '1', 'xon': '2'}}
                }

class SIM960Agent(object):
    def __init__(self, port, redis, redis_ts, baudrate=9600, timeout=0.1, initialize=True, sim_polarity='negative'):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)
        time.sleep(.5)
        self.redis = redis
        self.redis_ts = redis_ts

        self.sim_polarity = sim_polarity

        self.prev_sim_settings = {}
        self.new_sim_settings = {}

        if initialize:
            self.initialize_sim()
        else:
            self.read_default_settings()

    def connect(self, reconnect=False, raise_errors=True):
        """
        Create serial connection with the SIM960. In reality, the SIM960 connection is only up to the USB-to-RS232
        interface, and so disconnects will need to be checked differently from either side of the converter.
        """
        if reconnect:
            self.disconnect()

        try:
            if self.ser.isOpen():
                return
        except Exception:
            pass

        log.debug(f"Connecting to {self.port} at {self.baudrate}")
        try:
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
            log.debug(f"port {self.port} connection established")
            return True
        except (SerialException, IOError) as e:
            self.ser = None
            log.error(f"Conntecting to port {self.port} failed: {e}")
            if raise_errors:
                raise e
            else:
                return False

    def disconnect(self):
        """
        Disconnect from the SIM960 serial connection
        """
        try:
            self.ser.close()
            self.ser = None
        except Exception as e:
            log.info(f"Exception durring disconnect: {e}")

    def send(self, msg: str, connect=True):
        """
        Send a message to the SIM960 in its desired format.
        The typical message is all caps, terminated with a newline character '\n'
        Commands will be followed by a code, typically a number (e.g. 'AMAN 0\n')
        Queries will be followed by a question mark (e.g. 'MOUT?\n')
        The identity query (and a number of other 'special' commands) start with a * (e.g. '*IDN?')
        """
        if connect:
            self.connect()
        msg = msg.strip().upper() + "\n"
        try:
            log.debug(f"Writing message: {msg}")
            self.ser.write(msg.encode("utf-8"))
            log.debug(f"Sent {msg} successfully")
        except (SerialException, IOError) as e:
            self.disconnect()
            log.error(f"Send failed: {e}")
            raise e

    def receive(self):
        """
        Receiving from the SIM960 consists of reading a line, as some queries may return longer strings than others,
        and each query has its own parsing needs (for example: '*IDN?' returns a string with model, serial number,
        firmware, and company, while 'MOUT?' returns the measured voltage output value at the time)
        """
        try:
            data = self.ser.readline().decode("utf-8").strip()
            log.debug(f"read {data} from SIM960")
            return data
        except (IOError, SerialException) as e:
            self.disconnect()
            log.debug(f"Send failed {e}")
            raise e

    def reset_sim(self):
        """
        Send a reset command to the SIM device. This should not be used in regular operation, but if the device is not
        working it is a useful command to be able to send.
        BE CAREFUL - This will reset certain parameters which are set for us to control the ADR magnet.
        If you do perform a reset, it will then be helpful to restore the 'default settings' which we have determined
        to be the optimal to read out the hardware we have.
        """
        try:
            log.info(f"Resetting the SIM960!")
            self.send("*RST")
        except IOError as e:
            raise e

    def command(self, command_msg: str):
        """
        A wrapper for the self.send function. This assumes that the command_msg input is a legal command as dictated by
        the manual in picturec/hardware/thermometry/SRS-SIM960-PIDController-Manual.pdf
        """
        try:
            log.debug(f"Sending command '{command_msg}' to SIM960")
            self.send(command_msg)
        except IOError as e:
            raise e

    def query(self, query_msg: str):
        """
        A wrapper to both send and receive in one holistic block so that we ensure if a query is sent, and answer is
        received.
        This assumes that the command_msg input is a legal query as dictated by the manual in
        picturec/hardware/thermometry/SRS-SIM960-PIDController-Manual.pdf
        """
        try:
            log.debug(f"Querying '{query_msg}' from SIM960")
            self.send(query_msg)
            response = self.receive()
        except Exception as e:
            raise IOError(e)
        return response

    def query_ID(self):
        """
        Specific function to query the SIM960 identity to get its s/n, firmware, and model. Will be used in
        conjunction with store_sim960_id_info to ensure we properly log the .
        """
        try:
            idn_msg = self.query("*IDN?")
        except IOError as e:
            raise e

        try:
            idn_info = idn_msg.split(',')
            model = idn_info[1]
            sn = idn_info[2]
            firmware = idn_info[3]
            log.info(f"SIM960 Identity - model {model}, s/n:{sn}, firmware {firmware}")
        except Exception as e:
            raise ValueError(f"Illegal format. Check communication is working properly: {e}")

        return [model, sn, firmware]

    def read_default_settings(self):
        """
        Reads all of the default SIM960 settings that are stored in the redis database and reads them into the
        dictionaries which the agent will use to command the SIM960 to change settings. Also reads these now current
        settings into the redis database.
        """
        try:
            for i, j in zip(DEFAULT_SETTING_KEYS, SETTING_KEYS):
                value = get_redis_value(self.redis, i)
                self.prev_sim_settings[j] = value
                self.new_sim_settings[j] = value
                store_redis_data(self.redis, {j: value})
        except RedisError as e:
            raise e

    def initialize_sim(self):
        log.info(f"Initializing SIM960")

        try:
            self.read_default_settings()

            self.reset_sim()

            self.set_output_mode("manual")
            self.set_manual_output_voltage(0)
            self.set_flow_control("none")
            self.set_output_lower_limit(-0.100)
            self.set_output_upper_limit(10)
            self.set_setpoint_mode("internal")
            self.enable_setpoint_ramping("on")
            self.set_setpoint_ramping_rate(5e-3)
            self.set_internal_setpoint_value(0.0)

            self.set_pid_polarity("negative")
            self.set_pid_p_value("pi", -1.6e1)
            self.set_pid_i_value("pi", 0.2)
            self.set_pid_d_value("pi", 0.0)

        except IOError as e:
            log.debug(f"Initialization failed: {e}")
            raise e
        except RedisError as e:
            log.debug(f"Redis error occurred in initialization of SIM960: {e}")
            raise e

    def set_sim_value(self, setting: str, value: str):
        """
        Setting param must be one of the valid setting commands. Value must be a legal value to send to the SIM960 as
        laid out in its manual, pages 3-8 to 3-24 (picturec/hardware/thermometry/SRS-SIM960-PIDController-Manual.pdf)
        """
        set_string = setting + " " + value
        try:
            self.command(set_string)
        except IOError as e:
            raise e

    def set_sim_param(self, command, value):
        """
        Takes a given command from the SIM960 manual (the top level key in the COMMAND_DICT) and uses the keys/vals
        in the dictionary value for that command to determine if legal values are being sent to the SIM960. If all of
        the rules for a given command are properly met, sends that command to the SIM960 for the value to be changed.
        """
        try:
            dict_for_command = COMMAND_DICT[command]
        except KeyError as e:
            raise KeyError(f"'{command}' is not a valid SIM960 command! Error: {e}")

        command_key = dict_for_command['key'] if 'key' in dict_for_command.keys() else None
        command_vals = dict_for_command['vals']

        if type(command_vals) is list:
            min_val = command_vals[0]
            max_val = command_vals[1]

            if value < min_val:
                log.warning(f"Cannot set {command_key} to {value}, it is below the minimum allowed "
                                            f"value! Setting {command_key} to minimum allowed value: {min_val}")
                cmd_value = str(min_val)
            elif value > max_val:
                log.warning(f"Cannot set {command_key} to {value}, it is above the maximum allowed "
                                            f"value! Setting {command_key} to maximum allowed value: {max_val}")
                cmd_value = str(max_val)
            else:
                log.info(f"Setting {command_key} to {value}")
                cmd_value = str(value)
        else:
            try:
                cmd_value = command_vals[value]
                log.info(f"Setting {command_key} to {value}")
            except KeyError:
                raise KeyError(f"{value} is not a valid value for '{command}")

        try:
            self.set_sim_value(command, cmd_value)
            if command_key is not None:
                store_redis_data(self.redis, {command_key: value})
        except IOError as e:
            raise e
        except RedisError as e:
            raise e

    def set_output_mode(self, mode):
        try:
            self.set_sim_param("AMAN", str(mode))
        except (IOError, RedisError) as e:
            raise e

    def set_manual_output_voltage(self, voltage):
        try:
            self.set_sim_param("MOUT", float(voltage))
        except (IOError, RedisError) as e:
            raise e

    def set_flow_control(self, method):
        try:
            self.set_sim_param("FLOW", str(method))
        except (IOError, RedisError) as e:
            raise e

    def set_output_lower_limit(self, value):
        try:
            ulim = self.query("ULIM?")
            if float(ulim) > float(value):
                self.set_sim_param("LLIM", float(value))
            else:
                log.warning(f"Trying to set an lower voltage limit above the upper voltage limit!")
        except (IOError, RedisError) as e:
            raise e

    def set_output_upper_limit(self, value):
        try:
            llim = self.query("LLIM?")
            if float(llim) < float(value):
                self.set_sim_param("ULIM", float(value))
            else:
                log.warning(f"Trying to set an upper voltage limit below the lower voltage limit!")
        except (IOError, RedisError) as e:
            raise e

    def set_setpoint_mode(self, mode):
        try:
            self.set_sim_param("INPT", str(mode))
        except (IOError, RedisError) as e:
            raise e

    def set_internal_setpoint_value(self, value):
        try:
            self.set_sim_param("SETP", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_pid_p_value(self, p_on, p_value):
        try:
            self.set_sim_param("PCTL", str(p_on))
            self.set_sim_param("GAIN", float(p_value))
        except (IOError, RedisError) as e:
            raise e

    def set_pid_i_value(self, i_on, i_value):
        try:
            self.set_sim_param("ICTL", str(i_on))
            self.set_sim_param("INTG", float(i_value))
        except (IOError, RedisError) as e:
            raise e

    def set_pid_d_value(self, d_on, d_value):
        try:
            self.set_sim_param("DCTL", str(d_on))
            self.set_sim_param("DERV", float(d_value))
        except (IOError, RedisError) as e:
            raise e

    def set_pid_polarity(self, polarity):
        try:
            self.set_sim_param("APOL", str(polarity))
        except (IOError, RedisError) as e:
            raise e

    def enable_setpoint_ramping(self, enabled):
        try:
            self.set_sim_param("RAMP", str(enabled))
        except (IOError, RedisError) as e:
            raise e

    def set_setpoint_ramping_rate(self, rate):
        try:
            self.set_sim_param("RATE", float(rate))
        except (IOError, RedisError) as e:
            raise e

    def _check_settings(self):
        """
        Reads in the redis database values of the setting keys to self.new_sim_settings and then compares them to
        those in self.prev_sim_settings. If any of the values are different, it stores the key of the desired value to
        change as well as the new value. These will be used in self.update_sim_settings() to send the necessary commands
        to the SIM960 to change any of the necessary settings on the instrument.

        Returns a dictionary where the keys are the redis keys that correspond to the SIM960 settings and the values are
        the new, desired values to set them to.
        """
        try:
            for i in self.new_sim_settings.keys():
                self.new_sim_settings[i] = get_redis_value(self.redis, i)
        except RedisError as e:
            raise e

        changed_idx = []
        for i,j in enumerate(zip(self.prev_sim_settings.values(), self.new_sim_settings.values())):
            if str(j[0]) != str(j[1]):
                changed_idx.append(True)
            else:
                changed_idx.append(False)

        keysToChange = np.array(list(self.new_sim_settings.keys()))[changed_idx]
        valsToChange = np.array(list(self.new_sim_settings.values()))[changed_idx]

        return {k: v for k, v in zip(keysToChange, valsToChange)}

    def update_sim_settings(self):
        """
        Takes the output of self._check_settings() and sends the appropriate commands to the SIM960 to update the
        desired settings. Leaves the unchanged settings alone and does not send any commands associated with them.

        After changing all of the necessary settings, self.new_sim_settings is read into self.prev_sim_settings for
        continuity. This happens each time through the loop so self.prev_sim_settings reflects what the settings were in
        the previous loop and self.new_sim_settings reflects the desired state.
        """
        key_val_dict = self._check_settings()
        keys = key_val_dict.keys()
        try:
            if 'device-settings:sim960:mode' in keys:
                self.set_setpoint_mode(key_val_dict['device-settings:sim960:mode'])
            if 'device-settings:sim960:vout-min-limit' in keys:
                self.set_output_lower_limit(key_val_dict['device-settings:sim960:vout-min-limit'])
            if 'device-settings:sim960:vout-max-limit' in keys:
                self.set_output_upper_limit(key_val_dict['device-settings:sim960:vout-max-limit'])
            if 'device-settings:sim960:pid' in keys:
                self.set_pid_p_value(key_val_dict['device-settings:sim960:pid'],
                                     self.new_sim_settings['device-settings:sim960:pid-p'])
                self.set_pid_i_value(key_val_dict['device-settings:sim960:pid'],
                                     self.new_sim_settings['device-settings:sim960:pid-i'])
                self.set_pid_d_value(key_val_dict['device-settings:sim960:pid'],
                                     self.new_sim_settings['device-settings:sim960:pid-d'])
            if 'device-settings:sim960:pid-p' in keys:
                self.set_pid_p_value(self.new_sim_settings['device-settings:sim960:pid'],
                                     key_val_dict['device-settings:sim960:pid-p'])
            if 'device-settings:sim960:pid-i' in keys:
                self.set_pid_i_value(self.new_sim_settings['device-settings:sim960:pid'],
                                     key_val_dict['device-settings:sim960:pid-i'])
            if 'device-settings:sim960:pid-d' in keys:
                self.set_pid_d_value(self.new_sim_settings['device-settings:sim960:pid'],
                                     key_val_dict['device-settings:sim960:pid-d'])
            if 'device-settings:sim960:setpoint-mode' in keys:
                self.set_setpoint_mode(key_val_dict['device-settings:sim960:setpoint-mode'])
            if 'device-settings:sim960:pid-control-vin-setpoint' in keys:
                self.set_internal_setpoint_value(key_val_dict['device-settings:sim960:pid-control-vin-setpoint'])
            if 'device-settings:sim960:setpoint-ramp-rate' in keys:
                self.set_setpoint_ramping_rate(key_val_dict['device-settings:sim960:setpoint-ramp-rate'])
            if 'device-settings:sim960:setpoint-ramp-enable' in keys:
                self.enable_setpoint_ramping(key_val_dict['device-settings:sim960:setpoint-ramp-enable'])
            if 'device-settings:sim960:vout-value' in keys:
                self.set_manual_output_voltage(key_val_dict['device-settings:sim960:vout-value'])
        except (IOError, RedisError) as e:
            raise e

        for i in self.prev_sim_settings.keys():
            self.prev_sim_settings[i] = self.new_sim_settings[i]


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    for key in TS_KEYS:
        try:
            redis_ts.create(key)
        except RedisError:
            getLogger(__name__).debug(f"KEY '{key}' already exists")
            pass

    return redis_ts


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def get_redis_value(redis, key):
    try:
        val = redis.get(key).decode("utf-8")
    except RedisError as e:
        getLogger(__name__).error(f"Error accessing {key} from redis: {e}")
        return None
    return val


def store_sim960_status(redis, status: str):
    redis.set(STATUS_KEY, status)


def store_sim960_id_info(redis, info):
    redis.set(MODEL_KEY, info[0])
    redis.set(SERIALNO_KEY, info[1])
    redis.set(FIRMWARE_KEY, info[2])


def store_redis_data(redis, data):
    for k, v in data.items():
        getLogger(__name__).info(f"Setting key:value - {k}:{v}")
        redis.set(k, v)


def store_redis_ts_data(redis_ts, data):
    for k, v in data.items():
        getLogger(__name__).info(f"Setting key:value - {k}:{v} at {int(time.time())}")
        redis_ts.add(key=k, value=v, timestamp='*')
