from picturec.magnetduinoAgent import Currentduino
import logging
import picturec.magnetduinoAgent as ma

if __name__=="__main__":
    logging.basicConfig()
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)

    redis = ma.setup_redis(host='localhost', port=6379, db=0)
    redis_ts = ma.setup_redis_ts(host='localhost', port=6379, db=0)

    currentduino = Currentduino(port='/dev/currentduino', redis=redis, redis_ts=redis_ts, baudrate=115200, timeout=0.1)

    currentduino.initialize_heat_switch()
    currentduino.run()