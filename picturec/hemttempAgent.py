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
from picturec.pcredis import PCRedis, RedisError
import picturec.agent as agent

REDIS_DB = 0
QUERY_INTERVAL = 1

HEMT_VALUES = ['gate-voltage-bias', 'drain-current-bias', 'drain-voltage-bias']
KEYS = [f"status:feedline{5-i}:hemt:{j}" for i in range(5) for j in HEMT_VALUES]
KEY_DICT = {msg_idx: key for (msg_idx, key) in zip(np.arange(0, 15, 1), KEYS)}
STATUS_KEY = "status:device:hemtduino:status"
FIRMWARE_KEY = "status:device:hemtduino:firmware"


log = logging.getLogger(__name__)


class Hemtduino(agent.SerialAgent):
    VALID_FIRMWARES = [0.0, 0.1]
    def __init__(self, port, baudrate=115200, timeout=0.1, connect=True):
        super().__init__(port, baudrate, timeout, name='hemtduino')
        if connect:
            self.connect(raise_errors=False, post_connect_sleep=2)

        self.terminator = ''

    def format_msg(self, msg:str):
        return f"{msg.strip().lower()}{self.terminator}"

    def firmware_ok(self):
        return self.firmware in self.VALID_FIRMWARES

    @property
    def firmware(self):
        """ Return the firmware string or raise IOError"""
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
            raise ValueError(f"Error parsing response data: {response}")
        return ret


if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)

    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=KEYS)
    hemtduino = Hemtduino(port="/dev/hemtduino", baudrate=115200, timeout=0.1)

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
