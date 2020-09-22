from picturec.pcRedis import PCRedis
import logging
import redis
from redis import RedisError
import time

if __name__ == "__main__":
    log = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG)

    # redis = PCRedis(host='127.0.0.1', port=6379, db=0)
    redis = redis.Redis(host='127.0.0.1', port=6379, db=0, socket_keepalive=True)
    channels = ['channel1', 'channel2']

    subbed = False
    while True:
        while not subbed:
            try:
                ps = redis.pubsub()
                [ps.subscribe(c) for c in channels]
                subbed = True
            except RedisError as e:
                log.warning(f"Redis error in subscribing or reconnecting! {e}")
        try:
            msg = ps.get_message()
            if msg:
                log.info(f"Message received: {msg}")
        except RedisError as e:
            log.warning(f"Error in getting message from redis: {e}")
            raise e
            # ps = None
            # subbed = False
        time.sleep(0.001)