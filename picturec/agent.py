from logging import getLogger
import serial
import time

class SerialAgent:
    def __init__(self, port, baudrate=9600, timeout=0.1, name=''):
        self.ser = None
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.name = name if self.name else self.port

    def connect(self, reconnect=False, raise_errors=True, post_connect_sleep=0.2):
        """
        Create serial connection with the SIM921. In reality, the SIM921 connection is only up to the USB-to-RS232
        interface, and so disconnects will need to be checked differently from either side of the converter.

        #TODO What do you mean "only up to...so disconnects will ..."?
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
            time.sleep(post_connect_sleep)
            return True
        except (serial.SerialException, IOError) as e:
            self.ser = None
            getLogger(__name__).error(f"Conntecting to port {self.port} failed: {e}")
            if raise_errors:
                raise e
            return False

    def disconnect(self):
        """
        Disconnect from the SIM921 serial connection
        """
        try:
            self.ser.close()
            self.ser = None
        except Exception as e:
            getLogger(__name__).info(f"Exception during disconnect: {e}")

    def send(self, msg: str, connect=True, terminator='\n'):
        """
        Send a message to the SIM921 in its desired format.
        The typical message is all caps, terminated with a newline character '\n'
        Commands will be followed by a code, typically a number (e.g. 'RANG 3\n')
        Queries will be followed by a question mark (e.g. 'TVAL?\n')
        The identity query (and a number of other 'special' commands) start with a * (e.g. '*IDN?')
        """
        if connect:
            self.connect()
        msg = msg.strip().upper() + terminator
        try:
            getLogger(__name__).debug(f"Sending '{msg}'")  # Not the '' allow clearly logging empty sends
            self.ser.write(msg.encode("utf-8"))
            getLogger(__name__).debug(f"Sent '{msg}' successfully")
        except (serial.SerialException, IOError) as e:
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
            getLogger(__name__).debug(f"read {data} from {self.name}")
            return data
        except (IOError, serial.SerialException) as e:
            self.disconnect()
            getLogger(__name__).debug(f"Send failed {e}")
            raise e