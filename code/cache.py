import threading
import time
from typing import Callable


class KeyedCache:
    def __init__(self, ttl_seconds: float = 60.0):
        self._ttl = ttl_seconds
        self._entries: dict = {}
        self._lock = threading.Lock()
        self._keylocks: dict[tuple, threading.Lock] = {}
        # invalidate() bumps gen; get_or_compute discards its write if gen
        # moved during fn() — otherwise a mid-compute invalidate gets clobbered.
        self._gen: dict[int, int] = {}

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
                # Read-only users never invalidate, so expired entries would
                # otherwise pin memory for the lifetime of the worker.
                self._entries.pop(key, None)
                return None
        return value

    def set(self, key: tuple, value) -> None:
        with self._lock:
            self._entries[key] = (time.time(), value)

    def get_or_compute(self, key: tuple, fn: Callable):
        cached = self.get(key)
        if cached is not None:
            return cached
        with self._get_keylock(key):
            cached = self.get(key)
            if cached is not None:
                return cached
            user_id = key[0] if key else None
            with self._lock:
                gen_before = self._gen.get(user_id, 0)
            value = fn()
            with self._lock:
                if self._gen.get(user_id, 0) == gen_before:
                    self._entries[key] = (time.time(), value)
            return value

    def invalidate(self, user_id: int) -> None:
        with self._lock:
            self._gen[user_id] = self._gen.get(user_id, 0) + 1
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
