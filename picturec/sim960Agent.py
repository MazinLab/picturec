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
import logging
import sys
from picturec.pcredis import PCRedis, RedisError
import picturec.util as util
from picturec.devices import SIM960, SimCommand

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

log = logging.getLogger(__name__)


if __name__ == "__main__":

    util.setup_logging()

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)

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
            settings_to_load = {setting.lstrip('default:'): value for setting, value in settings_to_load.items()}
            initialized_settings = sim.initialize_sim(settings_to_load)
            redis.store(initialized_settings)  # TODO JB Exception handling
        except IOError:
            raise
        except RedisError:
            sys.exit(1)
        except KeyError:
            sys.exit(1)

    sim = SIM960(port=DEVICE, baudrate=9600, timeout=0.1, connection_callback=initialize)

    # ---------------------------------- MAIN OPERATION (The eternal loop) BELOW HERE ----------------------------------
    OUTPUT_TO_CURRENT_FACTOR = 1  # V/A (TODO: Measure this value)
    def callback(iv, ov):
        d = {}
        for k, val in zip((INPUT_VOLTAGE_KEY, OUTPUT_VOLTAGE_KEY), (iv, ov)):
            if val is not None:  # TODO JB: Since we don't want to store bad data
                d[k] = val
        if OUTPUT_VOLTAGE_KEY in d:
            d[MAGNET_CURRENT_KEY] = d[OUTPUT_VOLTAGE_KEY]*OUTPUT_TO_CURRENT_FACTOR
        redis.store(d, timeseries=True)
    sim.monitor(QUERY_INTERVAL, (sim.input_voltage, sim.output_voltage), value_callback=callback)
    #  TODO: Figure out where to add in magnet state checking (in the sim960 or elsewhere?)

    while True:
        try:
            for key, val in redis.listen(SETTING_KEYS):
                log.debug(f"sim960agent received {key}, {val}. Trying to send a command.")
                cmd = SimCommand(key, val)
                if cmd.valid_value():
                    try:
                        log.info(f'Sending command "{cmd.escaped}"')
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
