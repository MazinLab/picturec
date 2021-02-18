"""
Author: Noah Swimmer, 8 July 2020

Program for communicating with and controlling the SIM921 AC resistance bridge. The primary function of the SIM921
is monitoring the temperature of the thermometer on the MKID device stage in the PICTURE-C cryostat. It is also
responsible for properly conditioning its output signal so that the SIM960 (PID Controller) can properly regulate
the device temperature.

TODO JB: Add value caching? (self.output_mode = 'manual', self.curve_number = 1)

TODO NS: Add 'resetting' to last stable state to SIM960
"""
import logging
import sys
from picturec.pcredis import PCRedis, RedisError
import picturec.util as util
from picturec.devices import SIM921, SimCommand
import picturec.pcredis


DEVICE = '/dev/sim921'
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


TEMP_KEY = 'status:temps:mkidarray:temp'
RES_KEY = 'status:temps:mkidarray:resistance'
OUTPUT_VOLTAGE_KEY = 'status:device:sim921:sim960-vout'
TS_KEYS = [TEMP_KEY, RES_KEY, OUTPUT_VOLTAGE_KEY]


STATUS_KEY = 'status:device:sim921:status'
MODEL_KEY = 'status:device:sim921:model'
FIRMWARE_KEY = 'status:device:sim921:firmware'
SN_KEY = 'status:device:sim921:sn'

log = logging.getLogger(__name__)


class SIM921OutputMode:
    SCALED = 'scaled'
    MANUAL = 'manual'


def to_scaled_output():
    picturec.pcredis.publish('device-settings:sim921:output-mode', SIM921OutputMode.SCALED, store=False)


def to_manual_output():
    picturec.pcredis.publish('device-settings:sim921:output-mode', SIM921OutputMode.MANUAL, store=False)


def in_scaled_output():
    return picturec.pcredis.read('device-settings:sim921:output-mode',
                                 return_dict=False)[0] == SIM921OutputMode.SCALED


def in_manual_output():
    return picturec.pcredis.read('device-settings:sim921:output-mode',
                                 return_dict=False)[0] == SIM921OutputMode.MANUAL


if __name__ == "__main__":

    util.setup_logging('sim921Agent')
    redis = PCRedis(create_ts_keys=TS_KEYS)


    # TODO JB The whole point of not erroring out on a connection failure in __init__ is to allow
    #  execution to start without the device online. These calls here in main, that are required for operation,
    #  completely defeats that
    def initialize(sim):
        try:
            info = sim.device_info
            redis.store({FIRMWARE_KEY: info['firmware'], MODEL_KEY: info['model'], SN_KEY: info['sn']})
        except IOError as e:
            log.error(f"When checking device info: {e}")
            redis.store({FIRMWARE_KEY: '', MODEL_KEY: '', SN_KEY: ''})
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)

        from_state = 'defaults'
        keys = SETTING_KEYS if from_state.lower() in ('previous', 'last_state', 'last') else DEFAULT_SETTING_KEYS
        try:
            settings_to_load = redis.read(keys, error_missing=True)
            # settings_to_load = {setting.lstrip('default:'): value for setting, value in settings_to_load.items()}
            if from_state == 'defaults':
                settings_to_load = {setting[8:]: value for setting, value in settings_to_load.items()}
            initialized_settings = sim.initialize_sim(settings_to_load)
            redis.store(initialized_settings)  # TODO JB Exception handling
        except IOError:
            raise
        except RedisError:
            sys.exit(1)
        except KeyError:
            sys.exit(1)

    sim = SIM921(port=DEVICE, timeout=0.1, connection_callback=initialize)


    # ---------------------------------- MAIN OPERATION (The eternal loop) BELOW HERE ----------------------------------
    def callback(t, r, v):
        d = {}
        for k, val in zip((TEMP_KEY, RES_KEY, OUTPUT_VOLTAGE_KEY), (t, r, v)):
            if val is not None:  # TODO JB: Since we don't want to store bad data
                d[k] = val
        redis.store(d, timeseries=True)
    sim.monitor(QUERY_INTERVAL, (sim.temp, sim.resistance, sim.output_voltage), value_callback=callback)

    while True:
        try:
            for key, val in redis.listen(SETTING_KEYS):
                log.debug(f"sim921agent received {key}, {val}. Trying to send a command.")
                try:
                    cmd = SimCommand(key, val)
                except ValueError as e:
                    log.warning(f"Ignoring invalid command ('{key}={val}'): {e}")
                    continue
                try:
                    log.info(f"Processing command '{cmd}'")
                    sim.send(cmd.sim_string)
                    redis.store({cmd.setting: cmd.value})
                    redis.store({STATUS_KEY: "OK"})
                except IOError as e:
                    redis.store({STATUS_KEY: f"Error {e}"}) # todo jb: didnt we decide that we could just write the error to the schema key?
                    log.error(f"Comm error: {e}")

        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)
