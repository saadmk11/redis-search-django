from functools import lru_cache

import redis
from redis_om import has_redisearch


@lru_cache(maxsize=1)
def is_redis_running():
    try:
        return has_redisearch()
    except redis.exceptions.ConnectionError:
        return False
