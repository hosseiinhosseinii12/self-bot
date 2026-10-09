"""In-memory token bucket. Per-user (Telegram) and per-IP (Flask)."""
import threading
import time
from collections import defaultdict


class TokenBucket:
    def __init__(self, rate: int, per_seconds: float = 60.0):
        self.rate = rate
        self.per = per_seconds
        self.tokens = defaultdict(lambda: float(rate))
        self.last = defaultdict(time.time)
        self.lock = threading.Lock()

    def allow(self, key) -> bool:
        with self.lock:
            now = time.time()
            elapsed = now - self.last[key]
            refill = elapsed * (self.rate / self.per)
            self.tokens[key] = min(self.rate, self.tokens[key] + refill)
            self.last[key] = now
            if self.tokens[key] >= 1:
                self.tokens[key] -= 1
                return True
            return False


# 30 messages/minute per user
user_limiter = TokenBucket(rate=30, per_seconds=60.0)
# 60 requests/minute per IP for the web panels
ip_limiter = TokenBucket(rate=60, per_seconds=60.0)