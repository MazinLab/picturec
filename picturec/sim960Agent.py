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


def prepare_magnet():
    """
    BEFORE READING: Some of the information contained here might not be strictly under the purview of the sim960Agent.
    This docstring is purely to describe the process and expected operation of the magnet preparation.

    Prepare magnet describes the process of preparing the magnet (ADR) so it is properly ready for temperature
    regulation of the MKID device stage.

    First, prepare_magnet should be initialized with a few parameters.
    :param Ramp Rate: <float> The rate in A/s at which the current should be increased and decreased in the ADR
    :param Soak Time: <float|int> The duration at which the maximum current will be sent through the ADR
    :param Max Current: <float|int> The maximum current value that the magnet should attain.
    With these parameters come a few caveats, namely that Ramp Rate and Max Current are both values that need to be
    selected carefully so as not to damage the ADR. When initializing the ramp, the first check should be to make sure
    these values are not exceeded. For the ramp rate, that is dI/dt > 5 mA/s (0.005 A/s) and for max current, it is
    I > 9.4 A (note: We usually call this 10 A, but it is not a full 10 A).

    In order to prepare the magnet for PID control, a coordinated set of processes must be run so that the magnet
    current is increased up to the maximum value, soaks at that maximum value, and then is decreased back down to 0 A.

    In addition to performing these tasks, preparing the magnet properly also means that the heatswitch (which thermally
    connects the ADR to the LN2 tank) most be opened prior to decreasing the current from the max value back to 0 A.

    What the prepare_magnet process will look like is as follows:
    check_magnet_prep_parameters()
    increase_current_to_max()
    soak_magnet_at_max_current()
    open_heat_switch()
    decrease_current_to_zero()

    During this process, the device stage temperature should be monitored as always. Before the heat switch is opened,
    it should remain around the temperature of LN2 (still in thermal contact) and after the heat switch is opened it
    should fall below the LN2 temperature all the way down to below the operating temperature of the device*.

    It is also important to monitor the current through the magnet for 2 reasons. The first is to ensure that it is
    progressing as expected (smoothly increasing up to max, maintaining the max current with little noise, smoothly
    decreasing down) and the second is to ensure that a quench has not occurred.

    In the case of a quench (which would manifest as a sudden, sharp decrease in current), it is necessary to instantly
    (or as close to instantly as possible) reduce the current being pushed through the ADR to 0. A quench occurs when
    the superconducting magnet 'goes normal', which is to say it is no longer superconducting.
    TODO: (Just so this is specifically highlighted) A QUENCH IS POTENTIALLY THE MOST DAMAGING FAILURE THAT CAN OCCUR
     DURING MAGNET OPERATION.

    This will necessitate 1 of 2 things:
    1) Proper monitoring to account for a quench in any situation in the SIM960 agent
    2) A 'watcher' thread in the prepare_magnet() function.
    Either way, a function is needed (1 is more powerful and is realistically the best choice):
    monitor_for_and_handle_quench()

    What changes during the magnet preparation?:
    - The output voltage from the SIM960 that controls the high current boost board output
    - The position of the heatswitch (controlled by the currentduinoAgent)

    What does not (cannot) change during the magnet preparation?:
    - The output mode from the SIM960 <manual|PID> must remain manual
    - PID polarity <negative|positive> must remain negative (this can never change outside of massive structural change
     in the cryostat/readout)
    - Setpoint reference mode <internal|external> must remain internal (this can never change unless the electronics
     rack is completely changed so that an external reference voltage is added)

    What is prepare_magnet() agnostic to? (Note: just because it won't hurt the magnet preparation does not mean that it
    is wise or recommended to change these values):
    - PID control setpoint reference value
    - P, I, D values
    - Setpoint ramp value (how fast the setpoint changes)
    - Setpoint ramp enabling (does the setpoint value slew or 'jump')
    """
    pass


