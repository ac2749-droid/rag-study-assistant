"""A tiny in-memory sliding-window rate limiter (one process, which is how this app runs)."""
import threading
import time
from collections import defaultdict, deque

_now = time.monotonic  # indirection so tests can move the clock


class RateLimiter:
    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window = window_seconds
        self._events: dict[object, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def check(self, key: object, limit: int) -> int:
        """Record one use of `key` if it is under `limit` per window and return 0. Otherwise return how many
        whole seconds until it may try again (and record nothing)."""
        with self._lock:
            now = _now()
            events = self._events[key]
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= limit:
                return int(self.window - (now - events[0])) + 1
            events.append(now)
            return 0


ask_limiter = RateLimiter()
