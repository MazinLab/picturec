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
import picturec.util as util

REDIS_DB = 0
QUERY_INTERVAL = 1
LOOP_INTERVAL = .001

DEVICE = '/dev/currentduino'

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
CURRENT_VALUE_KEY = 'status:highcurrentboard:current'


R1 = 11790  # Values for R1 resistor in magnet current measuring voltage divider
R2 = 11690  # Values for R2 resistor in magnet current measuring voltage divider

log = logging.getLogger(__name__)


class Currentduino(agent.SerialAgent):
    VALID_FIRMWARES = (0.0, 0.1, 0.2)  #TODO: tuple is better as it isn't mutable

    def __init__(self, port, baudrate=115200, timeout=0.1, connect=True):
        super().__init__(port, baudrate, timeout, name='currentduino')
        if connect:
            self.connect(raise_errors=False, post_connect_sleep=2)
        self.heat_switch_position = None
        self._monitor_thread = None
        self.last_current = None
        self.terminator = ''

    def read_current(self):
        """
        Read and return the current, may raise ValueError (unparseable response) or IOError (serial port communcation
        not working for some reason)"""
        response = self.query('?', connect=True)
        try:
            value = float(response.split(' ')[0])
            current = (value * (5.0 / 1023.0) * ((R1 + R2) / R2))
        except ValueError:
            raise ValueError(f"Could not parse '{response}' into a float")
        return current

    def format_msg(self, msg: str):
        """
        Overwrites function from SerialAgent superclass. Follows the communication model we made where the arduinos in
        PICTURE-C do not require termination characters.
        """
        return f"{msg.strip().lower()}{self.terminator}"

    def move_heat_switch(self, pos):
        """
        Takes a position (open | close) and first checks to make sure that it is valid. If it is, send the command to
        the currentduino to move the heat switch to that position. Return position if successful, otherwise log that
        the command failed and the heat switch position is 'unknown'. Raise IOError if there is a problem communicating
        with the serial port.
        """
        pos = pos.lower()
        if pos not in ('open', 'close'):
            raise ValueError(f"'{pos} is not a vaild (open, close) heat switch position")

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
        """ Return True or False if the firmware is supported, may raise IOErrors """
        return self.firmware in self.VALID_FIRMWARES

    @property
    def firmware(self):
        """ Return the firmware string or raise IOError """
        try:
            log.debug(f"Querying currentduino firmware")
            response = self.query("v", connect=True)
            version, _, v = response.partition(" ")  # Arduino resonse format is "{response} {query char}"
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

    def monitor_current(self, interval, value_callback=None):
        """
        Create a function to continuously query the current as measured by the arduino. Log any IOErrors that occur.
        If a value_callback is given, perform whatever actions the value_callback requires (typically storing values to
        redis database). Except and raise RedisError. If the redis server has gone down or the program can't communicate
        with it, something is wrong and the program cannot successfully run. Interval determines the time between
        queries of current.
        """
        def f():
            while True:
                current = None
                try:
                    self.last_current = self.read_current()
                    current = self.last_current
                except IOError as e:
                    log.error(f"Unable to poll for current: {e}")

                if value_callback is not None and current is not None:
                    try:
                        value_callback(self.last_current)
                    except RedisError as e:
                        log.error(f"Unable to store current due to redis error: {e}")
                        raise e

                time.sleep(interval)

        self._monitor_thread = threading.Thread(target=f, name='Current Monitoring Thread')
        self._monitor_thread.daemon = True
        self._monitor_thread.start()


if __name__ == "__main__":

    util.setup_logging()

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=[CURRENT_VALUE_KEY])
    currentduino = Currentduino(port=DEVICE, baudrate=115200, timeout=0.1)

    try:
        firmware = currentduino.firmware
        redis.store({FIRMWARE_KEY: firmware})
        if not currentduino.firmware_ok():
            redis.store({STATUS_KEY: 'Unsupported firmware'})
            sys.exit(1)
    except IOError:
        redis.store({FIRMWARE_KEY: ''})
        redis.store({STATUS_KEY: 'FAILURE to poll firmware'})
        sys.exit(1)
    except RedisError as e:
        log.critical(f"Redis server error! {e}")
        sys.exit(1)

    store_func = lambda x: redis.store({CURRENT_VALUE_KEY: x}, timeseries=True)
    currentduino.monitor_current(QUERY_INTERVAL, value_callback=store_func)

    while True:
        try:
            for key, val in redis.listen([HEATSWITCH_MOVE_KEY]):
                hspos = val.lower()
                try:
                    currentduino.move_heat_switch(hspos)
                    redis.store({HEATSWITCH_STATUS_KEY: hspos})
                except IOError as e:
                    log.info(f"Some error communicating with the arduino! {e}")
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            break

