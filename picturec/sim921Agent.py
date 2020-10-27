"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO: Make sure that when done in mainframe mode, the exit string is sent to the SIM921

TODO: Include the SIM900 mainframe mode (SIM921 is in SIM900 mainframe and we want to connect to it directly. This is
 for development only!! This will not be used on the actual instrument)

TODO: For updating settings, we need to make sure that we're not in violation of the contract we're setting with redis.
 Basically, the new_settings / old_settings should come directly from redis and we can/should not have a software record
 of what those settings are, but rather get them from the instrument itself
 (e.g. ask redis what the desired excitation is, then query the sim921 to see what the excitation is set to, then update
 if they dont match (if changing it automatically would be a pain) or ask redis what the desired excitation is and then
  just send the command. The choice between if we need to ask the sim921 what the value is set to basically boils down
  to how much a pain in the butt it is if you change that setting.)
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

COMMAND_DICT = {'device-settings:sim921:resistance-range': {'command': 'RANG', 'vals': {20e-3: '0', 200e-3: '1', 2: '2', 20: '3', 200: '4', 2e3: '5', 20e3: '6', 200e3: '7', 2e6: '8', 20e6: '9'}},
                'device-settings:sim921:excitation-value': {'command': 'EXCI', 'vals': {0: '-1', 3e-6: '0', 10e-6: '1', 30e-6: '2', 100e-6: '3', 300e-6: '4', 1e-3: '5', 3e-3: '6', 10e-3: '7', 30e-3: '8'}},
                'device-settings:sim921:excitation-mode': {'command': 'MODE', 'vals': {'passive': '0', 'current': '1', 'voltage': '2', 'power': '3'}},
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
        self.cmd = COMMAND_DICT[self.setting]['cmd']
        setting_vals = COMMAND_DICT[self.setting]['vals']

        if isinstance(setting_vals, dict):
            self.mapping = setting_vals
            self.range = None
            mapping_type = type(list(self.mapping.keys())[0])
            if mapping_type == str:
                self.value = str(self.value)
            elif mapping_type == float:
                self.value = float(self.value)
            elif mapping_type == int:
                self.value = int(self.value)
        elif isinstance(setting_vals, list):
            self.range = setting_vals
            self.mapping = None
            self.value = float(self.value)

    def validValue(self):
        """
        TODO For the range parameter, you can either just not set the range (return false) or you could instead set
         it to the end of that range. My inclination is to just return false and let the user know they wanted to set an
         invalid value.
        """
        if self.range is not None:
            return self.range[0] <= self.value <= self.range[1]
        else:
            return self.value in self.mapping.keys()


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



    # def read_default_settings(self):
    #     """
    #     Reads all of the default SIM921 settings that are stored in the redis database and reads them into the
    #     dictionaries which the agent will use to command the SIM921 to change settings.
    #
    #     TODO Isn't this is a violation of the contract with the database? Since this function doesn't actually SET the settings
    #      calling it creates the probability of a mismatch between the database and whatever is loaded into the SIM!
    #
    #     #TODO axe this, at least as a class member
    #
    #     Also reads these now current settings into the redis database.
    #     """
    #     try:
    #         defaults = get_redis_value(self.redis, list(map(DEFAULT_KEY_FACTORY, SETTING_KEYS)))
    #         d = {k: v for k, v in zip(SETTING_KEYS, defaults)}
    #
    #         #self.prev_sim_settings.update(d) # this line isn't actually recording previous settings!
    #         #self.new_sim_settings.update(d) #this line may also break the contract with the control system
    #         #store_redis_data(self.redis,d)  #This line breaks the contract with the control system
    #     except RedisError as e:
    #         raise e

    # def initialize_sim(self, settings, load_curve=False):
    #     """
    #     Sets all of the values that are read in in the self.read_default_settings() function to their default values.
    #     In this instance, self.prev_sim_settings are the values from the default:* keys in the redis db.
    #
    #     TODO When we are operating and for some reason something happens that triggers a reinitialization it
    #      would probably make for smoother operations to load the current settings (which would have been loaded from
    #      defaults at program start).
    #      It would also be nicer for you if things are structured so that you've got a more general API to programatically
    #      set settings without bespoke code e.g.
    #         active_settings = self.get_active_settings()
    #         self.reset_sim()  #This might force a hiccup that isn't always needed
    #         for k,v in active_settings:
    #             self.set_setting(k, v)
    #
    #     Note that during the execution of this function there is a brief window where multiple settings are out of sync
    #     in redis this probably doesn't matter but should be kept in mind as it can create a race condition.
    #     I think tt is possible to lock redis such that anyone that is querying the keys you are in the process of modifying will
    #     block or otherwise get an indication that things are in flux. Thereby preventing another program from doing
    #     something based on a bad inferred state.
    #
    #     here that might look like
    #     lock_redis_keys(defaults.keys())
    #     for k,v in defaults.items(): self.set(k,v)
    #     update_redis(defaults)
    #     self.current_settings.update(defaults)
    #     unlock_redis_keys(defaults.keys())
    #
    #     or
    #     self.set_resistance(defaults['resistance'])
    #     self.current_settings['resistance'])=defaults['resistance']
    #     update_redis('resistance', defaults['resistance'])
    #     repeat with next setting.
    #
    #     The second is much more verbose and makes for more typing.
    #
    #
    #     """
    #     log.info(f"Initializing SIM921")
    #
    #     try:
    #
    #         #move the fetching from redis outside the class
    #         defaults = settings # get_redis_value(self.redis, list(map(DEFAULT_KEY_FACTORY, SETTING_KEYS)), return_dict=True)
    #
    #         self.reset_sim()
    #
    #         self.set_resistance_range(defaults['device-settings:sim921:resistance-range'])
    #         self.set_excitation_value(defaults['device-settings:sim921:excitation-value'])
    #         self.set_excitation_mode(defaults['device-settings:sim921:excitation-mode'])
    #         self.set_time_constant_value(defaults['device-settings:sim921:time-constant'])
    #
    #         self.set_temperature_offset(defaults['device-settings:sim921:temp-offset'])
    #         self.set_temperature_output_scale(defaults['device-settings:sim921:temp-slope'])
    #
    #         self.set_resistance_offset(defaults['device-settings:sim921:resistance-offset'])
    #         self.set_resistance_output_scale(defaults['device-settings:sim921:resistance-slope'])
    #
    #         self.set_output_manual_voltage(defaults['device-settings:sim921:manual-vout'])
    #         self.set_output_mode(defaults['device-settings:sim921:output-mode'])
    #         self.set_output_scale_units(self.scale_units)
    #
    #         if load_curve:
    #             # Loading the curve can and should probably be automated, but at the moment we only have one possible
    #             # curve we can use and so it is more trouble than it is worth to go through not hardcoding it.
    #             self._load_calibration_curve(1, 'linear', 'PICTURE-C', '../hardware/thermometry/RX-102A/RX-102A_Mean_Curve.tbl')
    #
    #         self.choose_calibration_curve(defaults['device-settings:sim921:curve-number'])
    #
    #         self.command("DTEM 1")
    #
    #         self.prev_sim_settings.update(defaults)  # this line isn't actually recording previous settings!
    #         self.new_sim_settings.update(defaults) #this line may also break the contract with the control system
    #         store_redis_data(self.redis,defaults)  #This line breaks the contract with the control system
    #
    #     except IOError as e:
    #         log.debug(f"Initialization failed: {e}")
    #         raise e
    #     except RedisError as e:
    #         log.debug(f"Redis error occurred in initialization of SIM921: {e}")
    #         raise e

#     def choose_calibration_curve(self, curve):
#         """
#         Choose the Resistance-vs-Temperature curve to report temperature. As of July 2020, there is only one possible
#         option that is loaded into channel 1, the LakeShore RX-102-A calibration curve for the thermistor that we have
#         in the PICTURE-C cryostat. Channels 2 and 3 are not 'legal' channels since we have not loaded any calibration
#         curves into them. When we do, LOADED_CURVES should be changed to reflect that so that curve can be used during
#         normal operation.
#         """
#         #TODO this should be be a global
#         # this can also be integrated with minor modification into the command class I defined above by just populating
#         # its mapping with the loaded curves
#         LOADED_CURVES = [1]  # This parameter should probably be updated in redis/somewhere permanent. But the most we
#         # can have is 3 curves on channels 1, 2, or 3. Loaded curves is currently manually set to whichever we have loaded
#         if curve in LOADED_CURVES:
#             try:
#                 self.set_sim_param("CURV", int(curve))
#             except (IOError, RedisError) as e:
#                 raise e
#         else:
#             log.warning(f"Curve number {curve} has not been loaded into the SIM921. This curve"
#                                         f"cannot be used to convert resistance to temperature!")
#
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
#
#     def _check_settings(self):
#         """
#         Reads in the redis database values of the setting keys to self.new_sim_settings and then compares them to
#         those in self.prev_sim_settings. If any of the values are different, it stores the key of the desired value to
#         change as well as the new value. These will be used in self.update_sim_settings() to send the necessary commands
#         to the SIM921 to change any of the necessary settings on the instrument.
#
#         Returns a dictionary where the keys are the redis keys that correspond to the SIM921 settings and the values are
#         the new, desired values to set them to.
#
#         TODO this doesn't really check the settings at all, rather it looks to see if previous and new are
#         """
#         try:
#             for i in self.new_sim_settings.keys():
#                 self.new_sim_settings[i] = get_redis_value(self.redis, i)
#         except RedisError as e:
#             raise e
#
#         changed_idx = []
#         for i,j in enumerate(zip(self.prev_sim_settings.values(), self.new_sim_settings.values())):
#             if str(j[0]) != str(j[1]):
#                 changed_idx.append(True)
#             else:
#                 changed_idx.append(False)
#
#         keysToChange = np.array(list(self.new_sim_settings.keys()))[changed_idx]
#         valsToChange = np.array(list(self.new_sim_settings.values()))[changed_idx]
#
#         return {k: v for k, v in zip(keysToChange, valsToChange)}
#
#     def update_sim_settings(self):
#         """
#
#         TODO A functions specification is an interface. It should be kept independing of other function. I.E.
#          Takes a dictionary of redis keys and values and uses them to update the SIM is great.
#          Takes the output of X and ... is problematic for many of the programming reasons we've talked about.
#
#         TODO Why is this function implicit? just make it take the settings dict, then you can use it and all its
#          validation everywhere (in the vein of my other comments.
#          def update...(self, d, error=True):
#              self._check_settings(d, error=error)
#              try:
#                  self.set_resistance_range(d['device-settings:sim921:resistance-range'])
#              except KeyError:
#                  pass
#              ...
#
#
#         Takes the output of self._check_settings() and sends the appropriate commands to the SIM921 to update the
#         desired settings. Leaves the unchanged settings alone and does not send any commands associated with them.
#
#         After changing all of the necessary settings, self.new_sim_settings is read into self.prev_sim_settings for
#         continuity. This happens each time through the loop so self.prev_sim_settings reflects what the settings were in
#         the previous loop and self.new_sim_settings reflects the desired state.
#         """
#         key_val_dict = self._check_settings()
#         keys = key_val_dict.keys()
#         try:
#             if 'device-settings:sim921:resistance-range' in keys:
#                 self.set_resistance_range(key_val_dict['device-settings:sim921:resistance-range'])
#             if 'device-settings:sim921:excitation-value' in keys:
#                 self.set_excitation_value(key_val_dict['device-settings:sim921:excitation-value'])
#             if 'device-settings:sim921:excitation-mode' in keys:
#                 self.set_excitation_mode(key_val_dict['device-settings:sim921:excitation-mode'])
#             if 'device-settings:sim921:time-constant' in keys:
#                 self.set_time_constant_value(key_val_dict['device-settings:sim921:time-constant'])
#             if 'device-settings:sim921:temp-offset' in keys:
#                 self.set_temperature_offset(key_val_dict['device-settings:sim921:temp-offset'])
#             if 'device-settings:sim921:temp-slope' in keys:
#                 self.set_temperature_output_scale(key_val_dict['device-settings:sim921:temp-slope'])
#             if 'device-settings:sim921:resistance-offset' in keys:
#                 self.set_resistance_offset(key_val_dict['device-settings:sim921:resistance-offset'])
#             if 'device-settings:sim921:resistance-slope' in keys:
#                 self.set_resistance_output_scale(key_val_dict['device-settings:sim921:resistance-slope'])
#             if 'device-settings:sim921:curve-number' in keys:
#                 self.choose_calibration_curve(key_val_dict['device-settings:sim921:curve-number'])
#             if 'device-settings:sim921:manual-vout' in keys:
#                 self.set_output_manual_voltage(key_val_dict['device-settings:sim921:manual-vout'])
#             if 'device-settings:sim921:output-mode' in keys:
#                 self.set_output_mode(key_val_dict['device-settings:sim921:output-mode'])
#         except (IOError, RedisError) as e:
#             raise e
#
#         # Update the self.prev_sim_settings dictionary. Consider doing this in the self.set_...() functions?
#         for i in self.prev_sim_settings.keys():
#             self.prev_sim_settings[i] = self.new_sim_settings[i]
#
#     def read_and_store_thermometry(self):
#         """
#         Query and store the resistance and temperature values at a given time.
#         """
#
#         #TODO dont see a good reason for the dual role function here
#         try:
#             tval = self.query("TVAL?")
#             rval = self.query("RVAL?")
#             store_redis_ts_data(self.redis_ts, {TEMP_KEY: tval})
#             store_redis_ts_data(self.redis_ts, {RES_KEY: rval})
#         except IOError as e:
#             raise e
#         except RedisError as e:
#             raise e
#
#     def read_and_store_output(self):
#         """
#         Query and store the output value from the SIM921 that will go to the SIM960. This is ultimately the signal which
#         will be used to run the PID loop and keep the temperature at 100 mK (or whatever operating temperature we may
#         choose to use). Ultimately, we should be comparing this at some point with what the SIM960 measures at its
#         input to confirm that the expected value is what it is reading.
#         """
#         #TODO dont see a good reason for the dual role function here
#         try:
#             output = self.query("AOUT?")
#             store_redis_ts_data(self.redis_ts, {OUTPUT_VOLTAGE_KEY: output})
#         except IOError as e:
#             raise e
#         except RedisError as e:
#             raise e

#
# def get_redis_value(redis, key):
#     try:
#         val = redis.get(key).decode("utf-8")
#     except RedisError as e:
#         log.error(f"Error accessing {key} from redis: {e}")
#         return None
#     return val
#
#
# def store_sim921_status(redis, status: str):
#     redis.set(STATUS_KEY, status)
#
#
# def store_sim921_id_info(redis, info):
#     redis.set(MODEL_KEY, info[0])
#     redis.set(SN_KEY, info[1])
#     redis.set(FIRMWARE_KEY, info[2])


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


    # """
    # For each loop, update the sim settings if they need to, read and store the thermometry data, read and store the
    # SIM921 output voltage, update the status of the program, and handle any potential errors that may come up.
    # """

    store_temp_res_func = lambda x: redis.store({TEMP_KEY: x['temperature'], RES_KEY: x['resistance']}, timeseries=True)
    sim921.monitor_temp(QUERY_INTERVAL, value_callback=store_temp_res_func)

    store_voltage_func = lambda x: redis.store({OUTPUT_VOLTAGE_KEY: x}, timeseries=True)
    sim921.monitor_output_voltage(QUERY_INTERVAL, value_callback=store_voltage_func)

    # NOTE: The following block is likely unnecessary BUT I have it in here as a safeguard because
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
                print(f"Key: {key} / Val: {val}")
                cmd = SimCommand(key, val)
                if cmd.validValue():
                    log.info(f'Here we would send the command "{cmd.cmd} {cmd.value}\\n"')
                else:
                    log.warning(f'Not a valid value. Can\'t send "{cmd.cmd} {cmd.value}\\n"')
        except RedisError as e:
            log.critical(f"Redis server error! {e}")

    # while True:
    #     try:
    #         #A rought over simplification of what we talked about:
    #         settings = redis.read(list_of_all_setting_keys, return_dict=True)
    #         sim921.set_settings(settings)
    #         redis.store(sim921.read_thermometry(return_setting_dict=True), timeseries=True)
    #         redis.store(sim921.read_output(return_setting_dict=True), timeseries=True)
    #         redis.store((STATUS_KEY, "OK"), timeseries=False)
    #     except IOError as e:
    #         log.error(f"IOError occurred in run loop: {e}")
    #         redis.store((STATUS_KEY, f"Error {e}"))
    #     except RedisError as e:
    #         log.critical(f"Error with redis while running: {e}")
    #         sys.exit(1)