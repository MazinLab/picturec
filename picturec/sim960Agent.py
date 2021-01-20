"""
Author: Noah Swimmer, 21 July 2020
TODO: Measure output voltage-to-current conversion. Should be ~1 V/A (from the hc boost board)

TODO: MAGNET_CURRENT_KEY - Is this the key for the measured value from HC Board?
"""
import logging
import sys
from collections import defaultdict
from logging import getLogger
import time
from datetime import datetime, timedelta
import threading
from transitions import MachineError, State, Transition
from transitions.extensions import LockedMachine

import picturec.util as util
from picturec.devices import SIM960, SimCommand, MagnetState
import picturec.pcredis as redis
from picturec.pcredis import RedisError
import picturec.currentduinoAgent as heatswitch


DEVICE = '/dev/sim960'
STATEFILE = ''
REDIS_DB = 0


#'device-settings:sim960:mode',  #TODO remove from schema
#'device-settings:sim960:vout-value', #TODO remove from schema

#TODO these 4 settings don't really follow the schema pattern that is used below as they do want discovery but don't
# control the device directly
RAMP_SLOPE_KEY = 'device-settings:sim960:ramp-rate'#TODO
DERAMP_SLOPE_KEY = 'device-settings:sim960:deramp-rate' #TODO
SOAK_TIME_KEY = 'device-settings:sim960:soak-time'#TODO
SOAK_CURRENT_KEY = 'device-settings:sim960:soak-current'#TODO
STATEFILE_PATH_KEY = 'device-settings:sim960:statefile'

# TODO (NS Response 1/19) SETTING_KEYS has been updated in devices.py -> COMMANDS960.
#  It's time for an overhaul doc of the updated schema. A lot has changed since last updated.
SETTING_KEYS = ['device-settings:sim960:vout-min-limit',
                'device-settings:sim960:vout-max-limit',
                'device-settings:sim960:pid-p:enabled',
                'device-settings:sim960:pid-i:enabled',
                'device-settings:sim960:pid-d:enabled',
                'device-settings:sim960:pid-p:value',
                'device-settings:sim960:pid-i:value',
                'device-settings:sim960:pid-d:value',
                'device-settings:sim960:vin-setpoint-mode',
                'device-settings:sim960:vin-setpoint',
                'device-settings:sim960:vin-setpoint-slew-rate',
                'device-settings:sim960:vin-setpoint-slew-enable']


OUTPUT_VOLTAGE_KEY = 'status:device:sim960:hcfet-control-voltage'  # Set by 'MOUT' in manual mode, monitored by 'OMON?' always
INPUT_VOLTAGE_KEY = 'status:device:sim960:sim921-vout'  # This is the output from the sim921 to the sim960 for PID control #TODO update in the schema
MAGNET_CURRENT_KEY = 'status:device:sim960:current-setpoint'  # To get the current from the sim960. We will need to run a calibration #TODO update in the schema
# test to figure out what the output voltage to current conversion is.
MAGNET_STATE_KEY = 'status:magnet:state'  # OFF | RAMPING | SOAKING | QUENCH (DON'T QUENCH!)

STATUS_KEY = 'status:device:sim960:status'
MODEL_KEY = 'status:device:sim960:model'
FIRMWARE_KEY = 'status:device:sim960:firmware'
SN_KEY = 'status:device:sim960:sn'


TS_KEYS = [OUTPUT_VOLTAGE_KEY, INPUT_VOLTAGE_KEY, MAGNET_CURRENT_KEY, MAGNET_STATE_KEY]


QUERY_INTERVAL = 10

COLD_AT_CMD = 'command:be-cold-at'#TODO
COLD_NOW_CMD = 'command:get-cold'#TODO
ABORT_CMD = 'command:abort-cooldown'#TODO
QUENCH_KEY = 'event:quenching'#TODO

DEVICE_TEMP_KEY = 'status:device:array:temperature:value'  #TODO ( currently 'status:temps:mkidarray:temp'. Which is better )
MAX_REGULATE_TEMP = .5 #TODO NS: probably want this to be 100 mK +/- a few mK. ('A few' depends on our control level)

COMMAND_KEYS = (COLD_AT_CMD, COLD_NOW_CMD, ABORT_CMD)


