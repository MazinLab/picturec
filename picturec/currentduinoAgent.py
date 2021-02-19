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
TODO: Also measure magnet-current-to-currentduino-measurement conversion (does the currentduino report the same thing we
 measure with an ammeter?)
"""

import sys
import time
import logging
import threading

from picturec.devices import Currentduino, HeatswitchPosition
import picturec.pcredis
from picturec.pcredis import PCRedis, RedisError
import picturec.util as util


QUERY_INTERVAL = .1

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


log = logging.getLogger(__name__)




def close():
    picturec.pcredis.publish(HEATSWITCH_MOVE_KEY, HeatswitchPosition.CLOSE)


def open():
    picturec.pcredis.publish(HEATSWITCH_MOVE_KEY, HeatswitchPosition.OPEN)


def is_opened():
    return picturec.pcredis.read(HEATSWITCH_STATUS_KEY, return_dict=False)[0] == HeatswitchPosition.OPEN


def is_closed():
    return picturec.pcredis.read(HEATSWITCH_STATUS_KEY, return_dict=False)[0] == HeatswitchPosition.CLOSE



if __name__ == "__main__":

    util.setup_logging('currentduinoAgent')

    redis = PCRedis(create_ts_keys=[CURRENT_VALUE_KEY])
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
                    time.sleep(2)
                    # TODO: Wire this sensor up. Until it is properly wired, check_hs_pos() defaults to True
                    if currentduino.check_hs_pos(hspos):
                        redis.store({HEATSWITCH_STATUS_KEY: hspos})
                    else:
                        pass
                except IOError as e:
                    log.info(f"Some error communicating with the arduino! {e}")
                except ValueError as e:
                    log.info(f"An invalid value was sent to the arduino. Please check to make sure your program is sending valid heatswitch positions.")
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            break