def check_magnet_prep_parameters():
    """
    A function that assesses the parameters selected for the ADR preparation and ensures that they are safe to use and
    will not cause damage to the magnet.

    The parameters that will be assessed are ramp_rate, max_current, and soak_time. These are discussed below

    :param ramp_rate: The ramp rate is the rate at which current will be increased/decreased in the magnet during the
    prepare_magnet() process. From experience using this magnet and the manual, the highest safe value to use is 5 mA/s.
    If a higher value than that is requested, it has two choices, set the ramp_rate to the highest allowable value or
    cause the prepare_magnet() process to fail and report/warn the user that they requested a dangerous value.
    NOTE: The ramp_rate holds for increasing the current AND decreasing, the only difference is that in the decreasing
    step, it will be negative.

    :param max_current: The max current is the highest current the magnet will attain during the prepare_magnet()
    process. Physically this is limited by the voltage (which controls the current via the high current boost board) the
    SIM960 is capable of outputting (10 V). However, the magnet can also only safely have a certain current flowing
    through it. This value is 9.4 A. If a higher value is requested, two choices can be made: set the max_current to its
    highest allowable value or cause prepare_magnet() to fail and report/warn the user that they requested a dangerous
    value.

    :param soak_time: This value will not damage the magnet, but for practical reasons should be checked. If the soak
    time is <1 hour the hold time at base temperature risks being drastically decreased. If the soak time is >4 hours
    it will no longer lead to an increase in hold time and simply becomes a waste of power. Within 1-4 hours, the hold
    time will increase proportionally with the soak time, although 1 hour has been shown to be more than enough for a
    night of observing. For these reasons, a check on soak_time is warranted and any value that are short (<1 hour) or
    quite long (>4 hours) should be reported to the user (give them an 'Are you sure?' message).

    One potential idea is to have a configuration parameter that says 'change_to_safe_values'. If true, then the program
    could report an unsafe value selection and modify it accordingly. If false, the program would not modify the values
    and fail instead, so that the magnet preparation is not able to go on.
    :return:
    """
    pass


def ramp():
    """
    A general function which will allow the SIM960 agent to iteratively update its output voltage (and thus the current
    through the ADR).

    The ramp function specified here can increase, hold, or decrease the current in the magnet by modifying (or holding)
    the voltage value output from the SIM960. This is the 'base' of increase_current_to_max(),
    soak_magnet_at_max_current(), and decrease_current_to_zero().

    It can be configured by giving it the starting value, the desired value, and the ramp rate. It needs to be smart
    enough to determine from the endpoints the direction of the ramp (e.g. start=5 A, stop=0 A, rate=1 A/s should
    recognize the need for a negative slope and handle it gracefully).

    As this will get called after the check_magnet_prep_parameters(), it is assumed that the ramp will progress at a
    safe level.

    The way that this will function is by creating a list of values of voltages to send to the SIM960 device for it to
    output. With that list of voltages, the SIM960 will iteratively command the voltage to change at an appropriate
    interval.
    NOTE: Since the ramp rates are going to be given in A/s, updating the value once per second is the natural choice
    for this. At the same time, since the SIM960 has a .001 V resolution on its output and the highest ramp value that
    is safe is .005 A/s, you could in principle increase the voltage by .001 V, 5 times per second for the same result
    (with a 1 A/V conversion between output voltage and resulting current)

    See prepare_magnet() for what DOES change, what CANNOT change, and what MAY change (but doesn't have to) during
    the use of this function (since the bulk of of prepare_magnet is ramping, those values hold within the ramp
    function). This behavior of what does/does not/may change is inherited by increase_...(), soak_...(), and
    decrease_...() processes.
    :return:
    """
    pass


def increase_current_to_max():
    """
    Increase_current_to_max() is a specific use case of the ramp function. It is assumed that it will start at 0 A and
    increase until it has reached the maximum specified current value.

    This is used as the first piece of the magnet preparation. It encompasses the ramping of the magnet from 0 A to its
    maximum value, then hands off its responsibility to soak_magnet_at_max_current().

    This is the stage at which it is most likely for the magnet to quench! Because the current is steadily increasing,
    a spike in current that is too great may cause a quench. For that reason, the monitoring program must be 'extra-
    vigilant' (it should always be the same amount vigilant, but for emphasis here) in regards to the state of the
    magnet current.
    :return:
    """
    pass


