"""
Author: Noah Swimmer 15 June 2020

Program to control ArduinoUNO that will measure the current through the ADR magnet by monitoring the
current-sensing resistor on the PIPER-designed HighCurrent Boost board (see picturec reference folder
for circuit drawing). Will log values to redis, will also act as a safeguard to tell the magnet current
control that the current is operating out of normal bounds.
NOTE: Redis/redistimeseries MUST be set up
for the currentduino to work.

TODO: - Add ability to compare current value from high current board ('status:highcurrentboard:current') to that
 of the magnet ('status:magnet:current') from the SIM960 - NOTE: This feels like higher level management
 - Test the heat switch touch signals to have an open/closed monitor (in lab)
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
VALID_FIRMWARES = [0.0, 0.1, 0.2] 

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
            self.connect(raise_errors=False, post_connect_sleep=2)
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
            log.info(f"Commanding heat switch to {pos}")
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
                log.warning(f"Query unsuccessful. Check error logs for response from arduino")
            return float(version_response[0])
        except (IOError, IndexError) as e:
            log.warning(f"Query unsuccessful. Check error logs: {e}")
            raise e


def poll_current():
    while True:
        try:
            redis.store({'status:highcurrentboard:current': currentduino.current}, timeseries=True)
            redis.store({'status:highcurrentboard:powered': "True"})  # NB changed from ok to true
        except RedisError as e:
            log.critical(f"Redis error{e}")
            sys.exit(1)
        except IOError as e:
            log.error(f"Error {e}")
            redis.store({STATUS_KEY: f"Error {e}"})
        time.sleep(QUERY_INTERVAL)


def handle_redis_message(message):
    if message['channel'].decode() == HEATSWITCH_MOVE_KEY:
        try:
            currentduino.move_heat_switch(message['data'].decode().lower())
            redis.store({HEATSWITCH_STATUS_KEY: message['data'].decode().lower()})
        except RedisError as e:
            raise e
        except IOError as e:
            raise e
    else:
        log.debug(f"Got a message from an unexpected channel: {message}")


if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)  #Note that ultimately this is going to need to change. As written I suspect
    # all log messages will appear from "__main__" instead of showing up from "picturec.currentduinoAgent.Currentduino"
    # TODO: Logging for a package is something that's been on my to-do list for a while. Now is probably the time

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=['status:highcurrentboard:current'])
    currentduino = Currentduino(port='/dev/currentduino', baudrate=115200, timeout=0.1)

    try:
        firmware = currentduino.firmware
        if firmware not in VALID_FIRMWARES:
            raise IOError(f"Unsupported firmware '{firmware}'. Supported FW: {VALID_FIRMWARES}")
        redis.store({FIRMWARE_KEY: firmware})
    except IOError:
        redis.store({FIRMWARE_KEY: ''})
        redis.store({STATUS_KEY: 'FAILURE to poll firmware'})
        sys.exit(1)
    except IndexError:
        redis.store({FIRMWARE_KEY: ''})
        redis.store({STATUS_KEY: 'Firmware poll returned nonsense'})
        sys.exit(1)

    pollthread = threading.Thread(target=poll_current, name='Current Monitoring Thread')
    pollthread.daemon = True
    pollthread.start()

    try:
        pubsub = redis.redis.pubsub()
        pubsub.subscribe([HEATSWITCH_MOVE_KEY])
    except RedisError as e:
        log.critical(f"Redis error while subscribing to redis pubsub!! {e}")
        raise e

    for msg in pubsub.listen():
        log.info(f"Pubsub received {msg}")
        if (msg['channel'].decode() == HEATSWITCH_MOVE_KEY) and (msg['type'] != 'subscribe'):
            try:
                currentduino.move_heat_switch(msg['data'].decode().lower())
                redis.store({HEATSWITCH_STATUS_KEY: msg['data'].decode().lower()})
            except RedisError as e:
                log.critical(f"Redis server may have closed! {e}")
                sys.exit()
            except IOError as e:
                log.critical(f"Some error communicating with the arduino! {e}")
        else:
            log.debug(f"Received {msg}")
