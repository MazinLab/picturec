from logging import getLogger
import numpy as np
import enum
import logging
import time
import threading
from collections import defaultdict
import serial
from serial import SerialException

log = logging.getLogger(__name__)

COMMANDS921 = {'device-settings:sim921:resistance-range': {'command': 'RANG', 'vals': {'20e-3': '0', '200e-3': '1', '2': '2',
                                                                                       '20': '3', '200': '4', '2e3': '5',
                                                                                       '20e3': '6', '200e3': '7',
                                                                                       '2e6': '8', '20e6': '9'}},
               'device-settings:sim921:excitation-value': {'command': 'EXCI', 'vals': {'0': '-1', '3e-6': '0', '10e-6': '1',
                                                                                       '30e-6': '2', '100e-6': '3',
                                                                                       '300e-6': '4', '1e-3': '5',
                                                                                       '3e-3': '6', '10e-3': '7', '30e-3': '8'}},
               'device-settings:sim921:excitation-mode': {'command': 'MODE', 'vals': {'passive': '0', 'current': '1',
                                                                                      'voltage': '2', 'power': '3'}},
               'device-settings:sim921:time-constant': {'command': 'TCON', 'vals': {'0.3': '0', '1': '1', '3': '2', '10': '3',
                                                                                    '30': '4', '100': '5', '300': '6'}},
               'device-settings:sim921:temp-offset': {'command': 'TSET', 'vals': [0.050, 40]},
               'device-settings:sim921:resistance-offset': {'command': 'RSET', 'vals': [1049.08, 63765.1]},
               'device-settings:sim921:temp-slope': {'command': 'VKEL', 'vals': [0, 1e-2]},
               'device-settings:sim921:resistance-slope': {'command': 'VOHM', 'vals': [0, 1e-5]},
               'device-settings:sim921:output-mode': {'command': 'AMAN', 'vals': {'scaled': '0', 'manual': '1'}},
               'device-settings:sim921:manual-vout': {'command': 'AOUT', 'vals': [-10, 10]},
               'device-settings:sim921:curve-number': {'command': 'CURV', 'vals': {'1': '1', '2': '2', '3': '3'}},
               }

COMMANDS960 = {'device-settings:sim960:vout-min-limit': {'command': 'LLIM', 'vals': [-10, 10]},
               'device-settings:sim960:vout-max-limit': {'command': 'ULIM', 'vals': [-10, 10]},
               'device-settings:sim960:vin-setpoint-mode': {'command': 'INPT', 'vals': {'internal': '0', 'external': '1'}},
               'device-settings:sim960:vin-setpoint': {'command': 'SETP', 'vals': [-10, 10]},
               'device-settings:sim960:pid-p:value': {'command': 'GAIN', 'vals': [-1e3, -1e-1]},
               'device-settings:sim960:pid-i:value': {'command': 'INTG', 'vals': [1e-2, 5e5]},
               'device-settings:sim960:pid-d:value': {'command': 'DERV', 'vals': [0, 1e1]},
               'device-settings:sim960:vin-setpoint-slew-enable': {'command': 'RAMP', 'vals': {'off': '0', 'on': '1'}},  # Note: Internal setpoint ramp, NOT magnet ramp
               'device-settings:sim960:vin-setpoint-slew-rate': {'command': 'RATE', 'vals': [1e-3, 1e4]},  # Note: Internal setpoint ramp rate, NOT magnet ramp
               'device-settings:sim960:pid-p:enabled': {'command': 'PCTL', 'vals': {'off': '0', 'on': '1'}},
               'device-settings:sim960:pid-i:enabled': {'command': 'ICTL', 'vals': {'off': '0', 'on': '1'}},
               'device-settings:sim960:pid-d:enabled': {'command': 'DCTL', 'vals': {'off': '0', 'on': '1'}},
               }

COMMAND_DICT={}
COMMAND_DICT.update(COMMANDS960)
COMMAND_DICT.update(COMMANDS921)


def escapeString(string):
    """
    Takes a string and escapes newline characters so they can be logged and display the newline characters in that string
    """
    return string.replace('\n', '\\n').replace('\r', '\\r')

