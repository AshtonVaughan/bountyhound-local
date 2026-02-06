"""Redis manager for task coordination and live state."""

import json
import os
import redis


REDIS_URL = os.environ.get("BHL_REDIS_URL", "redis://localhost:6379/0")

_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)


class TaskQueue:
    """Lightweight task coordination on top of Celery's Redis broker."""

    PREFIX = "bhl:"

    @staticmethod
    def set_hunt_state(hunt_id: int, state: dict):
        r = get_redis()
        r.set(f"{TaskQueue.PREFIX}hunt:{hunt_id}:state", json.dumps(state), ex=86400)

    @staticmethod
    def get_hunt_state(hunt_id: int) -> dict | None:
        r = get_redis()
        raw = r.get(f"{TaskQueue.PREFIX}hunt:{hunt_id}:state")
        return json.loads(raw) if raw else None

    @staticmethod
    def set_worker_status(worker_id: str, status: dict):
        r = get_redis()
        r.hset(f"{TaskQueue.PREFIX}workers", worker_id, json.dumps(status))
        r.expire(f"{TaskQueue.PREFIX}workers", 600)

    @staticmethod
    def get_all_worker_status() -> dict:
        r = get_redis()
        raw = r.hgetall(f"{TaskQueue.PREFIX}workers")
        return {k: json.loads(v) for k, v in raw.items()}

    @staticmethod
    def publish_finding(hunt_id: int, finding: dict):
        r = get_redis()
        r.rpush(f"{TaskQueue.PREFIX}hunt:{hunt_id}:findings", json.dumps(finding))

    @staticmethod
    def get_findings(hunt_id: int) -> list[dict]:
        r = get_redis()
        raw = r.lrange(f"{TaskQueue.PREFIX}hunt:{hunt_id}:findings", 0, -1)
        return [json.loads(x) for x in raw]

    @staticmethod
    def set_target_lock(domain: str, ttl: int = 3600) -> bool:
        """Prevent two workers from hunting the same target simultaneously."""
        r = get_redis()
        return r.set(f"{TaskQueue.PREFIX}lock:{domain}", "1", nx=True, ex=ttl)

    @staticmethod
    def release_target_lock(domain: str):
        r = get_redis()
        r.delete(f"{TaskQueue.PREFIX}lock:{domain}")

    @staticmethod
    def is_target_locked(domain: str) -> bool:
        r = get_redis()
        return r.exists(f"{TaskQueue.PREFIX}lock:{domain}") > 0

    @staticmethod
    def increment_stat(key: str, amount: int = 1):
        r = get_redis()
        r.incrby(f"{TaskQueue.PREFIX}stats:{key}", amount)

    @staticmethod
    def get_stats() -> dict:
        r = get_redis()
        keys = r.keys(f"{TaskQueue.PREFIX}stats:*")
        stats = {}
        for k in keys:
            name = k.replace(f"{TaskQueue.PREFIX}stats:", "")
            stats[name] = int(r.get(k) or 0)
        return stats

    @staticmethod
    def store_cross_target_pattern(pattern: dict):
        r = get_redis()
        r.rpush(f"{TaskQueue.PREFIX}cross_patterns", json.dumps(pattern))

    @staticmethod
    def get_cross_target_patterns() -> list[dict]:
        r = get_redis()
        raw = r.lrange(f"{TaskQueue.PREFIX}cross_patterns", 0, -1)
        return [json.loads(x) for x in raw]