log = logging.getLogger(__name__)


class StateError(Exception):
    pass

class Foo:
    count=0
    def current_at_soak(self, e):
        if self.count>0:
            raise ValueError
        self.count+=1
        return True

foo = Foo()
transitions = [
    # stay in ramping, increasing the current a bit each time unless the current is high enough
    # if we can't increment the current or get the current th
    {'trigger': 'next', 'source': 'ramping', 'dest': None, 'unless': 'current_at_soak',
     'after': 'increment_current'},
    {'trigger': 'next', 'source': 'ramping', 'dest': 'soaking', 'conditions': 'current_at_soak'},

]
from transitions import Machine, State
machine = Machine(foo, states=( State('off'), State('ramping'), State('soaking')),
                       transitions=transitions, initial='ramping', send_event=True)



def write_persisted_state(statefile, state):
    try:
        with open(statefile, 'w') as f:
            f.write(f'{time.time()}: {state}')
    except IOError:
        getLogger(__name__).warning('Unable to log state entry', exc_info=True)


def load_persisted_state(statefile):
    try:
        with open(statefile, 'r') as f:
            persisted_state_time, persisted_state = f.readline().split(':')
    except Exception:
        persisted_state_time, persisted_state = None, None
    return persisted_state_time, persisted_state


def monitor_callback(iv, ov, oc):
    d = {k: v for k, v in zip((INPUT_VOLTAGE_KEY, OUTPUT_VOLTAGE_KEY, MAGNET_CURRENT_KEY), (iv, ov, oc))
         if v is not None}  # NB 'if is not None' - > so we don't store bad data
    try:
        redis.store(d, timeseries=True)
    except RedisError:
        getLogger(__name__).warning('Storing magnet status to redis failed')


