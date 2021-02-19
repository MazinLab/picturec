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
from picturec.devices import SIM921, SimCommand, SIM921OutputMode
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


def firmware_pull(sim):
    # Grab and store device info
    try:
        info = sim.device_info
        d = {FIRMWARE_KEY: info['firmware'], MODEL_KEY: info['model'], SN_KEY: info['sn']}
    except IOError as e:
        log.error(f"When checking device info: {e}")
        d = {FIRMWARE_KEY: '', MODEL_KEY: '', SN_KEY: ''}

    try:
        redis.store(d)
    except RedisError:
        log.warning('Storing device info to redis failed')


def initializer(sim):
    """
    Callback run on connection to the sim whenever it is not initialized. This will only happen if the sim loses all
    of its settings, which should never every happen. Any settings applied take immediate effect
    """
    firmware_pull(sim)
    try:
        settings_to_load = redis.read(SETTING_KEYS, error_missing=True)
        initialized_settings = sim.apply_schema_settings(settings_to_load)
    except RedisError as e:
        log.critical('Unable to pull settings from redis to initialize sim960')
        raise IOError(e)
    except KeyError as e:
        log.critical('Unable to pull setting {e} from redis to initialize sim960')
        raise IOError(e)

    try:
        redis.store(initialized_settings)
    except RedisError:
        log.warning('Storing device settings to redis failed')


if __name__ == "__main__":

    util.setup_logging('sim921Agent')
    redis = PCRedis(create_ts_keys=TS_KEYS)
    sim = SIM921(port=DEVICE, timeout=0.1, initializer=initializer)

    # ---------------------------------- MAIN OPERATION (The eternal loop) BELOW HERE ----------------------------------
    def callback(t, r, v):
        # Since we don't want to store bad data
        d = {k: x for k, x in zip((TEMP_KEY, RES_KEY, OUTPUT_VOLTAGE_KEY), (t, r, v)) if x is not None}
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
                    redis.store({STATUS_KEY: f"Error {e}"})  # todo jb: didnt we decide that we could just write the error to the schema key?
                    log.error(f"Comm error: {e}")

        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)
