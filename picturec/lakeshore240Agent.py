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

HARDWARE NOTE: Ch1 -> LN2, Ch2 -> LHe

See manual in hardware/thermometry/LakeShore240_temperatureMonitor_manual.pdf
TODO: More Docstrings
"""

import sys
import time
import logging
import threading
from picturec.pcredis import PCRedis, RedisError
import picturec.agent as agent
import picturec.util as util

REDIS_DB = 0

KEYS = ['device-settings:ls240:lhe-profile',
        'device-settings:ls240:ln2-profile',
        'status:temps:lhetank',
        'status:temps:ln2tank'
        'status:device:ls240:firmware',
        'status:device:ls240:status',
        'status:device:ls240:model',
        'status:device:ls240:sn']

TS_KEYS = ('status:temps:lhetank', 'status:temps:ln2tank')

STATUS_KEY = "status:device:ls240:status"

FIRMWARE_KEY = "status:device:ls240:firmware"
MODEL_KEY = 'status:device:ls240:model'
SN_KEY = 'status:device:ls240:sn'

QUERY_INTERVAL = 1

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

        self.model = None

    def format_msg(self, msg:str):
        """
        Overrides agent.SerialAgent format_message() function. Commands to the LakeShore 240 are all upper-case.
        *NOTE: By choice, using .upper(), if we manually store a name of a curve/module, it will be in all caps.
        """
        return f"{msg.strip().upper()}{self.terminator}"

    def read_temperatures(self):
        """Queries the temperature of all enabled channels on the LakeShore 240. LakeShore reports values of temperature
        in Kelvin. May raise IOError in the case of serial communication not working."""
        readings = []
        tanks = ['ln2', 'lhe']
        for channel in self.enabled_channels:
            try:
                readings.append(float(self.query(f"KRDG? {channel}")))
            except IOError as e:
                log.error(f"Serial Error: {e}")
                raise IOError(f"Serial Error: {e}")
        temps = {tanks[i]: readings[i] for i in range(len(self.enabled_channels))}
        return temps

    @property
    def idn(self):
        """
        Queries the LakeShore240 for its ID information.
        Raise IOError if serial connection isn't working or if invalid values (from an unexpected module) are received
        ID return string is "<manufacturer>,<model>,<instrument serial>,<firmware version>\n"
        Format of return string is "s[4],s[11],s[7],float(#.#)"
        :return: Dict
        """
        try:
            id_string = self.query("*IDN?")
            manufacturer, model, sn, firmware = id_string.split(",")  # See manual p.43
            firmware = float(firmware)
            # TODO JB IMO it is bad practice for a property to have a sideeffect that alters the functionality of
            #  an object, and especially when it does not document it. Morover since other class members use this
            #  function they too have the sideeffect (and also do not disclose it).
            #  This really should be set at initialization, or at what ever step must be completed before functions
            #  using it can be meaningfully called. Here i think the solution is a post connect hook, wich will come
            #  naturally as we integrate device checking with the connection process.
            self.model = int(model[-2])
            return {'manufacturer': manufacturer,
                    'model': model,
                    'model-no': self.model,
                    'sn': sn,
                    'firmware': firmware}
        except IOError as e:
            log.error(f"Serial error: {e}")
            raise e
        except ValueError as e:
            log.error(f"Bad firmware format: {firmware}. Error: {e}")
            raise IOError(f"Bad firmware format: {firmware}. Error: {e}")

    def manufacturer_ok(self):
        """Returns true if the manufacturer for the lakeshore is valid. Otherwise false"""
        return self.idn['manufacturer'] == "LSCI"

    def model_ok(self):
        """Returns true if the model number for the lakeshore is valid. Otherwise false"""
        return self.idn['model-no'] in [2, 8]

    def _set_curve_name(self, channel: int, name: str):
        """Engineering function to set the name of a curve on the LakeShore240. Convenient since both thermometers are
        DT-670A-CU style, and so this can clear any ambiguity. Does not need to be used in normal operation. Logs
        IOError but does not raise it.
        """
        try:
            self.send(f'INNAME{str(channel)},"{name}"')
        except IOError as e:
            log.error(f"Unable to set channel {channel}'s name to '{name}'. "
                      f"Check to make sure the LakeShore USB is connected!")

    @property
    def enabled_channels(self):
        """
        'INTYPE? <channel>' query returns channel configuration info with
        returns '<sensor type>,<autorange>,<range>,<current reversal>,<units>\n'
        with format '#,#,#,#,#\n'. Raise IOError for any serial errors. Otherwise returns a list of enabled channels.
        If model number is not determined (i.e. IDN has not been queried), return None and report that the model number
        must be determined.
        """
        #TODO JB see comment in idn
        if not self.model:
            log.warning("enabled_channels() called yet model not set")
            raise RuntimeError('idn must be checked prior to use of enabled_channels() ')
        enabled = []
        for channel in range(1, self.model + 1):
            try:
                _, _, enabled_status = self.query(f"INTYPE? {channel}").rpartition(',')
                if enabled_status == "1":
                    enabled.append(channel)
            except IOError as e:
                log.error(f"Serial error: {e}")
                raise IOError(f"Serial error: {e}")
            #TODO why is this commented
            # except ValueError:
            #     log.critical(f"Channel {channel} returned and unknown value from channel information query")
            #     raise IOError(f"Channel {channel} returned and unknown value from channel information query")
        return enabled


if __name__ == "__main__":

    util.setup_logging()
    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)
    lakeshore = LakeShore240(port='/dev/lakeshore', baudrate=115200, timeout=0.1)

    try:
        info = lakeshore.idn
        # TODO JB: Note that placing the store before exit makes this program behave differently in an abort
        #  than both of the sims, which would not alter the database. I like this better.
        redis.store({FIRMWARE_KEY: info['firmware'], MODEL_KEY: info['model'], SN_KEY: info['firmware']})
        if not lakeshore.manufacturer_ok() or not lakeshore.model_ok():
            msg = f'Unsupported manufacture/device: {info["manufacturer"]}/{info["model"]}'
            redis.store({STATUS_KEY: msg})  #TODO JB: note that no status ever gets set in the event of normal operation
            log.critical(msg)
            sys.exit(1)
    except IOError as e:
        log.error(f"Serial error in querying LakeShore identification information: {e}")
        redis.store({FIRMWARE_KEY: '',  MODEL_KEY: '', SN_KEY: ''})
        sys.exit(1)

    #TODO note that by moving the firmware (and other sim init settings) into the devices class, one is always
    # guranateed that these checks are made, even if the connection is lost

    while True:
        try:
            temps = lakeshore.read_temperatures()
            redis.store({'status:temps:ln2tank': temps['ln2'],
                         'status:temps:lhetank': temps['lhe']}, timeseries=True)
        except IOError as e:
            log.error(f"Communication with LakeShore 240 failed: {e}")
        except RedisError as e:
            log.critical(f"Redis server error! {e}")
            sys.exit(1)
        time.sleep(QUERY_INTERVAL)