class MagnetController(LockedMachine):
    LOOP_INTERVAL = 1
    BLOCKS = defaultdict(set)  # TODO This holds the sim960 commands that are blocked out in a given state i.e.
                                 #  'regulating':('device-settings:sim960:setpoint-mode',)

    def __init__(self, statefile='./magnetstate.txt'):
        transitions = [
            #Allow aborting from any point, trigger will always succeed
            {'trigger': 'abort', 'source': '*', 'dest': 'deramping'},

            # Allow quench (direct to hard off) from any point, trigger will always succeed
            {'trigger': 'quench', 'source': '*', 'dest': 'off'},

            # Allow starting a ramp from off or deramping, if close_heatswitch fails then start should fail
            {'trigger': 'start', 'source': 'off', 'dest': 'hs_closing', 'prepare': 'close_heatswitch'},
            {'trigger': 'start', 'source': 'deramping', 'dest': 'hs_closing', 'prepare': 'close_heatswitch'},
            # {'trigger': 'start', 'source': 'cooling', 'dest': 'hs_closing', 'prepare': 'close_heatswitch'},
            # {'trigger': 'start', 'source': 'regulating', 'dest': 'hs_closing', 'prepare': 'close_heatswitch'},
            # {'trigger': 'start', 'source': 'soak', 'dest': 'hs_closing', 'prepare': 'close_heatswitch'},

            # Transitions for cooldown progression

            # stay in hs_closing until it is closed then transition to ramping
            # if we can't get the status from redis then the conditions default to false and we stay put
            {'trigger': 'next', 'source': 'hs_closing', 'dest': 'ramping', 'conditions': 'heatswitch_closed'},
            {'trigger': 'next', 'source': 'hs_closing', 'dest': None},

            # stay in ramping, increasing the current a bit each time unless the current is high enough to soak
            # if we can't increment the current or get the current then IOErrors will arise and we stay put
            # if we can't get the settings from redis then the conditions default to false and we stay put
            {'trigger': 'next', 'source': 'ramping', 'dest': None, 'unless': 'current_at_soak',
             'after': 'increment_current'},
            {'trigger': 'next', 'source': 'ramping', 'dest': 'soaking', 'conditions': 'current_at_soak'},

            # stay in soaking until we've elapsed the soak time, if the current changes move to deramping as something
            # is quite wrong, when elapsed command heatswitch open and move to waiting on the heatswitch
            # if we can't get the current then conditions raise IOerrors and we will deramp
            # if we can't get the settings from redis then the conditions default to false and we stay put
            # Note that the hs_opening command will always complete (even if it fails) so the state will progress
            {'trigger': 'next', 'source': 'soaking', 'dest': None, 'unless': 'soak_time_expired',
             'conditions': 'current_at_soak'},
            {'trigger': 'next', 'source': 'soaking', 'dest': 'hs_opening', 'prepare': 'open_heatswitch',
             'conditions': ('current_at_soak', 'soak_time_expired')},  #condition repeated to preclude call passing due to IO hiccup
            {'trigger': 'next', 'source': 'soaking', 'dest': 'deramping'},

            # stay in hs_opening until it is open then transition to cooling
            # don't require conditions on current
            # if we can't get the status from redis then the conditions default to false and we stay put
            {'trigger': 'next', 'source': 'hs_opening', 'dest': 'cooling', 'conditions': 'heatswitch_opened'},
            {'trigger': 'next', 'source': 'hs_opening', 'dest': None},

            # stay in cooling, decreasing the current a bit until the device is regulatable
            # if the heatswitch closes move to deramping
            # if we can't change the current or interact with redis for related settings the its a noop and we
            #  stay put
            # if we can't put the device in pid mode (IOError)  we stay put
            {'trigger': 'next', 'source': 'cooling', 'dest': None, 'unless': 'device_regulatable',
             'after': 'decrement_current', 'conditions': 'heatswitch_opened'},
            {'trigger': 'next', 'source': 'cooling', 'dest': 'regulating', 'before': 'to_pid_mode',
             'conditions': 'heatswitch_opened'},
            {'trigger': 'next', 'source': 'cooling', 'dest': 'deramping', 'conditions': 'heatswitch_closed'},

            # stay in regulating until the device is too warm to regulate
            # if it somehow leaves PID mode (or we can't verify it is in PID mode: IOError) move to deramping
            # if we cant pull the temp from redis then device is assumed unregulatable and we move to deramping
            # TODO (having not yet gone past here): Is not being able to pull temp immediately unregulateable or can it be tried a few times?
            {'trigger': 'next', 'source': 'regulating', 'dest': None, 'conditions': ['device_regulatable', 'in_pid_mode']},  # TODO
            {'trigger': 'next', 'source': 'regulating', 'dest': 'deramping'},

            # stay in deramping, trying to decrement the current, until the device is off then move to off
            # condition defaults to false in the even of an IOError and decrement_current will just noop if there are
            # failures
            {'trigger': 'next', 'source': 'deramping', 'dest': None, 'unless': 'current_off',
             'after': 'decrement_current'},
            {'trigger': 'next', 'source': 'deramping', 'dest': 'off'},

            #once off stay put, if the current gets turned on while in off then something is fundamentally wrong with
            # the sim itself. This can't happen.
            {'trigger': 'next', 'source': 'off', 'dest': None}
        ]

        states = (# Entering off MUST succeed
                  State('off', on_enter=['record_entry', 'kill_current']),
                  State('hs_closing', on_enter='record_entry'),
                  State('ramping', on_enter='record_entry'),
                  State('soaking', on_enter='record_entry'),
                  State('hs_opening', on_enter='record_entry'),
                  State('cooling', on_enter='record_entry'),
                  State('regulating', on_enter='record_entry'),
                  # Entering ramping MUST succeed
                  State('deramping', on_enter='record_entry'))

        sim = SIM960(port=DEVICE, baudrate=9600, timeout=0.1, initializer=self.initialize_sim)
        # NB If the settings are manufacturer defaults then the sim960 had a major upset, generally initialize_sim
        # will not be called

        # Kick off a thread to run forever and just log data into redis
        # TODO bundling these into a sim960.monitor_values if necessary to simplify redundant serial comm.
        sim.monitor(QUERY_INTERVAL, (sim.input_voltage, sim.output_voltage, sim.setpoint),
                    value_callback=monitor_callback)

        self.statefile = statefile
        self.sim = sim
        self.lock = lock = threading.RLock() # TODO: Is this a typo/not done?
        self.scheduled_cooldown = None
        self._run = False  # Set to false to kill the main loop
        self._main = None

        initial = self.compute_initial_state()
        self.state_entry_time = {initial: time.time()}
        LockedMachine.__init__(self, transitions=transitions, initial=initial, states=states, machine_context=self.lock)

        if sim.initialized_at_last_connect:
            self.firmware_pull()
            self.set_redis_settings(init_blocked=False)  #allow IO and Redis errors to shut things down.

        self.start_main()

    def initialize_sim(self):
        """
        Callback run on connection to the sim whenever it is not initialized. This will only happen if the sim loses all
        of its settings, which should never every happen. Any settings applied take immediate effect
        """
        self.firmware_pull()
        try:
            self.set_redis_settings(init_blocked=True) # If called the sim is in a blank state and needs everything!
        except (RedisError, KeyError) as e:
            raise IOError(e) #we can't initialize!

    def firmware_pull(self):
        # Grab and store device info
        try:
            info = self.sim.device_info
            d = {FIRMWARE_KEY: info['firmware'], MODEL_KEY: info['model'], SN_KEY: info['sn']}
        except IOError as e:
            log.error(f"When checking device info: {e}")
            d = {FIRMWARE_KEY: '', MODEL_KEY: '', SN_KEY: ''}

        try:
            redis.store(d)
        except RedisError:
            log.warning('Storing device info to redis failed')

    def set_redis_settings(self, init_blocked=False):
        """may raise IOError, if so sim can be in a partially configured state"""
        try:
            settings_to_load = redis.read(SETTING_KEYS, error_missing=True)
        except RedisError:
            log.critical('Unable to pull settings from redis to initialize sim960')
            raise
        except KeyError as e:
            log.critical('Unable to pull setting {e} from redis to initialize sim960')
            raise

        blocks = self.BLOCKS[self.state]
        blocked_init = blocks.intersection(settings_to_load.keys())

        current_settings = {}
        if blocked_init:
            if init_blocked:
                # TODO: Error below?
                log.warning(f'Initializing {"\n\t".join(blocked_init)}\n\t  despite being blocked by current state.')
            else:
                log.warning(f'Skipping settings {"\n\t".join(blocked_init)}\n  as they are blocked by current state.')
                settings_to_load = {k: v for k, v in settings_to_load if k not in blocks}
                current_settings = self.sim.read_schema_settings(blocked_init)  #keep redis in sync

        initialized_settings = self.sim.apply_schema_settings(settings_to_load)
        initialized_settings.update(current_settings)
        try:
            redis.store(initialized_settings)
        except RedisError:
            log.warning('Storing device settings to redis failed')

    def compute_initial_state(self):
        initial_state = 'deramping'  #always safe to start here
        try:
            if self.sim.initialized_at_last_connect:
                mag_state = self.sim.mode
                if mag_state == MagnetState.PID:
                    initial_state = 'regulating'  # NB if HS wrong device won't stay cold and we'll transition to deramping
                else:
                    initial_state = load_persisted_state(self.statefile)
                    current = self.sim.setpoint
                    if initial_state == 'soaking' and current != float(redis.read(SOAK_CURRENT_KEY)):
                        initial_state = 'ramping'  # we can recover

                    # be sure the command is sent
                    if initial_state in ('hs_closing',):
                        heatswitch.close()

                    if initial_state in ('hs_opening',):
                        heatswitch.open()

                    # failure cases
                    if ((initial_state in ('ramping', 'soaking') and heatswitch.is_opened()) or
                            (initial_state in ('cooling',) and heatswitch.is_closed()) or
                            (initial_state in ('off', 'regulating'))):
                        initial_state = 'deramping'  # deramp to off, we are out of sync with the hardware

        except IOError:
            getLogger(__name__).critical('Lost sim960 connection during agent startup. defaulting to deramping')
            initial_state = 'deramping'
        except RedisError:
            getLogger(__name__).critical('Lost redis connection during compute_initial_state startup.')
            raise
        return initial_state

    def start_main(self):
        self._run = True  # Set to false to kill the m
        self._main = threading.Thread(target=self.main)
        self._main.daemon = True
        self._main.start()

    def main(self):
        while self._run:
            try:
                self.machine.next()
            except IOError:
                getLogger(__name__).info(exc_info=True)
            except MachineError:
                getLogger(__name__).info(exc_info=True)
            except RedisError:
                getLogger(__name__).info(exc_info=True)
            finally:
                time.sleep(self.LOOP_INTERVAL)

    @property
    def min_time_until_cool(self):
        """return an estimate of the time to cool from the current state """
        # TODO (Updated) MATH:
        #   If RAMPING -> Time = ((SOAK_CURRENT - CURRENT_CURRENT)/ RAMP_RATE) + SOAK_TIME + (SOAK_CURRENT / DERAMP_RATE)
        #   If SOAKING -> Time = TIME_LEFT_IN_SOAK + (SOAK_CURRENT / DERAMP_RATE)
        #   If DERAMPING -> Time = CURRENT_CURRENT / DERAMP_RATE
        #  Note: This doesn't take into account (1) Heatswitch failures/time to close (2) Starting regulation on the tail of deramping
        return timedelta(minutes=30)


    def schedule_cooldown(self, time):
        """time specifies the time by which to be cold"""
        # TODO how to handle scheduling when we are warming up or other such
        if self.state not in ('off', 'deramping'):
            raise ValueError(f'Cooldown in progress, abort before scheduling.')

        now = datetime.now()
        time_needed = self.min_time_until_cool

        if time < now + time_needed:
            raise ValueError(f'Time travel not possible, specify a time at least {time_needed} in the future')

        self.cancel_scheduled_cooldown()
        t = threading.Timer(time - time_needed - now, self.start) # TODO (For JB): self.start?
        self.scheduled_cooldown = (time - time_needed, t)
        t.daemon = True
        t.start()

    def cancel_scheduled_cooldown(self):
        if self.scheduled_cooldown is not None:
            getLogger(__name__).info(f'Cancelling cooldown scheduled for {self.scheduled_cooldown[0]}')
            self.scheduled_cooldown[1].cancel()
            self.scheduled_cooldown = None
        else:
            getLogger(__name__).debug(f'No pending cooldown to cancel')

    @property
    def status(self):
        """A string indicating the current status e.g. state[, Cooldown scheduled for X] """
        ret = self.machine.state
        if ret not in ('off', 'regulating'):
            ret += f", cold in {self.min_time_until_cool} minutes"
        if self.scheduled_cooldown is not None:
            ret += f', cooldown scheduled for {self.scheduled_cooldown[0]}'
        return ret

    def close_heatswitch(self, event):
        heatswitch.close()

    def open_heatswitch(self, event):
        try:
            heatswitch.open()
        except RedisError:
            pass

    def current_off(self, event):
        try:
            return self.sim.mode==MagnetState.MANUAL and self.sim.setpoint==0
        except IOError:
            return False

    def heatswitch_closed(self, event):
        """return true iff heatswitch is closed"""
        try:
            return heatswitch.is_closed()
        except RedisError:
            return False

    def heatswitch_opened(self, event):
        """return true iff heatswitch is closed"""
        try:
            return heatswitch.is_opened()
        except RedisError:
            return False

    def increment_current(self, event):
        limit = self.sim.MAX_CURRENT_SLOPE
        interval = self.LOOP_INTERVAL
        try:
            slope = abs(float(redis.read([RAMP_SLOPE_KEY])))
        except RedisError:
            getLogger(__name__).warning(f'Unable to pull {RAMP_SLOPE_KEY} using {limit}.')
            slope = limit

        if slope > self.sim.MAX_CURRENT_SLOPE:
            getLogger(__name__).info(f'{RAMP_SLOPE_KEY} too high, overwriting.')
            try:
                redis.store(RAMP_SLOPE_KEY, limit)
            except RedisError:
                getLogger(__name__).info(f'Overwriting failed.')

        if not slope:
            getLogger(__name__).warning('Ramp slope set to zero, this will take eternity.')

        try:
            self.sim.setpoint += slope * interval
        except IOError:
            getLogger(__name__).warning('Failed to increment current, sim offline')

    def decrement_current(self, event):
        limit = self.sim.MAX_CURRENT_SLOPE
        interval = self.LOOP_INTERVAL # No need to do this faster than increment current.
        try:
            slope = abs(float(redis.read(DERAMP_SLOPE_KEY)))
        except RedisError:
            getLogger(__name__).warning(f'Unable to pull {DERAMP_SLOPE_KEY} using {limit}.')
            slope = limit

        if slope > self.sim.MAX_CURRENT_SLOPE:
            getLogger(__name__).info(f'{DERAMP_SLOPE_KEY} too high, overwriting.')
            try:
                redis.store(DERAMP_SLOPE_KEY, limit)
            except RedisError:
                getLogger(__name__).info(f'Overwriting failed.')

        if not slope:
            getLogger(__name__).warning('Deramp slope set to zero, this will take eternity.')

        try:
            self.sim.setpoint -= slope * interval
        except IOError:
            getLogger(__name__).warning('Failed to decrement current, sim offline')

    def soak_time_expired(self, event):
        try:
            return (time.time() - self.state_entry_time['soaking']) >= float(redis.read(SOAK_TIME_KEY))
        except RedisError:
            return False

    def current_at_soak(self, event):
        try:
            return self.sim.setpoint >= redis.read(SOAK_CURRENT_KEY)
        except RedisError:
            return False

    def in_pid_mode(self, event):
        return self.sim.mode == MagnetState.PID

    def to_pid_mode(self, event):
        self.sim.mode = MagnetState.PID

    def device_regulatable(self, event):
        #TODO
        try:
            return float(redis.read([DEVICE_TEMP_KEY])) <= MAX_REGULATE_TEMP
        except RedisError:
            return False

    def kill_current(self, event):
        """Kill the current if possible, return False if fail"""
        try:
            self.sim.kill_current()
            return True
        except IOError:
            return False

    def sim_command(self, cmd):
        """ Directly execute a SimCommand if if possible. May raise IOError or StateError"""
        with self.lock:
            if cmd.setting in self.BLOCKS.get(self.state, tuple()):
                msg = f'Command {cmd} not supported while in state {self.state}'
                getLogger(__name__).error(msg)
                raise StateError(msg)
            self.sim.send(cmd)

    def record_entry(self, event):
        self.state_entry_time[self.state] = time.time()
        write_persisted_state(self.statefile, self.state)


