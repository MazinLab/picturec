"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO: - Create run function
 - Give keys names and use them that way
 - Turn load_new_curve and set_analog_output_scale_units into internal functions (should only be used for engineering)
 - In curve selection function, add a validity check to only allow us to change to valid curves.
 - Decide if mainframe mode is worth using (I think it is for testing)
"""

import serial
import numpy as np
from logging import getLogger
from serial import SerialException
import time
from redis import Redis, RedisError
from redistimeseries.client import Client
import sys


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


DEFAULT_SETTING_KEYS = ['default:device-settings:sim921:resistance-range',
                        'default:device-settings:sim921:excitation-value',
                        'default:device-settings:sim921:excitation-mode',
                        'default:device-settings:sim921:time-constant',
                        'default:device-settings:sim921:temp-offset',
                        'default:device-settings:sim921:temp-slope',
                        'default:device-settings:sim921:resistance-offset',
                        'default:device-settings:sim921:resistance-slope',
                        'default:device-settings:sim921:curve-number',
                        'default:device-settings:sim921:manual-vout',
                        'default:device-settings:sim921:output-mode']


TEMP_KEY = 'status:temps:mkidarray:temp'
RES_KEY = 'status:temps:mkidarray:resistance'
OUTPUT_VOLTAGE_KEY = 'status:device:sim921:sim960-vout'


TS_KEYS = [TEMP_KEY, RES_KEY, OUTPUT_VOLTAGE_KEY]


STATUS_KEY = 'status:device:sim921:status'
MODEL_KEY = 'status:device:sim921:model'
FIRMWARE_KEY = 'status:device:sim921:firmware'
SERIALNO_KEY = 'status:device:sim921:sn'


COMMAND_DICT = {'RANG': {'key': 'device-settings:sim921:resistance-range',
                         'vals': {20e-3: '0', 200e-3: '1', 2: '2', 20: '3', 200: '4',
                                  2e3: '5', 20e3: '6', 200e3: '7', 2e6: '8', 20e6: '9'}},
                'EXCI': {'key': 'device-settings:sim921:excitation-value',
                         'vals': {0: '-1', 3e-6: '0', 10e-6: '1', 30e-6: '2', 100e-6: '3',
                                  300e-6: '4', 1e-3: '5', 3e-3: '6', 10e-3: '7', 30e-3: '8'}},
                'MODE': {'key': 'device-settings:sim921:excitation-mode',
                         'vals': {'passive': '0', 'current': '1', 'voltage': '2', 'power': '3'}},
                'EXON': {'key': 'device-settings:sim921:excitation-value',
                         'vals': {'off': '0', 'on': '1'}},
                'TSET': {'key': 'device-settings:sim921:temp-offset',
                         'vals': [0.050, 40]},
                'RSET': {'key': 'device-settings:sim921:resistance-offset',
                         'vals': [1049.08, 63765.1]},
                'VKEL': {'key': 'device-settings:sim921:temp-slope',
                         'vals': [0, 1e-2]},
                'VOHM': {'key': 'device-settings:sim921:resistance-slope',
                         'vals': [0, 1e-5]},
                'AMAN': {'key': 'device-settings:sim921:output-mode',
                         'vals': {'scaled': '1', 'manual': '0'}},
                'AOUT': {'key': 'device-settings:sim921:manual-vout',
                         'vals': [-10, 10]},
                'ATEM': {'vals': {'resistance': '0', 'temperature': '1'}},
                'CURV': {'key': 'device-settings:sim921:curve-number',
                         'vals': {1: '1', 2: '2', 3: '3'}}
                }


class SIM921Agent(object):
    def __init__(self, port, redis, redis_ts, baudrate=9600, timeout=0.1, initialize=True, mainframe=False):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        # self.connect(raise_errors=False)
        # time.sleep(.5)
        self.redis = redis
        self.redis_ts = redis_ts

        self.prev_sim_settings = {}
        self.new_sim_settings = {}
        self.read_default_settings()

        if initialize:
            self.initialize_sim()

    def connect(self, reconnect=False, raise_errors=True):
        """
        Create serial connection with the SIM921. In reality, the SIM921 connection is only up to the USB-to-RS232
        interface, and so disconnects will need to be checked differently from either side of the converter.
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
        """
        Disconnect from the SIM921 serial connection
        """
        try:
            self.ser.close()
            self.ser = None
        except Exception as e:
            getLogger(__name__).info(f"Exception durring disconnect: {e}")

    def send(self, msg: str, connect=True):
        """
        Send a message to the SIM921 in its desired format.
        The typical message is all caps, terminated with a newline character '\n'
        Commands will be followed by a code, typically a number (e.g. 'RANG 3\n')
        Queries will be followed by a question mark (e.g. 'TVAL?\n')
        The identity query (and a number of other 'special' commands) start with a * (e.g. '*IDN?')
        """
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
        """
        Receiving from the SIM921 consists of reading a line, as some queries may return longer strings than others,
        and each query has its own parsing needs (for example: '*IDN?' returns a string with model, serial number,
        firmware, and company, while 'TVAL?' or 'RVAL?' returns the measured temperature/resistance value at the time)
        """
        try:
            data = self.ser.readline().decode("utf-8").strip()
            getLogger(__name__).debug(f"read {data} from SIM921")
            return data
        except (IOError, SerialException) as e:
            self.disconnect()
            getLogger(__name__).debug(f"Send failed {e}")
            raise e

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
            getLogger(__name__).info(f"Resetting the SIM921!")
            self.send("*RST")
        except IOError as e:
            raise e

    def command(self, command_msg: str):
        """
        A wrapper for the self.send function. This assumes that the command_msg input is a legal command as dictated by
        the manual in picturec/hardware/thermometry/SRS-SIM921-ResistanceBridge-Manual.pdf
        """
        try:
            getLogger(__name__).debug(f"Sending command '{command_msg}' to SIM921")
            self.send(command_msg)
        except IOError as e:
            raise e

    def query(self, query_msg: str):
        """
        A wrapper to both send and receive in one holistic block so that we ensure if a query is sent, and answer is
        received.
        This assumes that the command_msg input is a legal query as dictated by the manual in
        picturec/hardware/thermometry/SRS-SIM921-ResistanceBridge-Manual.pdf
        """
        try:
            getLogger(__name__).debug(f"Querying '{query_msg}' from SIM921")
            self.send(query_msg)
            response = self.receive()
        except Exception as e:
            raise IOError(e)
        return response

    def query_ID(self):
        """
        Specific function to query the SIM921 identity to get its s/n, firmware, and model. Will be used in
        conjunction with store_sim921_id_info to ensure we properly log the .
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
            getLogger(__name__).info(f"SIM921 Identity - model {model}, s/n:{sn}, firmware {firmware}")
        except Exception as e:
            raise ValueError(f"Illegal format. Check communication is working properly: {e}")

        return [model, sn, firmware]

    def read_default_settings(self):
        """
        Reads all of the default SIM921 settings that are stored in the redis database and reads them into the
        dictionaries which the agent will use to command the SIM921 to change settings. Also reads these now current
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

    def initialize_sim(self, load_curve=False):
        """
        Sets all of the values that are read in in the self.read_default_settings() function to their default values.
        TODO: Have this in a manner where it uses the self.new_sim_settings dictionary.
        """
        getLogger(__name__).info(f"Initializing SIM921")

        try:
            self.reset_sim()

            self.set_resistance_range(20e3)
            self.set_excitation_value(100e-6)
            self.set_excitation_mode('voltage')
            self.set_time_constant_value(3)

            self.set_temperature_offset(0.100)
            self.set_temperature_output_scale(1e-2)

            self.set_resistance_offset(19400.5)
            self.set_resistance_output_scale(1e-5)

            self.set_output_manual_voltage(0)
            self.set_output_mode('manual')
            self.set_output_scale_units('resistance')

            if load_curve:
                self._load_calibration_curve(1, 'linear', 'PICTURE-C', '../hardware/thermometry/RX-102A/RX-102A_Mean_Curve.tbl')

            self.choose_calibration_curve(1)

            self.command("DTEM 1")

        except IOError as e:
            getLogger(__name__).debug(f"Initialization failed: {e}")
            raise e
        except RedisError as e:
            getLogger(__name__).debug(f"Redis error occurred in initialization of SIM921: {e}")
            raise e

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

    def set_sim_param(self, command, value):
        try:
            dict_for_command = COMMAND_DICT[command]
        except KeyError as e:
            raise KeyError(f"'{command}' is not a valid SIM921 command! Error: {e}")

        command_key = dict_for_command['key'] if 'key' in dict_for_command.keys() else None
        command_vals = dict_for_command['vals']

        if type(command_vals) is list:
            min_val = command_vals[0]
            max_val = command_vals[1]

            if value < min_val:
                getLogger(__name__).warning(f"Cannot set {command_key} to {value}, it is below the minimum allowed "
                                            f"value! Setting {command_key} to minimum allowed value: {min_val}")
                cmd_value = str(min_val)
            elif value > max_val:
                getLogger(__name__).warning(f"Cannot set {command_key} to {value}, it is above the maximum allowed "
                                            f"value! Setting {command_key} to maximum allowed value: {max_val}")
                cmd_value = str(max_val)
            else:
                getLogger(__name__).info(f"Setting {command_key} to {value}")
                cmd_value = str(value)
        else:
            try:
                cmd_value = command_vals[value]
                getLogger(__name__).info(f"Setting {command_key} to {value}")
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

    def set_resistance_range(self, value):
        try:
            self.set_sim_param("RANG", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_time_constant_value(self, value):
        try:
            self.set_sim_param("TCON", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_excitation_value(self, value):
        try:
            if float(value) == 0:
                self.set_sim_value("EXON", "0")
            else:
                self.set_sim_param("EXON", "1")
            self.set_sim_param("EXCI", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_excitation_mode(self, mode):
        try:
            self.set_sim_param("MODE", mode)
        except (IOError, RedisError) as e:
            raise e

    def set_temperature_offset(self, value):
        try:
            self.set_sim_param("TSET", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_resistance_offset(self, value):
        try:
            self.set_sim_param("RSET", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_temperature_output_scale(self, value):
        try:
            self.set_sim_param("VKEL", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_resistance_output_scale(self, value):
        try:
            self.set_sim_param("VOHM", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_output_scale_units(self, units):
        try:
            self.set_sim_param("ATEM", str(units))
        except (IOError, RedisError) as e:
            raise e

    def set_output_mode(self, mode):
        try:
            self.set_sim_param("AMAN", str(mode))
        except (IOError, RedisError) as e:
            raise e

    def set_output_manual_voltage(self, value):
        try:
            self.set_sim_param("AOUT", float(value))
        except (IOError, RedisError) as e:
            raise e

    def choose_calibration_curve(self, curve):
        LOADED_CURVES = [1]
        if curve in LOADED_CURVES:
            try:
                self.set_sim_param("CURV", int(curve))
            except (IOError, RedisError) as e:
                raise e
        else:
            getLogger(__name__).warning(f"Curve number {curve} has not been loaded into the SIM921. This curve"
                                        f"cannot be used to convert resistance to temperature!")

    def _load_calibration_curve(self, curve_num: int, curve_type, curve_name: str, path_to_curve="../hardware/thermometry/RX-102A/RX-102A_Mean_Curve.tbl"):
        CURVE_NUMBER_KEY = 'device-settings:sim921:curve-number'
        valid_curves = [1, 2, 3]

        CURVE_TYPE_DICT = {'linear': '0',
                           'semilogt': '1',
                           'semilogr': '2',
                           'loglog': '3'}

        if curve_num in valid_curves:
            getLogger(__name__).debug(f"Curve {curve_num} is valid and can be initialized.")
        else:
            getLogger(__name__).warning(f"Curve {curve_num} is NOT valid. Not initializing any curve")
            return False

        if curve_type in CURVE_TYPE_DICT.keys():
            getLogger(__name__).debug(f"Curve type {curve_type} is valid and can be initialized.")
        else:
            getLogger(__name__).warning(f"Curve type {curve_type} is NOT valid. Not initializing any curve")
            return False

        try:
            curve_init_str = "CINI "+str(curve_num)+", "+str(CURVE_TYPE_DICT[curve_type]+", "+curve_name)
            self.command(curve_init_str)
        except IOError as e:
            raise e

        try:
            curve_data = np.loadtxt(path_to_curve)
            temp_data = np.flip(curve_data[:, 0], axis=0)
            res_data = np.flip(curve_data[:, 1], axis=0)
        except Exception:
            raise ValueError(f"{path_to_curve} couldn't be loaded.")

        try:
            for t, r in zip(temp_data, res_data):
                self.command("CAPT"+str(curve_num)+", "+str(r)+", "+str(t))
                time.sleep(0.1)
        except IOError as e:
            raise e

        try:
            store_redis_data(self.redis, {CURVE_NUMBER_KEY: curve_num})
        except RedisError as e:
            raise e

    def _check_settings(self):
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
        key_val_dict = self._check_settings()
        keys = list(key_val_dict.keys())
        try:
            if 'device-settings:sim921:resistance-range' in keys:
                self.set_resistance_range(key_val_dict['device-settings:sim921:resistance-range'])
            if 'device-settings:sim921:excitation-value' in keys:
                self.set_excitation_value(key_val_dict['device-settings:sim921:excitation-value'])
            if 'device-settings:sim921:excitation-mode' in keys:
                self.set_excitation_mode(key_val_dict['device-settings:sim921:excitation-mode'])
            if 'device-settings:sim921:time-constant' in keys:
                self.set_time_constant_value(key_val_dict['device-settings:sim921:time-constant'])
            if 'device-settings:sim921:temp-offset' in keys:
                self.set_temperature_offset(key_val_dict['device-settings:sim921:temp-offset'])
            if 'device-settings:sim921:temp-slope' in keys:
                self.set_temperature_output_scale(key_val_dict['device-settings:sim921:temp-slope'])
            if 'device-settings:sim921:resistance-offset' in keys:
                self.set_resistance_offset(key_val_dict['device-settings:sim921:resistance-offset'])
            if 'device-settings:sim921:resistance-slope' in keys:
                self.set_resistance_output_scale(key_val_dict['device-settings:sim921:resistance-slope'])
            if 'device-settings:sim921:curve-number' in keys:
                self.choose_calibration_curve(key_val_dict['device-settings:sim921:curve-number'])
            if 'device-settings:sim921:manual-vout' in keys:
                self.set_output_manual_voltage(key_val_dict['device-settings:sim921:manual-vout'])
            if 'device-settings:sim921:output-mode' in keys:
                self.set_output_mode(key_val_dict['device-settings:sim921:output-mode'])
        except (IOError, RedisError) as e:
            raise e

        self.prev_sim_settings = self.new_sim_settings

    def read_and_store_thermometry(self):
        try:
            tval = self.query("TVAL?")
            rval = self.query("RVAL?")
            store_redis_ts_data(self.redis_ts, {TEMP_KEY: tval})
            store_redis_ts_data(self.redis_ts, {RES_KEY: rval})
        except IOError as e:
            raise e
        except RedisError as e:
            raise e

    def read_and_store_output(self):
        try:
            output = self.query("AOUT?")
            store_redis_ts_data(self.redis_ts, {OUTPUT_VOLTAGE_KEY: output})
        except IOError as e:
            raise e
        except RedisError as e:
            raise e

    def run(self):
        while True:
            try:
                self.update_sim_settings()
                self.read_and_store_thermometry()
                self.read_and_store_output()
                store_status(self.redis, "OK")
            except IOError as e:
                getLogger(__name__).error(f"IOError occurred in run loop: {e}")
                store_status(self.redis, f"Error {e}")
            except RedisError as e:
                getLogger(__name__).error(f"Error with redis while running: {e}")
                sys.exit(1)


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


def store_sim921_status(redis, status: str):
    redis.set(STATUS_KEY, status)


def store_sim921_id_info(redis, info):
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


if __name__ == "__main__":
    redis = setup_redis()
    redis_ts = setup_redis_ts()

    sim921 = SIM921Agent(port='/dev/sim921', redis=redis, redis_ts=redis_ts, baudrate=9600,
                         timeout=0.1, initialize=True, mainframe=False)

    try:
        getLogger(__name__).info(f"Querying SIM921 for identification information.")
        sim_info = sim921.query_ID()
        store_sim921_id_info(sim_info)
        getLogger(__name__).info(f"Successfully queried {sim_info[0]} (s/n {sim_info[1]}). Firmware is {sim_info[2]}.")
    except IOError as e:
        getLogger(__name__).error(f"Couldn't communicate with SIM921: {e}")
    except ValueError as e:
        getLogger(__name__).error(f"SIM921 returned an invalid value for the ID query: {e}")
    except RedisError as e:
        getLogger(__name__).error(f"Couldn't communicate with Redis to store sim ID information: {e}")
