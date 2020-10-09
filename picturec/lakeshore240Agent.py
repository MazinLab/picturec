"""
Author: Noah Swimmer
8 October 2020

Program for communicating with and controlling the LakeShore240 Thermometry Unit.
This module is responsible for reading out 2 temperatures, that of the LN2 tank and the LHe tank.
Both are identical LakeShore DT-670A-CU diode thermometers. Using the LakeShore MeasureLink desktop application, the
LakeShore can be configured easily (it autodetects the thermometers and loads in the default calibration curve). There
will be functionality in the lakeshore240Agent to configure settings, although that should not be necessary unless the
thermometers are removed and replaced with new ones.
Again, the calibration process can be done manually using the LakeShore GUI if so desired.

See manual in hardware/thermometry/LakeShore240_temperatureMonitor_manual.pdf
TODO: More Docstrings

TODO: Everything

TODO: Make UDEV rule for LakeShore240
"""

import sys
import time
import logging
import threading
from picturec.pcredis import PCRedis, RedisError
import picturec.agent as agent

REDIS_DB = 0

KEYS = ['device-settings:ls240:lhe-profile',
        'device-settings:ls240:ln2-profile',
        'status:temps:lhetank',
        'status:temps:ln2tank'
        'status:device:ls240:firmware',
        'status:device:ls240:status',
        'status:device:ls240:model',
        'status:device:ls240:sn']

STATUS_KEY = "status:device:ls240:status"
# TODO: Consider validity of status key, but also a thought... Should the status key be reserved for essentially when
#  the program exits with an error? Which would mean status is probably the wrong key. Maybe 'last_error'?
FIRMWARE_KEY = "status:device:ls240:firmware"

log = logging.getLogger()

class LakeShore240(agent.SerialAgent):
    def __init__(self, port, baudrate=115200, timeout=0.1, connect=True):
        super().__init__(port, baudrate, timeout, name='lakeshore240')
        if connect:
            self.connect(raise_errors=False, post_connect_sleep=1)
        self._monitor_thread = None  # Maybe not even necessary since this only queries
        self.last_he_temp = None
        self.last_ln2_temp = None
        self.terminator = '\n'

    def read_temperatures(self):
        pass

    def format_msg(self, msg):
        pass

    def id_query(self):
        """
        Queries the LakeShore240 for its ID information.
        Raise IOError if serial connection isn't working or if invalid values (from an unexpected module) are received
        ID return string is "<manufacturer>,<model>,<instrument serial>,<firmware version>\n"
        Format of return string is "s[4],s[11],s[7],#.#"
        :return: Dict
        """
        try:
            id_string = self.query("*IDN?")
            manufacturer, model, sn, firmware = id_string.split(",")  # See manual p.43
            firmware = float(firmware)
            if manufacturer != "LSCI":
                raise NotImplementedError(f"Manufacturer {manufacturer} is has no supported devices!")
            if model[-2] not in ["2", "8"]:
                raise NotImplementedError(f"Model {model} has not been implemented!")
            return {'manufacturer': manufacturer,
                    'model': model,
                    'sn': sn,
                    'firmware': firmware}
        except IOError as e:
            log.error(f"Serial error: {e}")
            raise e
        except NotImplementedError:
            log.error(f"Bad ID Query format: '{id_string}'")
            raise IOError(f"Bad ID Query format: '{id_string}'")


    def enabled_channels(self):
        pass

if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)
    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB,
                    create_ts_keys=['status:temps:lhetank', 'status:temps:ln2tank'])
    lakeshore = LakeShore240(port='/dev/lakeshore240', baudrate=115200, timeout=0.1)

