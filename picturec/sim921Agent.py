"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO: Make sure that when done in mainframe mode, the exit string is sent to the SIM921

TODO describe the different mods of operation of this program and what they are. "Mainframe?!"

TODO:
 Why not
  self.redis.get_redis_value(key: (str, tuple, list)) and
  self.redis.set_redis_value(setting: (dict, tuple or list of 2-tuples))
  instead of e.g.
    for k, v in pairs: store_redis_data(self.redis,k,v )


  new_sim_settings and prev_sim_settings give me pause. redis is the source of this why have another record here?

"""

import serial
import numpy as np
from logging import getLogger
from serial import SerialException
import time
from redis import Redis, RedisError
from redistimeseries.client import Client
import sys
from picturec.agent import SerialAgent


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


#TODO seeing this like this I'm leaning towards the convention DEFAULT_KEY='default:'+KEY. Then you can just have a
# factory and never worry about making changes twice.
# DEFAULT_KEY_FACTORY = lambda key: f'default:{key}'
# default = redis.get(DEFAULT_KEY_FACTORY(key))
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


class SIM921Agent(agent.SerialAgent):
    def __init__(self, port, baudrate=9600, timeout=0.1,
                 scale_units='resistance', connect_mainframe=False, mainframe_args=(2, 'xyz')):

        super().__init__super().__init__(port, baudrate, timeout, name='Sim921')

        self.scale_units = scale_units

        self.prev_sim_settings = {}
        self.new_sim_settings = {}

        self.connect(raise_errors=False)  # Moveded after initialization of all instance members

        if connect_mainframe:
            self.mainframe_connect(*mainframe_args)

        #note I deleted the redis initialization stuff, pull that out of the class, at least for now

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

        TODO I don't see a point in having this function abstracting send().
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
        Specific function to query the SIM921 identity to get its s/n, firmware, and model.

        #TODO IMO docstrings aren't the best place for commentary on intent when the function can stand alone.
        Will be used in conjunction with store_sim921_id_info to ensure we properly log the .
        """
        try:
            idn_msg = self.query("*IDN?")
        except IOError as e:
            raise e

        try:
            idn_info = idn_msg.split(',')
            model, sn, firmware = idn_info[1:4]
            getLogger(__name__).info(f"SIM921 Identity - model {model}, s/n:{sn}, firmware {firmware}")
        except Exception as e:
            raise ValueError(f"Illegal format. Check communication is working properly: {e}")

        return model, sn, firmware

    def read_default_settings(self):
        """
        Reads all of the default SIM921 settings that are stored in the redis database and reads them into the
        dictionaries which the agent will use to command the SIM921 to change settings.

        TODO Isn't this is a violation of the contract with the database? Since this function doesn't actually SET the settings
         calling it creates the probability of a mismatch between the database and whatever is loaded into the SIM!

        #TODO axe this, at least as a class member

        Also reads these now current settings into the redis database.
        """
        try:
            defaults = get_redis_value(self.redis, list(map(DEFAULT_KEY_FACTORY, SETTING_KEYS)))
            d = {k: v for k, v in zip(SETTING_KEYS, defaults)}

            #self.prev_sim_settings.update(d) # this line isn't actually recording previous settings!
            #self.new_sim_settings.update(d) #this line may also break the contract with the control system
            #store_redis_data(self.redis,d)  #This line breaks the contract with the control system
        except RedisError as e:
            raise e

    def initialize_sim(self, settings, load_curve=False):
        """
        Sets all of the values that are read in in the self.read_default_settings() function to their default values.
        In this instance, self.prev_sim_settings are the values from the default:* keys in the redis db.

        TODO When we are operating and for some reason something happens that triggers a reinitialization it
         would probably make for smoother operations to load the current settings (which would have been loaded from
         defaults at program start).
         It would also be nicer for you if things are structured so that you've got a more general API to programatically
         set settings without bespoke code e.g.
            active_settings = self.get_active_settings()
            self.reset_sim()  #This might force a hiccup that isn't always needed
            for k,v in active_settings:
                self.set_setting(k, v)

        Note that during the execution of this function there is a brief window where multiple settings are out of sync
        in redis this probably doesn't matter but should be kept in mind as it can create a race condition.
        I think tt is possible to lock redis such that anyone that is querying the keys you are in the process of modifying will
        block or otherwise get an indication that things are in flux. Thereby preventing another program from doing
        something based on a bad inferred state.

        here that might look like
        lock_redis_keys(defaults.keys())
        for k,v in defaults.items(): self.set(k,v)
        update_redis(defaults)
        self.current_settings.update(defaults)
        unlock_redis_keys(defaults.keys())

        or
        self.set_resistance(defaults['resistance'])
        self.current_settings['resistance'])=defaults['resistance']
        update_redis('resistance', defaults['resistance'])
        repeat with next setting.

        The second is much more verbose and makes for more typing.


        """
        getLogger(__name__).info(f"Initializing SIM921")

        try:

            #move the fetching from redis outside the class
            defaults = settings # get_redis_value(self.redis, list(map(DEFAULT_KEY_FACTORY, SETTING_KEYS)), return_dict=True)

            self.reset_sim()

            self.set_resistance_range(defaults['device-settings:sim921:resistance-range'])
            self.set_excitation_value(defaults['device-settings:sim921:excitation-value'])
            self.set_excitation_mode(defaults['device-settings:sim921:excitation-mode'])
            self.set_time_constant_value(defaults['device-settings:sim921:time-constant'])

            self.set_temperature_offset(defaults['device-settings:sim921:temp-offset'])
            self.set_temperature_output_scale(defaults['device-settings:sim921:temp-slope'])

            self.set_resistance_offset(defaults['device-settings:sim921:resistance-offset'])
            self.set_resistance_output_scale(defaults['device-settings:sim921:resistance-slope'])

            self.set_output_manual_voltage(defaults['device-settings:sim921:manual-vout'])
            self.set_output_mode(defaults['device-settings:sim921:output-mode'])
            self.set_output_scale_units(self.scale_units)

            if load_curve:
                # Loading the curve can and should probably be automated, but at the moment we only have one possible
                # curve we can use and so it is more trouble than it is worth to go through not hardcoding it.
                self._load_calibration_curve(1, 'linear', 'PICTURE-C', '../hardware/thermometry/RX-102A/RX-102A_Mean_Curve.tbl')

            self.choose_calibration_curve(defaults['device-settings:sim921:curve-number'])

            self.command("DTEM 1")

            self.prev_sim_settings.update(defaults)  # this line isn't actually recording previous settings!
            self.new_sim_settings.update(defaults) #this line may also break the contract with the control system
            store_redis_data(self.redis,defaults)  #This line breaks the contract with the control system

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

        TODO I'd axe this function, rolling it and set_sim_param into one
        """
        set_string = setting + " " + value
        try:
            self.command(f"{setting} {value}")
        except IOError as e:
            raise e

    def set_sim_param(self, command, value):
        """
        Takes a given command from the SIM921 manual (the top level key in the COMMAND_DICT) and uses the keys/vals
        in the dictionary value for that command to determine if legal values are being sent to the SIM921. If all of
        the rules for a given command are properly met, sends that command to the SIM921 for the value to be changed.


        """
        try:
            dict_for_command = COMMAND_DICT[command]
        except KeyError as e:
            raise KeyError(f"'{command}' is not a valid SIM921 command! Error: {e}")

        command_key = dict_for_command['key'] if 'key' in dict_for_command.keys() else None
        command_vals = dict_for_command['vals']

        #TODO this can be simplified by making the command a small class
        class SimCommand:
            def __init__(self, redis_setting, command, mapping=None, range=None):
                if mapping is None and range is None:
                    raise ValueError('Mapping dict or range tuple required')
                self.mapping = mapping
                self.setting = redis_setting
                self.simcommand = command
                self.range = range

            def validValue(self, value):
                if self.range is not None:
                    return self.range[0] <=value <=self.range[1]
                else:
                    return value in self.mapping

            def translate(self, value):
                if not self.validValue(value):
                    raise ValueError(f"'{value}' is not allowed for {self.command}")
                return value if self.mapping is None else self.mapping[value]

        #then all the below could be simplified to
        cmd = COMMAND_DICT[command]  #TODO note that youve' indexed this dictionary on the SIM's command format and not the redis setting
        # while that works it means that all the code in this agent isn't generalizable to other agents without much more editing.
        self.set_sim_value(cmd.simcommand, cmd.translate(value))

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
            if command_key is not None:   #TODO doesn't this if imply that the above set_sim_value could be executed on a null command?!
                store_redis_data(self.redis, {command_key: value})
        except IOError as e:
            raise e
        except RedisError as e:
            raise e

    #TODO in general I would advocate replacing all of these set_blah/get_blah with getters and setters
    # e.g. @resistance_range.setter & @property or by using __getattr__() __setattr__().
    # The latter two allow for more compact code but do have implications for extension of this class via subclassing
    # and attribute resolution order (or the common parts that might be pulled into a parent class)


    def set_resistance_range(self, value):
        try:
            self.set_sim_param("RANG", float(value))
        except (IOError, RedisError) as e:
            #TODO since you aren't doing anything with the error there is no need
            # for the try except block!
            raise e

    def set_time_constant_value(self, value):
        try:
            self.set_sim_param("TCON", float(value))
        except (IOError, RedisError) as e:
            raise e

    def set_excitation_value(self, value):
        #TODO YIKES!
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
            self.set_sim_param("MODE", str(mode))
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
        """
        Choose the Resistance-vs-Temperature curve to report temperature. As of July 2020, there is only one possible
        option that is loaded into channel 1, the LakeShore RX-102-A calibration curve for the thermistor that we have
        in the PICTURE-C cryostat. Channels 2 and 3 are not 'legal' channels since we have not loaded any calibration
        curves into them. When we do, LOADED_CURVES should be changed to reflect that so that curve can be used during
        normal operation.
        """
        #TODO this should be be a global
        # this can also be integrated with minor modification into the command class I defined above by just populating
        # its mapping with the loaded curves
        LOADED_CURVES = [1]  # This parameter should probably be updated in redis/somewhere permanent. But the most we
        # can have is 3 curves on channels 1, 2, or 3. Loaded curves is currently manually set to whichever we have loaded
        if curve in LOADED_CURVES:
            try:
                self.set_sim_param("CURV", int(curve))
            except (IOError, RedisError) as e:
                raise e
        else:
            getLogger(__name__).warning(f"Curve number {curve} has not been loaded into the SIM921. This curve"
                                        f"cannot be used to convert resistance to temperature!")

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
        if file is None:
            #TODO use package resources and a curve to resource name golbal dict to lookup the path do that it works
            # with pip installation.
            # e.g. (needs refining)
            import pkg_resources as pkg
            CURVE_DICT = {'RX-102A_Mean_Curve':'RX-102A_Mean_Curve.tbl'}
            path_to_curve = pkg.resource_filename('hardware/thermometry/RX-102A', CURVE_DICT[curve_name])
        else:
            path_to_curve = file

        #All three of these things look like globals or things that should be programmatically generated
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
        """
        Reads in the redis database values of the setting keys to self.new_sim_settings and then compares them to
        those in self.prev_sim_settings. If any of the values are different, it stores the key of the desired value to
        change as well as the new value. These will be used in self.update_sim_settings() to send the necessary commands
        to the SIM921 to change any of the necessary settings on the instrument.

        Returns a dictionary where the keys are the redis keys that correspond to the SIM921 settings and the values are
        the new, desired values to set them to.

        TODO this doesn't really check the settings at all, rather it looks to see if previous and new are
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

        TODO A functions specification is an interface. It should be kept independing of other function. I.E.
         Takes a dictionary of redis keys and values and uses them to update the SIM is great.
         Takes the output of X and ... is problematic for many of the programming reasons we've talked about.

        TODO Why is this function implicit? just make it take the settings dict, then you can use it and all its
         validation everywhere (in the vein of my other comments.
         def update...(self, d, error=True):
             self._check_settings(d, error=error)
             try:
                 self.set_resistance_range(d['device-settings:sim921:resistance-range'])
             except KeyError:
                 pass
             ...


        Takes the output of self._check_settings() and sends the appropriate commands to the SIM921 to update the
        desired settings. Leaves the unchanged settings alone and does not send any commands associated with them.

        After changing all of the necessary settings, self.new_sim_settings is read into self.prev_sim_settings for
        continuity. This happens each time through the loop so self.prev_sim_settings reflects what the settings were in
        the previous loop and self.new_sim_settings reflects the desired state.
        """
        key_val_dict = self._check_settings()
        keys = key_val_dict.keys()
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

        # Update the self.prev_sim_settings dictionary. Consider doing this in the self.set_...() functions?
        for i in self.prev_sim_settings.keys():
            self.prev_sim_settings[i] = self.new_sim_settings[i]

    def read_and_store_thermometry(self):
        """
        Query and store the resistance and temperature values at a given time.
        """

        #TODO dont see a good reason for the dual role function here
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
        """
        Query and store the output value from the SIM921 that will go to the SIM960. This is ultimately the signal which
        will be used to run the PID loop and keep the temperature at 100 mK (or whatever operating temperature we may
        choose to use). Ultimately, we should be comparing this at some point with what the SIM960 measures at its
        input to confirm that the expected value is what it is reading.
        """
        #TODO dont see a good reason for the dual role function here
        try:
            output = self.query("AOUT?")
            store_redis_ts_data(self.redis_ts, {OUTPUT_VOLTAGE_KEY: output})
        except IOError as e:
            raise e
        except RedisError as e:
            raise e


    def mainframe_connect(self, arg1, arg2):  #TODO make these argument names informative
        self.send(f'CONN {arg1}, {arg2}')

    def mainframe_disconnect(self, args):
        self.send(f'{args[2]}')




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

from picturec.redis import PCRedis

if __name__ == "__main__":

    # TODO Try to make agent's mirror as much as possible. For instance hempttemp does logging, then the class,
    #  then redis, then runs a main loop. Here the orders are juggled with no obvious reason and the main loop is
    #  internal to the class. this also means this agent now must have a redis instance whereas the other agent doesn't.
    #  I think there are merits to both approaches but for now consistency is key.

    redis = PCRedis(host='localhost', port=6379, db=0, create_ts_keys=TS_KEYS)

    sim921 = SIM921Agent(port='/dev/sim921', baudrate=9600, timeout=0.1)

    try:
        getLogger(__name__).info(f"Querying SIM921 for identification information.")
        sim_info = sim921.query_ID()
        store_sim921_id_info(redis, sim_info)  #TODO is this run on every reconnect?
        getLogger(__name__).info(f"Successfully queried {sim_info[0]} (s/n {sim_info[1]}). Firmware is {sim_info[2]}.")
    except IOError as e:
        getLogger(__name__).error(f"Couldn't communicate with SIM921: {e}")
    except ValueError as e:
        getLogger(__name__).error(f"SIM921 returned an invalid value for the ID query: {e}")
    except RedisError as e:
        getLogger(__name__).error(f"Couldn't communicate with Redis to store sim ID information: {e}")

    #TODO are any of the above errors critical/fatal? If they occur should we be continuing?

    # """
    # For each loop, update the sim settings if they need to, read and store the thermometry data, read and store the
    # SIM921 output voltage, update the status of the program, and handle any potential errors that may come up.
    # """
    while True:
        try:

            #A rought over simplification of what we talked about:
            settings = redis.read(list_of_all_setting_keys, return_dict=True)
            sim921.set_settings(settings)
            redis.store(sim921.read_thermometry(return_setting_dict=True), timeseries=True)
            redis.store(sim921.read_output(return_setting_dict=True), timeseries=True)
            redis.store((STATUS_KEY, "OK"), timeseries=False)
        except IOError as e:
            getLogger(__name__).error(f"IOError occurred in run loop: {e}")
            redis.store((STATUS_KEY, f"Error {e}"))
        except RedisError as e:
            getLogger(__name__).critical(f"Error with redis while running: {e}")
            sys.exit(1)