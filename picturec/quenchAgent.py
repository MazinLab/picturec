"""
Author: Noah Swimmer, 3 February 2021

The goal of this program is to monitor for the potential signs of a quench in the PICTURE-C Magnet and, if found,
to report the quench as fast as possible to shut of the magnet and prevent any damage to it.

To run this in a testing capacity:
a) Open ipython
b) import numpy as np; import time; import picturec.pcredis as redis
c) redis.setup_redis(host='127.0.0.1', port=6379, db=0, create_ts_keys=['status:highcurrentboard:current'])
d) syntheticdata = np.load('/home/kids/simulatedlogs/synthetic_data.npz')
e) cycle = syntheticdata['cycle']; quench = syntheticdata['quench']
f) Run quench.py
e) for i in cycle: redis.store({'status:highcurrentboard:current': i}, timeseries=True); time.sleep(.1)
"""

import picturec.pcredis as redis
from picturec.pcredis import RedisError
import picturec.util as util
import picturec.currentduinoAgent as heatswitch
import numpy as np
from scipy.stats import linregress
from logging import getLogger
import time


TS_KEYS = ['status:temps:mkidarray:temp', 'status:highcurrentboard:current',
           'status:temps:lhetank', 'status:temps:ln2tank']
LOOP_INTERVAL = .25
QUENCH_KEY = 'event:quenching'


class QuenchMonitor:
    def __init__(self):
        self.fit = None
        self.fit_stddev = None

    @property
    def data(self):
        return np.array(redis.redis_ts.range('status:highcurrentboard:current', '-', '+')[-11:])

    def fit_data(self, data):
        data = data[:-1]
        reg_line = linregress(data[:, 0], data[:, 1])
        p = np.poly1d([reg_line[0], reg_line[1]])
        std_dev = np.std(data[:, 1] - p(data[:, 0]))
        return p, std_dev

    def check_quench_from_current(self):
        data = self.data
        getLogger(__name__).debug(data)
        self.fit, self.fit_stddev = self.fit_data(data)
        diff_from_expected = abs(data[-1][1] - self.fit(data[-1][0]))
        return diff_from_expected > 3 * self.fit_stddev and not self.fit_stddev <= 1e-5

if __name__ == "__main__":

    util.setup_logging('quenchAgent')
    redis.setup_redis(create_ts_keys=TS_KEYS)

    q = QuenchMonitor()

    warning = False
    log = getLogger('quenchAgent')
    log.debug('Starting quench monitoring')

    while True:
        try:
            quench = q.check_quench_from_current()

            log.debug(f"Checked for quench - quench={quench}")
            if quench:
                if warning:
                    redis.publish(QUENCH_KEY, f'QUENCH:{time.time()}')
                    log.critical(f"Quench detected.")
                else:
                    warning = True
            else:
                warning = False
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            break
        time.sleep(LOOP_INTERVAL)