if __name__ == "__main__":

    util.setup_logging()
    redis.setup_redis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)
    controller = MagnetController(statefile=redis.read(STATEFILE_PATH_KEY))

    # main loop, listen for commands and handle them
    try:
        while True:
            for key, val in redis.listen(SETTING_KEYS + COMMAND_KEYS + (QUENCH_KEY,)):
                if key in SETTING_KEYS:
                    try:
                        cmd = SimCommand(key, val)
                        controller.sim_command(cmd)
                    except (IOError, StateError):
                        pass
                    except ValueError:
                        getLogger(__name__).warning(f"Ignoring invalid command ('{key}={val}'): {e}")
                # NB I'm disinclined to include forced state overrides but they would go here
                elif key == ABORT_CMD:
                    # abort any cooldown in progress, warm up, and turn things off
                    # e.g. last command before heading to bed
                    controller.deramp()
                elif key == QUENCH_KEY:
                    controller.quench()
                elif key == COLD_AT_CMD:
                    try:
                        controller.schedule_cooldown(datetime.fromtimestamp(float(val)))
                    except ValueError as e:
                        getLogger(__name__).error(e)
                elif key == COLD_NOW_CMD:
                    try:
                        controller.start()
                    except MachineError:
                        getLogger(__name__).info('Cooldown already in progress', exc_info=True)
                else:
                    getLogger(__name__).info(f'Ignoring {key}:{val}')

                redis.store(STATUS_KEY, controller.status)

    except RedisError as e:
        getLogger(__name__).critical(f"Redis server error! {e}", exc_info=True)
        # TODO insert something to suppress the concomitant redis monitor thread errors that will spam logs?
        controller.deramp()

        try:
            while not controller.is_off():
                getLogger(__name__).info(f'Waiting (10s) for magnet to deramp from ({controller.sim.setpoint}) before exiting...')
                time.sleep(10)
        except IOError:
            pass
        sys.exit(1)