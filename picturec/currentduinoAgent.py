"""
Author: Noah Swimmer 15 June 2020

Program to control ArduinoUNO that will measure the current through the ADR magnet by monitoring the
current-sensing resistor on the PIPER-designed HighCurrent Boost board (see picturec reference folder
for circuit drawing). Will log values to redis, will also act as a safeguard to tell the magnet current
control that the current is operating out of normal bounds. NOTE: Redis/redistimeseries MUST be set up
for the currentduino to work.

TODO: - Add ability to compare current value from high current board ('status:highcurrentboard:current') to that
 of the magnet ('status:magnet:current') from the SIM960 - NOTE: This feels like higher level management
 - Test the heat switch touch signals to have an open/closed monitor (in lab)
"""

import serial
from serial import SerialException
import sys
import time
import logging
from logging import getLogger
from datetime import datetime
from redis import RedisError
# from redis import Redis
# from redistimeseries.client import Client
import threading
from picturec.pc_redis import PCRedis
import picturec.agent as agent

REDIS_DB = 0
QUERY_INTERVAL = 1
LOOP_INTERVAL = .05
VALID_FIRMWARES = ['0.0', '0.1', '0.2']  # TODO: Configuration file?

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

# TODO Any possibility these should be in redis defaults or some sort of namespace that indicates static configuration?
#  I don't know enough about how precicely these values are known and how stable they are
#     Response - These could be defaults, this is a voltage divider made up of 2 resistors I have measured in lab. They
#     could definitely go in a configuration file and are static. The only change would be in creating a new voltage
#     divider or if one of the resistors happened to blow (in which case we'd need to build/buy a new one anyway).
R1 = 11790  # Values for R1 resistor in magnet current measuring voltage divider
R2 = 11690  # Values for R2 resistor in magnet current measuring voltage divider

log = logging.getLogger(__name__)


class Currentduino(agent.SerialAgent):
    def __init__(self, port, baudrate=115200, timeout=0.1, connect=True):

        super().__init__(port, baudrate, timeout, name='currentduino')
        if connect:
            self.connect(raise_errors=False)
        time.sleep(1)
        self.heat_switch_position = None

    @property
    def current(self):
        try:
            self.send('?', instrument_name=self.name, connect=True)
            response = self.receive()

            try:
                value = float(response.split(' ')[0])
                current = (value * (5.0 / 1023.0) * ((R1 + R2) / R2))
            except ValueError:
                raise ValueError(f"Couldn't parse '{response}' into a float")

        except Exception as e:
            raise IOError(e)
        return current

    def move_heat_switch(self, pos):
        pos = pos.lower()
        if pos not in ('open', 'close'):
            raise ValueError(f"'{pos} is not a vaild (open, close) heat switch position")

        # NB it is mighty convenient that the serial command/confirmation and pos start with the same letter
        try:
            log.info(f"Commanding heat switch  to {pos}")
            self.send(pos[0], instrument_name=self.name)
            confirm = self.receive()
            if confirm == pos[0]:
                log.info(f"Command accepted")
            else:
                log.info(f"Command failed: '{confirm}'")
            return pos if confirm == pos[0] else 'unknown'
        except Exception as e:
            raise IOError(e)

    @property
    def firmware(self):
        try:
            log.info(f"Querying currentduino firmware")
            self.send("v", instrument_name=self.name, connect=True)
            version_response = self.receive().split(" ")
            if version_response[1] == "v":
                log.info(f"Query successful. Firmware version {version_response[0]}")
            else:
                log.info(f"Query unsuccessful. Check error logs for response from arduino")
            return float(version_response[0])
        except Exception as e:
            log.info(f"Query unsuccessful. Check error logs: {e}")
            raise Exception


def poll_current():
    while True:
        try:
            redis.store(('status:highcurrentboard:current', currentduino.current), timeseries=True)
            redis.store(('status:highcurrentboard:powered', "True"))  # NB changed from ok to true
            # redis.redis.set('status:highcurrentboard:powered', "True") - Replaced with the line above
        except RedisError as e:
            log.critical(f"Redis error{e}")
            sys.exit(1)
        except IOError as e:
            log.error(f"Error {e}")
            redis.store((STATUS_KEY, f"Error {e}"))
        time.sleep(QUERY_INTERVAL)