def soak_magnet_at_max_current():
    """
    soak_magnet_at_max_current is another specific use case of the ramp function. In this case, it will hold the magnet
    at the max current value once increase_current_to_max() has ramped up the current value sufficiently. In this case
    we are running a ramp with slope=0 (essentially I=const).

    It should try to dynamically (using continual updates) keep the current at its max value (within reason, a few mA
    of drift over the duration of the soak is not a problem) rather than statically (setting the value once and then
    just waiting around for a set time).

    A quench is unlikely at this stage, but it is important here to be monitoring the SIM960, high current boost board,
    and magnet itself since there is a HUGE current being pushed through it.

    After completing the soak, the heat switch for the ADR must be flipped from closed to an open position. It remains
    to be decided who/what agent is responsible for that, which leaves three options:
    1) SIM960Agent reports 'Soak Done!' via redis pubsub, and that triggers the currentduino to flip the heatswitch. In
    the meantime, the SIM960 agent is monitoring the heat switch position and once it is open, can progress further.
    2) The SIM960Agent sends a message saying 'soak done!' up the ladder to a higher level program. That coordinating
    program then sends a message to the currentduino to open. Once the 'coordinator' is aware that the switch was opened
    it tells the SIM960 as much, and it can proceed.

    Once the soak is completed, open_heat_switch() is run.
    :return:
    """
    pass


def open_heat_switch():
    """
    See discussion in soak_magnet_at_max_current().

    After the soak duration is reached, the heatswitch must be opened before the current can be decreased in the magnet.
    If the heatswitch is not opened, then the salt pills (heat sinks) will remain in thermal contact with the LN2 bath
    and the device stage will be unable to drop below that temperature.
    :return:
    """
    pass


def decrease_current_to_zero():
    """
    The inverse of increase_current_to_max(). A specific use case of the ramp function. In this case, it is assumed that
    it will start at the max current value and decrease the current until it has reached 0 A again.
    NOTE: There is a potential option here to smoothly transition to PID regulation once the temperature has reached the
    operating value. It is not clear if this is recommended or useful, but instead of dropping current to 0 A and then
    starting PID control, it may be desirable to - once the device stage is at its operating temperature - just flip
    over to PID control.

    This is the final stage of the magnet preparation before PID control. Afterwards it does not hand over
    responsibility to the next step in the prepare_magnet() process. Optionally, it can log a successful completion of
    the process.

    It is not recommended to drop the value very fast, because to sharp a change in current can cause a quench (even
    when decreasing current in the ADR)! For this reason, still remain extra-vigilant with respect to the possibility
    of a quench during this stage.

    :return:
    """
    pass


def monitor_for_and_handle_quench():
    """
    This process is one that should be running at all times which continually reads the current measured by the high
    current boost board, cache the last few values, and checkup with those values to make sure that a quench has not
    occurred.

    If it is determined that a quench has occurred, it also needs to have the capability to tell the SIM960 to drop
    everything and drop the current to 0 A.

    This is essentially a simple process, compare the most recent current value to the previous. If there is a massive,
    sharp drop between two measured values (especially during an increasing/holding step, but at any point) then it
    reports "A QUENCH HAS HAPPENED" and EVERYTHING must stop what it's doing and handle it.

    What handling a quench entails is (in the PICTURE-C electronics rack configuration), dropping the voltage output
    from the SIM960 to 0 V, which in turn makes it so that the high current boost board is not attempting to drive any
    current through the magnet.

    Because a quench involves the magnet going normal (having a resistance as it stops superconducting) while a huge
    current is being run through it, that will result in heating of the fridge. With this, one of the checks to make
    sure that the magnet has returned to a 'post-quench' state is that the temperatures (especially the device stage and
    LN2 thermometers) have returned to normal.

    However, all that being said: A quench is ultimately damaging enough that it is likely not advisable to try another
    prepare_magnet() cycle until the cryostat is inspected for damage.
    :return:
    """
    pass

def PID_control():
    """
    TODO: Create docstring re: pid control and what must occur during it

    :return:
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
