from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

# Failed-login throttling. Kept in memory: the app runs as a single process, and a
# restart clearing the counters is acceptable.
WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_IP_AND_EMAIL = 5
# Per-email cap regardless of IP, so rotating or spoofed addresses can't keep guessing.
MAX_FAILURES_PER_EMAIL = 20

_failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = Lock()


def _prune(entries: deque[float], now: float) -> None:
    while entries and now - entries[0] > WINDOW_SECONDS:
        entries.popleft()


def is_blocked(ip: str, email: str) -> bool:
    now = time.monotonic()
    with _lock:
        pair = _failures[(ip, email)]
        by_email = _failures[("*", email)]
        _prune(pair, now)
        _prune(by_email, now)
        return len(pair) >= MAX_FAILURES_PER_IP_AND_EMAIL or len(by_email) >= MAX_FAILURES_PER_EMAIL


def record_failure(ip: str, email: str) -> None:
    now = time.monotonic()
    with _lock:
        _failures[(ip, email)].append(now)
        _failures[("*", email)].append(now)


def record_success(ip: str, email: str) -> None:
    with _lock:
        _failures.pop((ip, email), None)


def reset() -> None:
    with _lock:
        _failures.clear()
