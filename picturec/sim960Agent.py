"""
Author: Noah Swimmer, 21 July 2020

NOTE: Unlike the SIM921, the SIM960 supports different baudrates. These need to be tested outside of the mainframe
before settling on the most appropriate one.
"""

import serial
import numpy as np
from logging import getLogger
from serial import SerialException
import time
from redis import Redis, RedisError
from redistimeseries.client import Client
import sys

SETTING_KEYS = ['device-settings:sim960:mode',
                'device-settings:sim960:vout-min-limit',
                'device-settings:sim960:vout-max-limit',
                'device-settings:sim960:pid',
                'device-settings:sim960:pid-p',
                'device-settings:sim960:pid-i',
                'device-settings:sim960:pid-d',
                'device-settings:sim960:setpoint-mode',
                'device-settings:sim960:pid-control-vin-setpoint',
                'device-settings:sim960:ramp-rate',
                'device-settings:sim960:ramp-enable',
                'device-settings:sim960:vout-value']

DEFAULT_SETTING_KEYS = ['default:device-settings:sim960:mode',
                        'default:device-settings:sim960:vout-min-limit',
                        'default:device-settings:sim960:vout-max-limit',
                        'default:device-settings:sim960:pid',
                        'default:device-settings:sim960:pid-p',
                        'default:device-settings:sim960:pid-i',
                        'default:device-settings:sim960:pid-d',
                        'default:device-settings:sim960:setpoint-mode',
                        'default:device-settings:sim960:pid-control-vin-setpoint',
                        'default:device-settings:sim960:ramp-rate',
                        'default:device-settings:sim960:ramp-enable',
                        'default:device-settings:sim960:vout-value']

OUTPUT_VOLTAGE_KEY = 'status:device:sim960:hcfet-control-voltage'
INPUT_VOLTAGE_KEY = 'status:device:sim921:sim960-vout'
MAGNET_CURRENT_KEY = 'status:magnet:current'  # To get the current from the sim960. We will need to run a calibration
# test to figure out what the output voltage to current conversion is.
MAGNET_STATE_KEY = 'status:magnet:state'
HEATSWITCH_STATUS_KEY = 'status:heatswitch'
HC_BOARD_CURRENT = 'status:highcurrentboard:current'

TS_KEYS = [OUTPUT_VOLTAGE_KEY, INPUT_VOLTAGE_KEY, MAGNET_CURRENT_KEY,
           MAGNET_STATE_KEY, HEATSWITCH_STATUS_KEY, HC_BOARD_CURRENT]

STATUS_KEY = 'status:device:sim921:status'
MODEL_KEY = 'status:device:sim921:model'
FIRMWARE_KEY = 'status:device:sim921:firmware'
SERIALNO_KEY = 'status:device:sim921:sn'

COMMAND_DICT = {}

