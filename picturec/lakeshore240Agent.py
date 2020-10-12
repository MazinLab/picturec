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

TODO: Consider using the INNAME (Sensor Input Name) Command. This can allow us to unambiguously determine which
 channel is for the LN2 tank and which is for LHe

TODO: Make UDEV rule for LakeShore240

TODO: Incorporate redis storage (this program does not need pubsub in any obvious places)
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

FIRMWARE_KEY = "status:device:ls240:firmware"
MODEL_KEY = 'status:device:ls240:model'
SN_KEY = 'status:device:ls240:sn'

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
        The exception to this is when setting names (for the Module or Individual channels, e.g.
        'INNAME1,"LHe Thermometer"\n' to set the name of the input channel)
        """
        return f"{msg.strip()}{self.terminator}"

    def read_temperatures(self):
        """Queries the temperature of all enabled channels on the LakeShore 240. LakeShore reports values of temperature
        in Kelvin. May raise IOError in the case of serial communication not working."""

        # TODO: Set and confirm the mapping of channel -> cryogen tank. (Ch1=?, Ch2=?). Could also query curvename here
        readings = []
        tanks = ['ln2', 'lhe']
        for channel in self.enabled_channels:
            try:
                readings.append(float(self.query("KRDG? " + channel)))
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
            self.model = float(model[-2])
            return {'manufacturer': manufacturer,
                    'model': model,
                    'model-no': self.model,
                    'sn': sn,
                    'firmware': firmware}
        except IOError as e:
            log.error(f"Serial error: {e}")
            raise e
        except ValueError as e:
            log.error(f"Bad firmware format: {firmware}")

    def manufacturer_ok(self):
        return self.idn['manufacturer'] == "LSCI"

    def model_ok(self):
        return self.idn['model-no'] in ["2", "8"]

    def _set_curve_name(self, channel: int, name: str):
        """Engineering function to set the name of a curve on the LakeShore240. Convenient since both thermometers are
        DT-670A-CU style, and so this can clear any ambiguity. Does not need to be used in normal operation
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
        with format '#,#,#,#,#\n'
        """
        enabled = []
        if self.model:
            for channel in range(1, self.model + 1):
                try:
                    _, _, _, _, enabled_status = self.query("INTYPE? "+str(channel)).split(",")
                    if enabled_status == "1":
                        enabled.append(channel)
                except IOError as e:
                    log.error(f"Serial error: {e}")
                    raise IOError(f"Serial error: {e}")
                # except ValueError:
                #     log.critical(f"Channel {channel} returned and unknown value from channel information query")
                #     raise IOError(f"Channel {channel} returned and unknown value from channel information query")
            return enabled
        else:
            log.critical("Cannot determine enabled channels! Model number has not been determined")
            return None

if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)
    redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB,
                    create_ts_keys=['status:temps:lhetank', 'status:temps:ln2tank'])
    lakeshore = LakeShore240(port='/dev/lakeshore240', baudrate=115200, timeout=0.1)

    # TODO: Properly query ID, model, and enabled channels once to ensure they're properly set up

    # TODO: Infinitely query the temperatures for LHe and LN2
