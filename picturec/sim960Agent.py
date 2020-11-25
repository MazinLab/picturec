"""
Author: Noah Swimmer, 21 July 2020

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

# TODO: Split ramp and PID control?
def ramp(sim, ramp_rate=0.005, soak_time=60, quick=False, max_current=9.4, **kwargs):
    """
    :param sim: SIM960 object. Responsible for controlling the ramp and pid control
    :param ramp_rate: <Float> Ramp up/down rate in A/s. Cannot exceed a magnitude of 5 mA/s.
    :param soak_time: <Float> Soak time in minutes. Time at the top of the ramp.
    :param quick: <Bool> Creates a quick ramp. Overrides soak time
    :return: None

    The ramp function is the first part of temperature control for the PICTURE-C MKID Camera. The desire is to properly
    manage the increasing, maintaining, and decreasing of current through the magnet and appropriately communicating
    with the proper fiducial devices (heat switch and current monitor primarily, SIM921 is not as important here) in
    order to configure the ADR to be ready to regulate the temperature on the device stage.

    The goal of the ramp is to steadily increase the current through the ADR to it's maximum value. This max value is
    9.4 A, but can be configured to be lower. Nominally, the magnet will soak for 1 hour, but that can be increased or
    decreased as desired. Longer soak times can help increase the hold time, and shorter ramps will decrease it. The
    ramp will operate by manually increasing the output voltage that the SIM960 sends to the high-current boost board
    slowly so that the current through the magnet doesn't increase more than 5 mA/s. While this is going on, the agent
    should be querying the current from the currentduino to make sure that it doesn't drop precipitously out of nowhere.
    That would mean the magnet has gone normal and the SIM960 must drop the voltage down to 0 immediately so as to not
    try to put any more current through the magnet (immediately is operative - as fast as possible is probably more
    feasible logistically).

    During the ramp, the agent must also be able to communicate to the heatswitch that it needs to open. During the ramp
    up and soaking phases, the heat switch should be closed so that the salt pills in the ADR can reach thermal
    equilibrium with the LN2 bath that they are in (or with the 4K stage of the pulse tube, if the pulse tube is
    installed). Just before the ramp down begins from soaking the magnet, the heat switch needs to be opened so that the
    salt pills are no longer in thermal contact with the 4K bath.

    Over the course of the ramp, it must be ensured that the output mode of the SIM960 stays 'manual' and does not
    change to 'PID'. The 'PID' control should only ever be changed after the ramp has concluded and we desire to start
    regulating the temperature. Otherwise, there are no settings that cannot be changed within the SIM960. It is not
    recommended to try to reconfigure the internal setpoint, P, I, and D values, but that should not be restricted, just
    done with caution and thought. The manual output voltage is required to change, as that is what controls the current
    value through the ADR.

    The value of current reported by the SIM960 will only be the output voltage times a conversion factor, and the
    method for determining if the magnet has quenched (gone normal) is if the currentduinoAgent (ArudinoUNO measuring
    the output current from the high-current boost board) reports a sudden drop in current.

    The ramp operates by creating a list of voltage values to feed to the SIM960 to fit the parameters of the ramp
    specified when the function is called. If any of the parameters are unnacceptable, they will default to the most
    extreme allowable level (max current, ramp rate, etc.) and notify the user that their choice was too high.
    """
    pass

# TODO: See note with ramp(). Should ramp and PID be separate?
def pid_control(control_temp=0.100, p=-16.0, i=.2, d=0, **kwargs):
    """
    PID control works after the ramp has terminated. With the setup of the system, it would not work to run the PID if
    the ramp hadn't been run before (it is predicated on the idea that increasing current through the ADR increases the
    temperature of the device stage and vise versa). If the PID control is started and the temperature is greater than
    the desired regulation temperature and the current in the magnet is 0, the PID control won't be able to run (there's
    no current to decrease to decrease the device stage). For these reasons, we need to be able to monitor the
    temperature of the device stage and the current through the magnet.

    During the PID control portion of the temperature regulation, it will be necessary to have control over the state of
    the SIM960 output, meaning it's necessary to be able to flip from manual to PID control or the other way around.
    This is because we control the magnet output current by providing voltage to the high current boost board, and in
    normal operation, PID control output will suffice, but in the case of a quench or termination of control before the
    magnet runs out (no longer observing, etc.) we want to be able to drop the current to 0 reliably.

    We must also ensure that the proper control signal is being sent to the SIM960 from the SIM921. Typically, the
    SIM921 will attempt to send 0V to the SIM960 input monitor, but during PID regulation the error signal (scaled
    output) is required for PID to properly run, and so the PID control is responsible for letting the SIM921 know when
    it needs to change its output mode.

    Because we are dealing with changing environments (on the balloon) it's also necessary to be able to change/enable
    the P, I, and D values so that the PID control loop can be tuned during operation (e.g. decreasing P if the signal
    starts oscillating). In that vein, it's still necessary to keep P, I, D values safe and in valid ranges. This means
    enforcing the allowable values from the SIM960 manual and ensuring the polarity (which is defined by the
    architecture of the control loop) is not changed. NOTE: The polarity should only ever change if the control loop
    goes through a major overhaul and the thermometry/temperature control/ADR is massively changed.

    Additionally, in the case it is desirable to raise/lower the temperature of the device during regulation (e.g. 90 mK
    is normal operating temperature and we want 100 mK for this run), that should be a configurable parameter. This
    involves telling the SIM921 that a new resistance/temperature offset is needed. Because the SIM921 outputs the
    conditioned signal from the device thermometer, this is the parameter that must be changed for temperature
    regulation. Since the output is scaled to the resistance measurement deviation (not temperature), a change in
    desired control temperature must be converted to the corresponding resistance value.

    Values that should not change during PID control are the upper and lower output limits, the setpoint mode (always
    should be internal), and the setpoint ramping parameters (enabling setpoint ramping and the ramping rate).
    :return: None
    """
    pass

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
                    redis.store({STATUS_KEY: f"Error {e}"})
                    log.error(f"Some error communicating with the SIM960! {e}")
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)