class SIM960Agent(object):
    def __init__(self, port, redis, redis_ts, baudrate=9600, timeout=0.1, initialize=True):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connect(raise_errors=False)
        time.sleep(.5)
        self.redis = redis
        self.redis_ts = redis_ts

        self.prev_sim_settings = {}
        self.new_sim_settings = {}

        if initialize:
            self.initialize_sim()
        else:
            self.read_default_settings()

    def connect(self, reconnect=False, raise_errors=True):
        """
        Create serial connection with the SIM921. In reality, the SIM921 connection is only up to the USB-to-RS232
        interface, and so disconnects will need to be checked differently from either side of the converter.
        """
        if reconnect:
            self.disconnect()

        try:
            if self.ser.isOpen():
                return
        except Exception:
            pass

        getLogger(__name__).debug(f"Connecting to {self.port} at {self.baudrate}")
        try:
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
            getLogger(__name__).debug(f"port {self.port} connection established")
            return True
        except (SerialException, IOError) as e:
            self.ser = None
            getLogger(__name__).error(f"Conntecting to port {self.port} failed: {e}")
            if raise_errors:
                raise e
            else:
                return False

    def disconnect(self):
        """
        Disconnect from the SIM921 serial connection
        """
        try:
            self.ser.close()
            self.ser = None
        except Exception as e:
            getLogger(__name__).info(f"Exception durring disconnect: {e}")

    def send(self, msg: str, connect=True):
        """
        Send a message to the SIM921 in its desired format.
        The typical message is all caps, terminated with a newline character '\n'
        Commands will be followed by a code, typically a number (e.g. 'RANG 3\n')
        Queries will be followed by a question mark (e.g. 'TVAL?\n')
        The identity query (and a number of other 'special' commands) start with a * (e.g. '*IDN?')
        """
        if connect:
            self.connect()
        msg = msg.strip().upper() + "\n"
        try:
            getLogger(__name__).debug(f"Writing message: {msg}")
            self.ser.write(msg.encode("utf-8"))
            getLogger(__name__).debug(f"Sent {msg} successfully")
        except (SerialException, IOError) as e:
            self.disconnect()
            getLogger(__name__).error(f"Send failed: {e}")
            raise e

    def receive(self):
        """
        Receiving from the SIM921 consists of reading a line, as some queries may return longer strings than others,
        and each query has its own parsing needs (for example: '*IDN?' returns a string with model, serial number,
        firmware, and company, while 'TVAL?' or 'RVAL?' returns the measured temperature/resistance value at the time)
        """
        try:
            data = self.ser.readline().decode("utf-8").strip()
            getLogger(__name__).debug(f"read {data} from SIM921")
            return data
        except (IOError, SerialException) as e:
            self.disconnect()
            getLogger(__name__).debug(f"Send failed {e}")
            raise e

    def reset_sim(self):
        """
        Send a reset command to the SIM device. This should not be used in regular operation, but if the device is not
        working it is a useful command to be able to send.
        BE CAREFUL - This will reset certain parameters which are set for us to read out the thermometer in the
        PICTURE-C cryostat (as of 2020, a LakeShore RX102-A).
        If you do perform a reset, it will then be helpful to restore the 'default settings' which we have determined
        to be the optimal to read out the hardware we have.
        """
        try:
            getLogger(__name__).info(f"Resetting the SIM921!")
            self.send("*RST")
        except IOError as e:
            raise e

    def command(self, command_msg: str):
        """
        A wrapper for the self.send function. This assumes that the command_msg input is a legal command as dictated by
        the manual in picturec/hardware/thermometry/SRS-SIM921-ResistanceBridge-Manual.pdf
        """
        try:
            getLogger(__name__).debug(f"Sending command '{command_msg}' to SIM921")
            self.send(command_msg)
        except IOError as e:
            raise e

    def query(self, query_msg: str):
        """
        A wrapper to both send and receive in one holistic block so that we ensure if a query is sent, and answer is
        received.
        This assumes that the command_msg input is a legal query as dictated by the manual in
        picturec/hardware/thermometry/SRS-SIM921-ResistanceBridge-Manual.pdf
        """
        try:
            getLogger(__name__).debug(f"Querying '{query_msg}' from SIM921")
            self.send(query_msg)
            response = self.receive()
        except Exception as e:
            raise IOError(e)
        return response

    def query_ID(self):
        """
        Specific function to query the SIM921 identity to get its s/n, firmware, and model. Will be used in
        conjunction with store_sim921_id_info to ensure we properly log the .
        """
        try:
            idn_msg = self.query("*IDN?")
        except IOError as e:
            raise e

        try:
            idn_info = idn_msg.split(',')
            model = idn_info[1]
            sn = idn_info[2]
            firmware = idn_info[3]
            getLogger(__name__).info(f"SIM921 Identity - model {model}, s/n:{sn}, firmware {firmware}")
        except Exception as e:
            raise ValueError(f"Illegal format. Check communication is working properly: {e}")

        return [model, sn, firmware]

    def read_default_settings(self):
        """
        Reads all of the default SIM921 settings that are stored in the redis database and reads them into the
        dictionaries which the agent will use to command the SIM921 to change settings. Also reads these now current
        settings into the redis database.
        """
        try:
            for i, j in zip(DEFAULT_SETTING_KEYS, SETTING_KEYS):
                value = get_redis_value(self.redis, i)
                self.prev_sim_settings[j] = value
                self.new_sim_settings[j] = value
                store_redis_data(self.redis, {j: value})
        except RedisError as e:
            raise e

    def initialize_sim(self):
        getLogger(__name__).info(f"Initializing SIM960")

        try:
            self.read_default_settings()
        except IOError as e:
            getLogger(__name__).debug(f"Initialization failed: {e}")
            raise e
        except RedisError as e:
            getLogger(__name__).debug(f"Redis error occurred in initialization of SIM921: {e}")
            raise e


def setup_redis(host='localhost', port=6379, db=0):
    redis = Redis(host=host, port=port, db=db)
    return redis


def setup_redis_ts(host='localhost', port=6379, db=0):
    redis_ts = Client(host=host, port=port, db=db)

    for key in TS_KEYS:
        try:
            redis_ts.create(key)
        except RedisError:
            getLogger(__name__).debug(f"KEY '{key}' already exists")
            pass

    return redis_ts


def store_status(redis, status):
    redis.set(STATUS_KEY, status)


def get_redis_value(redis, key):
    try:
        val = redis.get(key).decode("utf-8")
    except RedisError as e:
        getLogger(__name__).error(f"Error accessing {key} from redis: {e}")
        return None
    return val


def store_sim921_status(redis, status: str):
    redis.set(STATUS_KEY, status)


def store_sim921_id_info(redis, info):
    redis.set(MODEL_KEY, info[0])
    redis.set(SERIALNO_KEY, info[1])
    redis.set(FIRMWARE_KEY, info[2])


def store_redis_data(redis, data):
    for k, v in data.items():
        getLogger(__name__).info(f"Setting key:value - {k}:{v}")
        redis.set(k, v)


def store_redis_ts_data(redis_ts, data):
    for k, v in data.items():
        getLogger(__name__).info(f"Setting key:value - {k}:{v} at {int(time.time())}")
        redis_ts.add(key=k, value=v, timestamp='*')