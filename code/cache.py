import threading
import time
from typing import Callable


class KeyedCache:
    def __init__(self, ttl_seconds: float = 60.0):
        self._ttl = ttl_seconds
        self._entries: dict = {}
        self._lock = threading.Lock()
        self._keylocks: dict[tuple, threading.Lock] = {}

    def _get_keylock(self, key: tuple) -> threading.Lock:
        with self._lock:
            lock = self._keylocks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._keylocks[key] = lock
            return lock

    def get(self, key: tuple):
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.time() - ts >= self._ttl:
            return None
        return value

    def set(self, key: tuple, value) -> None:
        with self._lock:
            self._entries[key] = (time.time(), value)

    def get_or_compute(self, key: tuple, fn: Callable):
        cached = self.get(key)
        if cached is not None:
            return cached
        # Per-key lock serializes concurrent misses so fn() runs once.
        with self._get_keylock(key):
            cached = self.get(key)
            if cached is not None:
                return cached
            value = fn()
            self.set(key, value)
            return value

    def invalidate(self, user_id: int) -> None:
        # Keys are tuples with user_id as the first element.
        with self._lock:
            for k in list(self._entries.keys()):
                if k and k[0] == user_id:
                    self._entries.pop(k, None)
            for k in list(self._keylocks.keys()):
                if k and k[0] == user_id:
                    self._keylocks.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._keylocks.clear()