responses960 = {'*IDN?\n': b"Stanford_Research_Systems,SIM960,s/n021840,ver2.17\r\n",
                'LLIM?\n': b"-0.10\r\n",
                'ULIM?\n': b"+10.00\r\n",
                'INPT?\n': b"0\r\n",
                'SETP?\n': b"+0.000\r\n",
                'GAIN?\n': b"-1.6E+1\r\n",
                'INTG?\n': b"+2.0E-1\r\n",
                'DERV?\n': b"+1.0E-5\r\n",
                'RAMP?\n': b"1\r\n",
                'RATE?\n': b"+0.5E-2\r\n",
                'PCTL?\n': b"1\r\n",
                'ICTL?\n': b"1\r\n",
                'DCTL?\n': b"0\r\n",
                'APOL?\n': b"0\r\n",
                'AMAN?\n': b"0\r\n",  # needs a function to flip between manual/PID
                'MMON?\n': b"-00.008339\r\n",  # needs a function to generate plausible vals
                'OMON?\n': b"+00.003277\r\n",  # needs a function to generate plausible vals
                'MOUT?\n': b"+0.000\r\n",
                '*IDN?': b"Stanford_Research_Systems,SIM960,s/n021840,ver2.17\r\n",
                'LLIM?': b"-0.10\r\n",
                'ULIM?': b"+10.00\r\n",
                'INPT?': b"0\r\n",
                'SETP?': b"+0.000\r\n",
                'GAIN?': b"-1.6E+1\r\n",
                'INTG?': b"+2.0E-1\r\n",
                'DERV?': b"+1.0E-5\r\n",
                'RAMP?': b"1\r\n",
                'RATE?': b"+0.5E-2\r\n",
                'PCTL?': b"1\r\n",
                'ICTL?': b"1\r\n",
                'DCTL?': b"0\r\n",
                'APOL?': b"0\r\n",
                'AMAN?': b"0\r\n",  # needs a function to flip between manual/PID
                'MMON?': b"-00.008339\r\n",  # needs a function to generate plausible vals
                'OMON?': b"+00.003277\r\n",  # needs a function to generate plausible vals
                'MOUT?': b"+0.000\r\n"}  # needs a function to generate plausible vals
SERIAL_SIM_CONFIG = {'open': True, 'write_error': False, 'read_error': False, 'responses': responses960}
#NB: The responses should be a list of sent strings and their exact responses eg 'foo\n':'barr\r' or sent strings
# and a callable that given the sent string returns the response string 'foo\n':barr('foo\n') -> 'barr\r'
responses921 = {b'*IDN?\n': b'Stanford_Research_Systems,SIM921,s/n006241,ver3.6\r\n',
                b'TVAL?\n': b"+3.426272E-01\r\n",  # needs a function to generate plausible vals
                b'RVAL?\n': b"+5.003490E+03\r\n",  # needs a function to generate plausible vals
                b'CURV?\n': b"1\r\n",
                b'RANG?\n': b"6\r\n",
                b'EXON?\n': b"1\r\n",
                b'EXCI?\n': b"3\r\n",
                b'MODE?\n': b"2\r\n",
                b'TCON?\n': b"2\r\n",
                b'TSET?\n': b"+9.999999E-02\r\n",
                b'RSET?\n': b"+1.940050E+04\r\n",
                b'VKEL?\n': b"1.000000E-02\r\n",
                b'VOHM?\n': b"9.999998E-06\r\n",
                b'AMAN?\n': b"1\r\n",
                b'AOUT?\n': b"0.00000\r\n",
                b'ATEM?\n': b"0\r\n"}
