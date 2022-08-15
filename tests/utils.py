import redis
from redis_om import has_redisearch


def is_redis_running():
    try:
        return has_redisearch()
    except redis.exceptions.ConnectionError:
        return False
