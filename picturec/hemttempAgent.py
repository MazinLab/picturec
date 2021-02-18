"""
Author: Noah Swimmer, 1 April 2020

Program for controlling ArduinoMEGA that monitors the bias voltages and currents of the transistors
of the cryogenic HEMT amplifiers in the PITCURE-C cryostat. Will log the values directly to redis database
for the fridge monitor to store/determine that the amplifiers are working properly.

TODO: Add HEMT rack temperature reporting (after rack thermometers installed)

TODO: Account for HEMT Power On/Off?

TODO: STORE HEMT S/N Value? Maybe here, maybe static config location

TODO: Currently (3 November 2020) this purely stores bias voltages. Drain-current-bias can be stored as a current using
 the conversion formula -> drain-current-bias = (0.1 V/mA) * drain-current
                        -> drain-current = drain-current-bias / (0.1 V/mA)
"""


import sys
import time
import logging
import numpy as np

import picturec.devices
from picturec.pcredis import PCRedis, RedisError
import picturec.util as util
import time

DEVICE = "/dev/hemtduino"
REDIS_DB = 0
QUERY_INTERVAL = 1

HEMT_VALUES = ['gate-voltage-bias', 'drain-current-bias', 'drain-voltage-bias']
KEYS = [f"status:feedline{5-i}:hemt:{j}" for i in range(5) for j in HEMT_VALUES]
KEY_DICT = {msg_idx: key for (msg_idx, key) in zip(np.arange(0, 15, 1), KEYS)}
STATUS_KEY = "status:device:hemtduino:status"
FIRMWARE_KEY = "status:device:hemtduino:firmware"

# TODO Note that this way of using logging throughout the file means that Hemtduino (and all the othrs)
#  would be better off in their own picturec.devices module
#  Noah (18 January 2020) - I think this^ is how everything is logged and I just haven't updated the comment.
log = logging.getLogger(__name__)


class Hemtduino(picturec.devices.SerialDevice):
    VALID_FIRMWARES = (0.0, 0.1)

    def __init__(self, port, baudrate=115200, timeout=0.1, connect=True):
        super().__init__(port, baudrate, timeout, name='hemtduino')
        if connect:
            self.connect(raise_errors=False)

        self.terminator = ''

    def _postconnect(self):
        """
        Overwrites serialDevice _postconnect function. Sleeps for an appropriate amount of time to let the arduino get
        booted up properly so the first queries don't return nonsense (or nothing)
        """
        time.sleep(1)

    def format_msg(self, msg:str):
        """
        Overwrites the format_msg function from SerialDevice. Returns a lowercase string with the hemtduino terminator
        (which is '' in the contract with the hemtduino).
        """
        return f"{msg.strip().lower()}{self.terminator}".encode("utf-8")

    def firmware_ok(self):
        """
        Return True if the reported firmware is in the list of valid firmwares for the hemtduino.
        """
        return self.firmware in self.VALID_FIRMWARES

    @property
    def firmware(self):
        """ Return the firmware string or raise IOError """
        #TODO JB There is a lot of common code between firmware, idn, and the mains.
        # Things could be simplified and made more reliable by better encapsulating this
        #  NS: ^Agreed. Firmware/idn properties are split into 2 groups, arduinos (hemttempAgent+currentduinoAgent just
        #  use firmware) and non-Arduinos (sim921,sim960,lakeshore have idns).
        #  Probably could make both agent superclass properties
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

    def read_hemt_data(self):
        """
        Return the hemt data in the order received from the hemtduino (it reads A0 -> A14) along with the proper keys.
        This reports the bias voltages read. It does not convert to current for the gate current values. Raises a value
        error if a bad response is returned (the arduino does not report back the query string as the final character)
        or a nonsense string is returned that is unparseable.
        """
        response = self.query('?', connect=True)
        try:
            resp = response.split(' ')
            values = list(map(float, resp[:-1]))
            confirm = resp[-1]
            if confirm == '?':
                log.info("HEMT values successfully queried")
                pvals = [v * (5.0 / 1023.0) if i % 3 else 2 * ((v * (5.0 / 1023.0)) - 2.5) for i, v in enumerate(values)]
                ret = {key: v for key, v in zip(KEYS, pvals)}
            else:
                raise ValueError(f"Nonsense was returned: {response}")
        except Exception as e:
            raise ValueError(f"Error parsing response data: {response}. Exception {e}")
        return ret


if __name__ == "__main__":

    util.setup_logging('hemttempAgent')

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=KEYS)
    hemtduino = Hemtduino(port=DEVICE, baudrate=115200, timeout=0.1)

    try:
        firmware = hemtduino.firmware
        redis.store({FIRMWARE_KEY: firmware})
        if not hemtduino.firmware_ok():
            redis.store({STATUS_KEY: 'Unsupported firmware'})
            sys.exit(1)
    except IOError:
        redis.store({FIRMWARE_KEY: ''})
        redis.store({STATUS_KEY: 'FAILURE to poll firmware'})
        sys.exit(1)
    except RedisError as e:
        log.critical(f"Redis server error! {e}")
        sys.exit(1)

    while True:
        try:
            data = hemtduino.read_hemt_data()
            redis.store(data, timeseries=True)
            redis.store({STATUS_KEY: 'OK'})
        except RedisError as e:
            log.error(f"Redis error {e}")
            sys.exit(1)
        except IOError as e:
            log.error(f"Error {e}")
            redis.store({STATUS_KEY: f"Error: {e}"})

        time.sleep(QUERY_INTERVAL)