responses960 = {b'*IDN?\n': b"Stanford_Research_Systems,SIM960,s/n021840,ver2.17\r\n",
                b'LLIM?\n': b"-0.10\r\n",
                b'ULIM?\n': b"+10.00\r\n",
                b'INPT?\n': b"0\r\n",
                b'SETP?\n': b"+0.000\r\n",
                b'GAIN?\n': b"-1.6E+1\r\n",
                b'INTG?\n': b"+2.0E-1\r\n",
                b'DERV?\n': b"+1.0E-5\r\n",
                b'RAMP?\n': b"1\r\n",
                b'RATE?\n': b"+0.5E-2\r\n",
                b'PCTL?\n': b"1\r\n",
                b'ICTL?\n': b"1\r\n",
                b'DCTL?\n': b"0\r\n",
                b'APOL?\n': b"0\r\n",
                b'AMAN?\n': b"0\r\n",  # needs a function to flip between manual/PID
                b'MMON?\n': b"-00.008339\r\n",  # needs a function to generate plausible vals
                b'OMON?\n': b"+00.003277\r\n",  # needs a function to generate plausible vals
                b'MOUT?\n': b"+0.000\r\n"}  # needs a function to generate plausible vals
responses_ls240 = {b'*IDN?\n': b"LSCI,MODEL240-2P,LSA2359,1.9\r\n",
                   b'INTYPE? 1\n': b"1,0,0,0,1,1\r\n",
                   b'INTYPE? 2\n': b"1,0,0,0,1,1\r\n",
                   b'KRDG? 1\n': b"+0292.19\r\n",  # needs a function to generate plausible vals
                   b'KRDG? 2\n': b"+0293.00\r\n",  # needs a function to generate plausible vals
                   b'INNAME? 1\n': b"LN2            \r\n",
                   b'INNAME? 2\n': b"LHE            \r\n"}
responses_currentduino = {b'v': b" 0.20 v\r\n",
                          b'?': b" 374 ?\r\n",  # needs a function to generate plausible vals
                          b'o': b" o\r\n",
                          b'c': b" c\r\n"}
responses_hemtduino = {b'v': b" 0.10 v\r\n",
                       b'?': b" 364 355 379 364 351 349 351 350 348 342 362 353 368 362 353 ?\r\n"}  # needs a function to generate plausible vals

class SimulatedSerial:
    def __init__(self, *args, **kwargs):
        self._lastwrite=''

    def close(self):
        pass

    def write(self, msg):
        if SERIAL_SIM_CONFIG['write_error']:
            raise SerialException('')
        self._lastwrite = msg

    def readline(self):
        if SERIAL_SIM_CONFIG['read_error']:
            raise SerialException('')

        resp = SERIAL_SIM_CONFIG['responses'][self._lastwrite]
        try:
            return resp
        except TypeError:
            resp.encode('utf-8')

    def isOpen(self):
        return SERIAL_SIM_CONFIG['open']


Serial = serial.Serial
def enable_simulator():
    global Serial
    Serial = SimulatedSerial


def disable_simulator():
    global Serial
    Serial = serial.Serial


class SimCommand:
    def __init__(self, schema_key, value=None):
        """
        Initializes a SimCommand. Takes in a redis device-setting:* key and desired value an evaluates it for its type,
        the mapping of the command, and appropriately sets the mapping|range for the command. If the setting is not
        supported, raise a ValueError.

        If no value is specified it will create the command as a query

        """
        if schema_key not in COMMAND_DICT.keys():
            raise ValueError(f'Unknown command: {schema_key}')

        self.range = None
        self.mapping = None
        self.value = value
        self.setting = schema_key

        self.command = COMMAND_DICT[self.setting]['command']
        setting_vals = COMMAND_DICT[self.setting]['vals']

        if isinstance(setting_vals, dict):
            self.mapping = setting_vals
        else:
            self.range = setting_vals
        self._vet()

    def _vet(self):
        """Verifies value agaisnt papping or range and handles necessary casting"""
        if self.value is None:
            return True

        value = self.value
        if self.mapping is not None:
            if value not in self.mapping:
                raise ValueError(f'Invalid value {value}. Options are: {list(self.mapping.keys())}.')
        else:
            try:
                self.value = float(value)
            except ValueError:
                ValueError(f'Invalid value {value}, must be castable to float.')
            if not self.range[0] <= self.value <= self.range[1]:
                raise ValueError(f'Invalid value {value}, must in {self.range}.')

    def __str__(self):
        return f"{self.setting}->{self.value}: {self.sim_string}"

    @property
    def is_query(self):
        return self.value is None

    @property
    def sim_string(self):
        """
        Returns the command string for the SIM.
        """
        if self.is_query:
            return self.sim_query_string
        v = self.mapping[self.value] if self.range is None else self.value
        return f"{self.command} {v}"

    @property
    def sim_query_string(self):
        """ Returns the corresponding command string to query for the setting"""
        return f"{self.command}?"


