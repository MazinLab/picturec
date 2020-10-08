"""
Author: Noah Swimmer 15 June 2020

Program to control ArduinoUNO that will measure the current through the ADR magnet by monitoring the
current-sensing resistor on the PIPER-designed HighCurrent Boost board (see picturec reference folder
for circuit drawing). Will log values to redis, will also act as a safeguard to tell the magnet current
control that the current is operating out of normal bounds.
NOTE: Redis/redistimeseries MUST be set up for the currentduino to work.

TODO: Add ability to compare current value from high current board ('status:highcurrentboard:current') to that
 of the magnet ('status:magnet:current') from the SIM960 - NOTE: This feels like higher level management

TODO: Test the heat switch touch signals to have an open/closed monitor (in lab)
"""

import sys
import time
import logging
import threading
from picturec.pcredis import PCRedis, RedisError
import picturec.agent as agent

REDIS_DB = 0
QUERY_INTERVAL = 1
LOOP_INTERVAL = .001


KEYS = ['device-settings:currentduino:highcurrentboard',
        'device-settings:currentduino:heatswitch',
        'status:magnet:current',
        'status:heatswitch',
        'status:highcurrentboard:powered',
        'status:highcurrentboard:current']


STATUS_KEY = "status:device:currentduino:status"
FIRMWARE_KEY = "status:device:currentduino:firmware"
HEATSWITCH_STATUS_KEY = 'status:heatswitch'
HEATSWITCH_MOVE_KEY = 'device-settings:currentduino:heatswitch'


R1 = 11790  # Values for R1 resistor in magnet current measuring voltage divider
R2 = 11690  # Values for R2 resistor in magnet current measuring voltage divider

log = logging.getLogger(__name__)


class Currentduino(agent.SerialAgent):
    VALID_FIRMWARES = [0.0, 0.1, 0.2]

    def __init__(self, port, baudrate=115200, timeout=0.1, connect=True):
        super().__init__(port, baudrate, timeout, name='currentduino')
        if connect:
            self.connect(raise_errors=False, post_connect_sleep=2)
        self.heat_switch_position = None
        self._monitor_thread = None
        self.monitor_state = 'Not Started'
        self.last_current = None
        self.terminator = ''

    def read_current(self):
        """Read and return the current, may raise ValueError (unparseable response) or IOError (something else)"""
        response = self.query('?', connect=True)
        try:
            value = float(response.split(' ')[0])
            current = (value * (5.0 / 1023.0) * ((R1 + R2) / R2))
        except ValueError:
            raise ValueError(f"Couldn't parse '{response}' into a float")
        return current

    def format_msg(self, msg: str):
        return f"{msg.strip().lower()}"

    def move_heat_switch(self, pos):
        pos = pos.lower()
        if pos not in ('open', 'close'):
            raise ValueError(f"'{pos} is not a vaild (open, close) heat switch position")

        # NB it is mighty convenient that the serial command/confirmation and pos start with the same letter
        try:
            log.info(f"Commanding heat switch to {pos}")
            confirm = self.query(pos[0], connect=True)
            if confirm == pos[0]:
                log.info(f"Command accepted")
            else:
                log.info(f"Command failed: '{confirm}'")
            return pos if confirm == pos[0] else 'unknown'
        except Exception as e:
            raise IOError(e)

    def firmware_ok(self):
        return self.firmware in self.VALID_FIRMWARES

    @property
    def firmware(self):
        """ Return the firmware string or raise IOError"""
        try:
            log.debug(f"Querying currentduino firmware")
            response = self.query("v", connect=True)
            v, _, version = response.partition(" ")
            version = float(version)
            if v != "v":
                raise ValueError('Bad format')
            return version
        except IOError as e:
            log.error(f"Serial error: {e}")
            raise e
        except ValueError:
            log.error(f"Bad firmware format: '{response}'")
            raise IOError(f'Bad firmware response: "{response}"')

    def monitor_current(self, interval):

        def f():
            try:
                self.last_current = currentduino.read_current()
                self.monitor_state = 'OK'
            except RedisError as e:
                log.error(f"Unable to store current due to redis error: {e}")
                self.monitor_state = 'Redis Error'
            except IOError as e:
                log.error(f"Error {e}")
                self.monitor_state = 'IOError'
            time.sleep(interval)

        self._monitor_thread = threading.Thread(target=f, name='Current Monitoring Thread')
        self._monitor_thread.daemon = True
        self._monitor_thread.start()


if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)  #Note that ultimately this is going to need to change. As written I suspect
    # all log messages will appear from "__main__" instead of showing up from "picturec.currentduinoAgent.Currentduino"
    # TODO: Logging for a package is something that's been on my to-do list for a while. Now is probably the time

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=['status:highcurrentboard:current'])
    currentduino = Currentduino(port='/dev/currentduino', baudrate=115200, timeout=0.1)

    try:
        firmware = currentduino.firmware
        redis.store({FIRMWARE_KEY: firmware})
    except IOError:
        redis.store({FIRMWARE_KEY: ''})
        redis.store({STATUS_KEY: 'FAILURE to poll firmware'})
        sys.exit(1)

    try:
        if not currentduino.firmware_ok():
            redis.store({STATUS_KEY: 'Unsupported firmware'})
            sys.exit(1)
    except IOError:
        redis.store({STATUS_KEY: 'Comm failure'})
        sys.exit(1)

    currentduino.monitor_current(QUERY_INTERVAL)

    while True:
        try:
            if currentduino.monitor_state == 'OK':
                redis.store({'status:highcurrentboard:current': currentduino.last_current}, timeseries=True)
                # TODO: status:highcurrentboard:powered should be a key that currentduino reads. Implement when power switches are introduced
                # redis.store({'status:highcurrentboard:powered': "True"})
                redis.store({STATUS_KEY: 'OK'})
            else:
                redis.store({STATUS_KEY: 'Current monitoring error'})

            for key, val in redis.listen([HEATSWITCH_MOVE_KEY]):
                hspos = val.lower()
                try:
                    currentduino.move_heat_switch(hspos)
                    redis.store({HEATSWITCH_STATUS_KEY: hspos})
                    redis.store({STATUS_KEY: 'OK'})
                except IOError as e:
                    log.info(f"Some error communicating with the arduino! {e}")
                    redis.store({STATUS_KEY: 'Arduino Com error'})
        except RedisError as e:
            redis.store({STATUS_KEY: 'Redis Error'})
            log.critical(f"Redis server error! {e}")
            sys.exit(1)
        except StopIteration:
            pass
