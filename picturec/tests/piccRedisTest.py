from picturec.pcRedis import PCRedis
import logging
import redis
import time

import socket

if __name__ == "__main__":
    log = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(message)s')

    channels = [f'c{i}' for i in range(20)]
    redis_con = None
    subs = {c: None for c in channels}
    subbed = {c: False for c in channels}
    while True:
        try:
            if redis_con is None:
                redis_con = redis.Redis(host='127.0.0.1', port=6379, db=0, socket_timeout=None, socket_keepalive=True)
                # redis_con = redis.Redis(host='127.0.0.1', port=6379, db=0, socket_timeout=None, socket_keepalive=True, socket_keepalive_options={})

            for c in channels:
                if subs[c] is None or (subbed[c] == False):
                    log.debug(f"{time.time()}")
                    log.info(f"Subscribing to channel: {c}")
                    subs[c] = redis_con.pubsub()
                    subs[c].subscribe(c)
                    subbed[c] = True
                    log.debug(f"{time.time()}")
        except redis.ConnectionError as e:
            log.warning(f"Redis connection error during subscription! {e}")
            redis_con.close()
            redis_con = None
        except redis.PubSubError as e:
            log.warning(f"Redis error in subscribing or reconnecting! {e}")

        for c in channels:
            if subbed[c]:
                subs[c].check_health()

        if redis_con:
            for c in channels:
                try:
                    msg = subs[c].get_message()
                    if msg:
                        log.info(f"Message received: {msg}")
                except redis.ConnectionError as e:
                    log.warning(f"Redis connection error during message reception for {c}! {e}")
                    subs[c].unsubscribe()
                    subbed[c] = False
                    # subs[c].subscribe(subs[c].channels)
                except redis.PubSubError as e:
                    log.warning(f"Redis error in subscribing or reconnecting after subscription error! {e}")

        time.sleep(0.01)