def redis_listen(keys_to_register):
    ps = redis.redis.pubsub()
    [ps.subscribe(key) for key in keys_to_register]
    while True:
        try:
            msg = ps.get_message()
            if msg:
                log.info(f"Redis client received a message {msg}")
                handle_redis_message(msg)
            else:
                pass
        except RedisError as e:
            log.critical(f"Redis error{e}")
            sys.exit(1)
        except IOError as e:
            log.error(f"Error {e}")
            redis.store((STATUS_KEY, f"Error {e}"))
        except Exception as e:
            log.warning(f" Exception in PubSub operation has occurred: {e}")
            ps = None
            time.sleep(.1)
            ps = redis.redis.pubsub()
            [ps.subscribe(key) for key in keys_to_register]
        time.sleep(LOOP_INTERVAL)


def handle_redis_message(message):
    if message['type'] == 'subscribe':
        if message['channel'].decode() == HEATSWITCH_MOVE_KEY:
            try:
                currentduino.move_heat_switch(message['data'].decode().lower())
                redis.store({HEATSWITCH_STATUS_KEY: message['data'].decode().lower()})
            except RedisError as e:
                raise e
            except IOError as e:
                raise e


if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)  #Note that ultimately this is going to need to change. As written I suspect
    # all log messages will appear from "__main__" instead of showing up from "picturec.currentduinoAgent.Currentduino"
    # TODO: Logging for a package is something that's been on my to-do list for a while. Now is probably the time

    redis = PCRedis(host='localhost', port=6379, db=REDIS_DB, create_ts_keys=['status:highcurrentboard:current'])
    currentduino = Currentduino(port='/dev/currentduino', baudrate=115200, timeout=0.1)

    try:
        firmware = currentduino.firmware
        if firmware not in VALID_FIRMWARES:
            raise IOError(f"Unsupported firmware '{firmware}'. Supported FW: {VALID_FIRMWARES}")
        redis.store((FIRMWARE_KEY, firmware))
    except IOError:
        redis.store((FIRMWARE_KEY, ''))
        redis.store((STATUS_KEY, 'FAILURE to poll firmware'))
        sys.exit(1)

    pollthread = threading.Thread(target=poll_current, name='Current Monitoring Thread')
    pollthread.daemon = True
    pollthread.start()

    heatswitchthread = threading.Thread(target=redis_listen, name='Command Monitoring Thread', args=HEATSWITCH_STATUS_KEY)
    heatswitchthread.daemon = True
    heatswitchthread.start()

    # while True:
    #
    #     try:
    #         switch_pos = redis.read(HEATSWITCH_MOVE_KEY, return_dict=False)
    #         if switch_pos in ('open', 'close'):
    #             hs_pos = currentduino.move_heat_switch(switch_pos)
    #             redis.store((HEATSWITCH_STATUS_KEY, hs_pos))
    #         else:
    #             log.info('Ignoring invalid requested HS position')
    #             redis.store(HEATSWITCH_MOVE_KEY, '')  #TODO change to the current value, error? -> Not sure what this means
    #     except RedisError as e:
    #         log.critical(f"Redis error{e}")
    #         sys.exit(1)
    #     except IOError as e:
    #         log.error(f"Error {e}")
    #
    #         # TODO Note that as implemented this is similar to a race condition. The polling thread can have a
    #         #  error and report it in the status key. Which then gets overwritten here milliseconds later.
    #         #  One fix around this is for the redis class (or probably more appropriately) an eventual Agent program
    #         #  class to have an update_status method whihc first reads the redis status and only updates the part that
    #         #  is changing. In essence this program here doesn't have a single status, it has at least 3 bits: current
    #         #  polling, hs operation, and general program function. Access to all of those parts has to be passed
    #         #  through an inflection point so that the update to the status from the hs doesn't need to know about the
    #         #  current and vice versa.
    #
    #         redis.store((STATUS_KEY, f"Error {e}"))
    #
    #     time.sleep(LOOP_INTERVAL)