class SerialDevice:
    def __init__(self, port, baudrate=115200, timeout=0.1, name=None, terminator='\n'):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.name = name if name else self.port
        self.terminator = terminator
        self._rlock = threading.RLock()

    def _preconnect(self):
        """
        Override to perform an action immediately prior to connection.
        Function should raise IOError if the serial device should not be opened.
        """
        pass

    def _postconnect(self):
        """
        Override to perform an action immediately after connection. Default is to sleep for twice the timeout
        Function should raise IOError if there are issues with the connection.
        Function will not be called if a connection can not be established or already exists.
        """
        time.sleep(2*self.timeout)

    def _predisconnect(self):
        """
        Override to perform an action immediately prior to disconnection.
        Function should raise IOError if the serial device should not be opened.
        """
        pass

    def connect(self, reconnect=False, raise_errors=True):
        """
        Connect to a serial port. If reconnect is True, closes the port first and then tries to reopen it. First asks
        the port if it is already open. If so, returns nothing and allows the calling function to continue on. If port
        is not already open, first attempts to create a serial.Serial object and establish the connection.
        Raises an IOError if the serial connection is unable to be established.
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
            self._preconnect()
            self.ser = Serial(port=self.port, baudrate=self.baudrate, timeout=self.timeout)
            self._postconnect()
            getLogger(__name__).info(f"port {self.port} connection established")
            return True
        except (serial.SerialException, IOError) as e:
            self.ser = None
            getLogger(__name__).error(f"Conntecting to port {self.port} failed: {e}")
            if raise_errors:
                raise e
            return False

    def disconnect(self):
        """
        First closes the existing serial connection and then sets the ser attribute to None. If an exception occurs in
        closing the port, log the error but do not raise.
        """
        try:
            self._predisconnect()
            self.ser.close()
            self.ser = None
        except Exception as e:
            getLogger(__name__).info(f"Exception during disconnect: {e}")

    def format_msg(self, msg:str):
        """Subclass may implement to apply hardware specific formatting"""
        if msg and msg[-1] != self.terminator:
            msg = msg+self.terminator
        return msg.encode('utf-8')

    def send(self, msg: str, connect=True):
        """
        Send a message to a serial port. If connect is True, try to connect to the serial port before sending the
        message. Formats message according to the class's format_msg function before attempting to write to serial port.
        If IOError or SerialException occurs, first disconnect from the serial port, then log and raise the error.
        """
        with self._rlock:
            if connect:
                self.connect()
            try:
                getLogger(__name__).debug(f"Sending '{msg}'")
                self.ser.write(msg)
            except (serial.SerialException, IOError) as e:
                self.disconnect()
                getLogger(__name__).error(f"...failed: {e}")
                raise e

    def receive(self):
        """
        Receives a message from a serial port. Assumes that the message consists of a single line. If a message is
        received, decode it and strip it of any newline characters. In the case of an error or serialException,
        disconnects from the serial port and raises an IOError.
        """
        with self._rlock:
            try:
                data = self.ser.readline().decode("utf-8").strip()
                getLogger(__name__).debug(f"read {escapeString(data)} from {self.name}")
                return data
            except (IOError, serial.SerialException) as e:
                self.disconnect()
                getLogger(__name__).debug(f"Send failed {e}")
                raise IOError(e)

    def query(self, cmd: str, **kwargs):
        """
        Send command and wait for a response, kwargs passed to send, raises only IOError
        """
        with self._rlock:
            try:
                self.send(cmd, **kwargs)
                time.sleep(.1)
                return self.receive()
            except Exception as e:
                raise IOError(e)


class SimDevice(SerialDevice):
    def __init__(self, name, port, baudrate=9600, timeout=0.1, connect=True, initilizer=None):
        """The initialize callback is called after _simspecificconnect iff _initialized is false. The callback
        will be passed this object and should raise IOError if the device can not be initialized. If it completes
        without exception (or is not specified) the device will then be considered initialized
        The .initialized_at_last_connect attribute may be checked to see if initilization ran.
        """

        super().__init__(port, baudrate, timeout, name=name)

        self.sn = None
        self.firmware = None
        self.mainframe_slot = None
        self.mainframe_exitstring = 'XYZ'
        self.initilizer = initilizer
        self._monitor_thread = None
        self._initialized = False
        self.initialized_at_last_connect = False
        if connect:
            self.connect(raise_errors=False)

    def _walk_mainframe(self, name):
        """
        Walk the mainframe to find self.name in the device models

        raise KeyError if not present
        raise RuntimeError if not in mainframe mode

        will populate self.firmware and self.sn on success
        """
        id_msg = self.query("*IDN?", connect=False)
        manufacturer, model, _, _ = id_msg.split(",")
        if model != 'SIM900':
            raise RuntimeError('Mainframe not present')

        for slot in range(1, 9):
            self.send(f"CONN {slot}, '{self.mainframe_exitstring}'")
            time.sleep(.1)
            id_msg = self.query("*IDN?", connect=False)
            try:
                manufacturer, model, _, _ = id_msg.split(",")
            except Exception:
                if id_msg == '':
                    log.debug(f"No device in mainframe at slot {slot}")
                    pass
                else:
                    raise IOError(f"Bad response to *IDN?: '{id_msg}'")
            if model == name:
                self.mainframe_slot=slot
                return slot
            else:
                self.send(f"{self.mainframe_exitstring}\n", connect=False)
        raise KeyError(f'{name} not found in any mainframe slot')

    def _predisconnect(self):
        if self.mainframe_slot is not None:
            self.send(f"{self.mainframe_exitstring}\n", connect=False)

    def reset(self):
        """
        Send a reset command to the SIM device. This should not be used in regular operation, but if the device is not
        working it is a useful command to be able to send.
        BE CAREFUL - This will reset certain parameters which are set for us to read out the thermometer in the
        PICTURE-C cryostat (as of 2020, a LakeShore RX102-A).
        If you do perform a reset, it will then be helpful to restore the 'default settings' which we have determined
        to be the optimal to read out the hardware we have.
        """
        log.info(f"Resetting the {self.name}!")
        self.send("*RST")

    def format_msg(self, msg: str):
        return super().format_msg(msg.strip().upper())

    def _simspecificconnect(self):
        pass

    def _preconnect(self):
        time.sleep(1)

    def _postconnect(self):
        try:
            self.send(self.mainframe_exitstring)
            self._walk_mainframe(self.name)
        except RuntimeError:
            pass

        id_msg = self.query("*IDN?", connect=False)
        try:
            manufacturer, model, self.sn, self.firmware = id_msg.split(",")  # See manual page 2-20
        except ValueError:
            log.debug(f"Unable to parse IDN response: '{id_msg}'")
            manufacturer, model, self.sn, self.firmware = [None]*4

        if not (manufacturer == "Stanford_Research_Systems" and model == self.name):
            msg = f"Unsupported device: {manufacturer}/{model} (idn response = '{id_msg}')"
            log.critical(msg)
            raise IOError(msg)

        self._simspecificconnect()

        if self.initilizer and not self._initialized:
            self.initilizer(self)
            self._initialized = True

    @property
    def device_info(self):
        self.connect(reconnect=False)
        return dict(model=self.name, firmware=self.firmware, sn=self.sn)

    def apply_schema_settings(self, settings_to_load):
        """
        Configure the sim device with a dict of redis settings via SimCommand translation

        In the event of an IO error configuration is aborted and the IOError raised. Partial configuration is possible
        In the even that a setting is not valid it is skipped

        Returns the sim settings and the values per the schema
        """
        ret = {}
        for setting, value in settings_to_load.items():
            try:
                cmd = SimCommand(setting, value)
                log.debug(cmd)
                self.send(cmd.sim_string)
                ret[setting] = value
            except ValueError as e:
                log.warning(f"Skipping bad setting: {e}")
                ret[setting] = self.query(cmd.sim_query_string)
        return ret

    def read_schema_settings(self, settings):
        ret = {}
        for setting in settings:
            cmd = SimCommand(setting)
            ret[setting] = self.query(cmd.sim_query_string)
        return ret

    def monitor(self, interval: float, monitor_func: (callable, tuple), value_callback: (callable, tuple) = None):
        """
        Given a monitoring function (or is of the same) and either one or the same number of optional callback
        functions call the monitors every interval. If one callback it will get all the values in the order of the
        monitor funcs, if a list of the same number as of monitorables each will get a single value.

        Monitor functions may not return None.

        When there is a 1-1 correspondence the callback is not called in the event of a monitoring error.
        If a single callback is present for multiple monitor functions values that had errors will be sent as None.
        Function must accept as many arguments as monitor functions.
        """
        if not isinstance(monitor_func, (list, tuple)):
            monitor_func = (monitor_func,)
        if value_callback is not None and not isinstance(value_callback, (list, tuple)):
            value_callback = (value_callback,)
        if not (value_callback is None or len(monitor_func) == len(value_callback) or len(value_callback) == 1):
            raise ValueError('When specified, the number of callbacks must be one or the number of monitor functions')

        def f():
            while True:
                vals = []
                print(monitor_func)
                for func in monitor_func:
                    try:
                        vals.append(func)
                    except IOError as e:
                        log.error(f"Failed to poll {func}: {e}")
                        vals.append(None)

                if value_callback is not None:
                    if len(value_callback) > 1 or len(monitor_func) == 1:
                        for v, cb in zip(vals, value_callback):
                            if v is not None:
                                try:
                                    cb(v)
                                except Exception as e:
                                    log.error(f"Callback {cb} raised {e} when called with {v}.")
                    else:
                        cb = value_callback[0]
                        try:
                            cb(*vals)
                        except Exception as e:
                            log.error(f"Callback {cb} raised {e} when called with {v}.")

                time.sleep(interval)

        self._monitor_thread = threading.Thread(target=f, name='Monitor Thread')
        self._monitor_thread.daemon = True
        self._monitor_thread.start()


class MagnetState(enum.Enum):
     PID = enum.auto()
     MANUAL = enum.auto()


class SIM960(SimDevice):

    MAX_CURRENT_SLOPE = .005  # 5 mA/s
    MAX_CURRENT = 10.0
    OFF_SLOPE = 0.5

    def __init__(self, port, baudrate=9600, timeout=0.1, connect=True, initializer=None):
        """
        Initializes SIM960 agent. First hits the superclass (SerialDevice) init function. Then sets class variables which
        will be used in normal operation. If connect mainframe is True, attempts to connect to the SIM960 via the SIM900
        in mainframe mode. Raise IOError if an invalid slot or exit string is given (or if no exit string is given).
        """
        self.polarity = 'negative'
        self.last_input_voltage = None
        self.last_output_voltage = None
        self._last_manual_change = time.time() - 1  # This requires that in the case the program fails that systemd does
        # not try to restart the sim960Agent program more frequently than once per second (i.e. if sim960Agent crashes,
        # hold off on trying to start it again for at least 1s)
        super().__init__('SIM960', port, baudrate, timeout, connect=connect, initilizer=initializer)

    @property
    def state(self):
        """
        Return offline, online, or configured

        NB configured implies that settings have not been lost due to a power cycle
        """
        try:
            polarity = self.query("APOL?", connect=True)
            return 'configured' if int(polarity)==0 else 'online'
        except IOError:
            return 'offline'

    def _simspecificconnect(self):
        polarity = self.query("APOL?", connect=False)
        if int(polarity) == 1:
            self.send("APOL 0", connect=False)  # Set polarity to negative, fundamental to the wiring.
            polarity = self.query("APOL?", connect=False)
            if polarity != '0':
                msg = f"Polarity query returned {polarity}. Setting PID loop polarity to negative failed."
                log.critical(msg)
                raise IOError(msg)
            self._initialized = False
            self.initialized_at_last_connect = False
        else:
            self._initialized = polarity == '0'
            self.initialized_at_last_connect = self._initialized


    @property
    def input_voltage(self):
        """Read the voltage being sent to the input monitor of the SIM960 from the SIM921"""
        iv = float(self.query("MMON?"))
        self.last_input_voltage = iv
        return iv


    @property
    def output_voltage(self):
        """Report the voltage at the output of the SIM960. In manual mode, this will be explicitly controlled using MOUT
        and in PID mode this will be the value set by the function Output = P(e + I * int(e) + D * derv(e)) + Offset"""
        ov = float(self.query("OMON?"))
        self.last_output_voltage = ov
        return ov


    @staticmethod
    def _out_volt_2_current(volt:float, inverse=False):
        """
        Converts a sim960 output voltage to the expected current.
        TODO: require volt param to be float
        :param volt:
        :param inverse:
        :return:
        """
        if inverse:
            return volt/1.0
        else:
            return 1.0*volt

    @property
    def setpoint(self):
        """ return the current that is currently commanded by the sim960 """
        return self._out_volt_2_current(self.output_voltage)

    @property
    def manual_current(self):
        """
        return the manual current setpoint. Queries the manual output voltage and converts that to the expected current.
        'MOUT?' query returns the value of the user-specified output voltage. This will only be the output voltage in manual mode (not PID).
        """
        manual_voltage_setpoint = float(self.query("MOUT?"))
        return self._out_volt_2_current(manual_voltage_setpoint)

    @manual_current.setter
    def manual_current(self, x: float):
        """ will clip to the range 0,MAX_CURRENT and enforces a maximum absolute current derivative """
        if not self._initialized:
            raise ValueError('Sim is not initialized')
        x = min(max(x, 0), self.MAX_CURRENT)
        # TODO: There should be something about a discrete jump in here too
        delta = abs((self.setpoint - x)/(time.time()-self._last_manual_change))
        if delta > self.MAX_CURRENT_SLOPE:
            raise ValueError('Requested current delta unsafe')
        self.mode = MagnetState.MANUAL
        self.send(f'MOUT {self._out_volt_2_current(x, inverse=True):.3f}')  # Response, there's mV accuracy, so at least 3 decimal places
        self._last_manual_change = time.time()

    def kill_current(self):
        """Immediately kill the current"""
        self.mode=MagnetState.MANUAL
        self.send("MOUT 0")

    @property
    def mode(self):
        """ Returns MagnetState or raises IOError (which means we don't know!) """
        return MagnetState.MANUAL if self.query('AMAN?') == '0' else MagnetState.PID

    @mode.setter
    def mode(self, value: MagnetState):
        """ Set the magnet state, state may not be set of Off directly.
        If transistioning to manual ensure that the manual current doesn't hiccup
        """
        with self._rlock:
            mode = self.mode
            if mode == value:
                return
            if value == MagnetState.MANUAL:
                self.manual_current = self.setpoint
                self.send("AMAN 0")
                #NB no need to set the _lat_manual_change time as we arent actually changing the current
            else:
                self.send("AMAN 1")


class SIM921(SimDevice):
    def __init__(self, port, timeout=0.1, connect=True, connection_callback=None):
        super().__init__(name='SIM921', port=port, baudrate=9600, timeout=timeout, connect=connect, connection_callback=connection_callback)
        self.scale_units = 'resistance'
        self.last_voltage = None
        self.last_monitored_values = None
        self._monitor_thread = None
        self.last_voltage_read = None
        self.last_temp_read = None
        self.last_resistance_read = None

    def _simspecificconnect(self):
        # Ensure that the scaled output will be proportional to the resistance error. NOT the temperature error. The
        # resistance spans just over 1 order of magnitude (~1-64 kOhms) while temperature spans 4 (5e-2 - 4e2 K).
        self.send("ATEM 0", connect=False)
        atem = self.query("ATEM?", connect=False)
        if atem != '0':
            msg = (f"Setting ATEM=0 failed, got '{atem}'. Zero, indicating voltage scale units are in resistance, "
                   "is required. DO NOT OPERATE! Exiting.")
            log.critical(msg)
            raise IOError(msg)

        # Make sure that the excitation is turned on. If not successful, exit the program
        # TODO: This can be like the vin-setpoint-slew-enable for the SIM960 (
        self.send("EXON 1", connect=False)
        exon = self.query("EXON?", connect=False)
        if exon != '1':
            msg = f"EXON=1 failed, got '{exon}'. Unable to enable excitation and unable to operate!"
            log.critical(msg)
            raise IOError(msg)

    def temp(self):
        # TODO: Make this (and resistance) more amenable to the SimCommand.sim_query_string property?
        temp = self.query("TVAL?")
        self.last_temp_read = temp
        return temp

    def resistance(self):
        res = self.query("RVAL?")
        self.last_resistance_read = res
        return res

    def output_voltage(self):
        aman = self.query("AMAN?")
        if aman == "1":
            log.debug("SIM921 voltage output is in manual mode!")
            voltage = self.query("AOUT?")
        elif aman == "0":
            log.debug("SIM921 voltage output is in scaled mode!")
            voltage = float(self.query("VOHM?")) * float(self.query("RDEV?"))
        else:
            log.critical(f"SIM921 voltage output is in an unknown mode! -> {aman}")
            raise ValueError(f"SIM921 voltage output is in an unknown mode! -> {aman}")
        self.last_voltage_read = voltage
        return voltage

    def temp_and_resistance(self):
        return {'temperature': self.temp, 'resistance': self.resistance}

    def _load_calibration_curve(self, curve_num: int, curve_type, curve_name: str, file:str=None):
        """
        This is an engineering function for the SIM921 device. In normal operation of the fridge, the user should never
        have to load a curve in. This should only ever be used if (1) a new curve becomes available, (2) the
        thermometer used by the SIM921 is changed out for a new one, or (3) the original curve becomes corrupted.
        Currently (21 July 2020) designed specifically to read in the LakeShore RX-102-A calibration curve, but can be
        modified without difficulty to take in other curves. The command syntax will not change for loading the curve
        onto the SIM921, only the np.loadtxt() and data manipulation of the curve data itself. As long as the curve
        is in a format where resistance[n] < resistance[n+1] for all points n on the curve, it can be loaded into the
        SIM921 instrument.
        """
        if curve_num not in (1, 2, 3):
            log.error(f"SIM921 only accepts 1, 2, or 3 as the curve number")
            return None

        CURVE_TYPE_DICT = {'linear': '0', 'semilogt': '1', 'semilogr': '2', 'loglog': '3'}
        if curve_type not in CURVE_TYPE_DICT.keys():
            log.error(f"Invalid calibration curve type for SIM921. Valid types are {CURVE_TYPE_DICT.keys()}")
            return None

        if file is None:
            import pkg_resources as pkg
            file = pkg.resource_filename('hardware.thermometry.RX-102A', 'RX-102A_Mean_Curve.tbl')

        log.info(f"Curve data at {file}")

        try:
            curve_data = np.loadtxt(file)
            temp_data = np.flip(curve_data[:, 0], axis=0)
            res_data = np.flip(curve_data[:, 1], axis=0)
        except OSError:
            log.error(f"Could not find curve data file.")
            raise ValueError(f"{file} couldn't be loaded.")
        except IndexError:
            raise ValueError(f"{file} couldn't be loaded.")

        log.info(f"Attempting to initialize curve {curve_num}, type {curve_type}")
        try:
            self.send(f"CINI {curve_num}, {CURVE_TYPE_DICT[curve_type]}, {curve_name}")
            for t, r in zip(temp_data, res_data):
                self.send(f"CAPT {curve_num}, {r}, {t}")
                time.sleep(0.1)
        except IOError as e:
            raise e
        log.info(f"Successfully loaded curve {curve_num} - '{curve_name}'!")
