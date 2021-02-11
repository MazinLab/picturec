"""
Author: Noah Swimmer, 3 February 2021

The goal of this program is to monitor for the potential signs of a quench in the PICTURE-C Magnet and, if found,
to report the quench as fast as possible to shut of the magnet and prevent any damage to it.

# TODO: Test
Needs:
 - Redis server up and running (and client in program)
 - Up-to-date current readings
 - Device temperature measurements
 - LHe tank temperature measurements
"""

import picturec.pcredis as redis
from picturec.pcredis import RedisError
import picturec.util as util
import picturec.currentduinoAgent as heatswitch
import numpy as np
from scipy.stats import linregress
import logging


REDIS_DB = 0
TS_KEYS = ['status:temps:mkidarray:temp', 'status:highcurrentboard:current', 'status:temps:lhetank', 'status:temps:ln2tank']
LOOP_INTERVAL = .1

QUENCH_KEY = 'event:quenching'

class QuenchMonitor:

    def __init__(self):
        pass

    def read_timestream(self, key):
        return np.array(redis.redis_ts.range(key, '-', '+')[-11:])

    def fit_data(self, data):
        data = data[:-1]
        reg_line = linregress(data[:, 0], data[:, 1])
        p = np.poly1d([reg_line[0], reg_line[1]])
        std_dev = np.std(data[:, 1] - p(data[:, 0]))
        return p, std_dev

    def check_quench_from_current(self):
        data = self.read_timestream('status:highcurrentboard:current')
        fit, std_dev = self.fit_data(data)

        diff_from_expected = data[-1][1] - fit(data[-1][0])
        if diff_from_expected > 3 * std_dev:
            return True
        else:
            return False


if __name__ == "__main__":

    # util.setup_logging()
    log = logging.getLogger(__name__)
    redis.setup_redis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)

    q = QuenchMonitor()

    quench = False

    while True:
        try:
            quench = q.check_quench_from_current()
            if quench:
                redis.publish(QUENCH_KEY, 'Quenched!!!')
            else:
                pass
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            break

