"""
Author: Noah Swimmer, 3 February 2021

The goal of this program is to monitor for the potential signs of a quench in the PICTURE-C Magnet and, if found,
to report the quench as fast as possible to shut of the magnet and prevent any damage to it.

TODO: Complete testing with simulated data
"""

import picturec.pcredis as redis
from picturec.pcredis import RedisError
import picturec.util as util
import numpy as np
from scipy.stats import linregress
from logging import getLogger
import time


TS_KEYS = ['status:temps:mkidarray:temp', 'status:highcurrentboard:current',
           'status:temps:lhetank', 'status:temps:ln2tank']
LOOP_INTERVAL = .25
QUENCH_KEY = 'event:quenching'

MAX_STARTUP_LAG_TIME_SECONDS = 600


class QuenchMonitor:
    def __init__(self):
        self.fit = None
        self.fit_stddev = None
        self.timestream = self.initialize_data()
        self.di_dt = self.initialize_di_dt()
        self.max_ramp_rate = float(redis.read('device-settings:sim960:deramp-rate'))

    def update(self):
        new_data = redis.read('status:highcurrentboard:current')
        if new_data[0] == self.timestream[-1][0]:
            pass
        else:
            self.di_dt.append((new_data[0], 1000 * (new_data[1] - self.timestream[-1][1])/(new_data[0] - self.timestream[-1][0])))
            self.timestream.append(new_data)

    def initialize_data(self):
        """
        returns a 2 column array where column 0 is time (ms) and column 1 is current (A) up to the
        last MAX_STARTUP_LAG_TIME_SECONDS. If there is no data in this time range, it will return an empty array
        """
        now = time.time() * 1000
        first_time = MAX_STARTUP_LAG_TIME_SECONDS * 1000
        return redis.pcr_range('status:highcurrentboard:current', int(now-first_time), int(now))

    def initialize_di_dt(self):
        """
        returns a 2 column array where column 0 is time (ms) and column 1 is the current di/dt (A/s). The timestamps
        will match those from initialize_data
        """
        tsl = len(self.timestream)
        di_dt = []
        if tsl > 1:
            for i in range(len(self.timestream)-1):
                di_dt.append((self.timestream[i+1][0],
                              1000*((self.timestream[i+1][1] - self.timestream[i][1])/(self.timestream[i+1][0] - self.timestream[i][0]))))

        return di_dt

    def check_quench(self):
        return self.di_dt[-1][1] <= -5 * self.max_ramp_rate


if __name__ == "__main__":

    util.setup_logging('quenchAgent')
    redis.setup_redis(create_ts_keys=TS_KEYS)

    q = QuenchMonitor()

    warning = False
    log = getLogger('quenchAgent')
    log.debug('Starting quench monitoring')

    while True:
        try:
            q.update()
            quench = q.check_quench()

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

