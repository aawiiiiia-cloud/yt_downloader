"""Bounded recent-rate sampling and quality-preserving original-source retries."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class ImageTransferPolicy:
    read_size: int = 16 * 1024
    rate_window: float = 3.0
    startup_grace: float = 3.0
    slow_window: float = 4.0
    slow_windows: int = 2
    minimum_rate: float = 32 * 1024
    max_slow_sources: int = 3
    original_idle_timeout: float = 15.0
    log_interval: float = 5.0


class ImageRateMeter:
    """Rate is recent, slow decisions require separate consecutive windows."""

    def __init__(self, started: float, policy: ImageTransferPolicy):
        self.started = started
        self.policy = policy
        self.samples = deque([(started, 0)])
        self.speed: float | None = None
        self.slow_count = 0
        self._slow_started: float | None = None
        self._slow_bytes = 0

    def update(self, now: float, received: int) -> float | None:
        cutoff = now - self.policy.rate_window
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()
        baseline_time, baseline_bytes = self.samples[0]
        elapsed = now - baseline_time
        self.speed = max(0, received - baseline_bytes) / elapsed if elapsed > 0 else None
        # Sampling at <=10 Hz bounds memory even on a fast connection.
        if now - self.samples[-1][0] >= 0.1:
            self.samples.append((now, received))
        if self._slow_started is None:
            if now - self.started >= self.policy.startup_grace:
                self._slow_started, self._slow_bytes = now, received
        elif now - self._slow_started >= self.policy.slow_window:
            rate = (received - self._slow_bytes) / (now - self._slow_started)
            self.slow_count = self.slow_count + 1 if rate < self.policy.minimum_rate else 0
            self._slow_started, self._slow_bytes = now, received
        return self.speed

    def should_switch(self, received: int, expected: int | None) -> bool:
        if self.slow_count < self.policy.slow_windows:
            return False
        if expected is not None:
            remaining = expected - received
            # Do not throw away a complete or nearly complete image.
            if remaining <= max(64 * 1024, (self.speed or 0) * 2):
                return False
        return True


class OriginalSourcePlan:
    """Try other equal-quality originals before retrying the same failed URL.

    Slow sources are NOT failed originals. After bounded probing, retry the best
    slow original without the low-rate guard. A display fallback is only tried
    if all originals really fail; no partial bytes are combined across sources.
    """

    def __init__(self, count: int, originals: tuple[int, ...], ranks: dict[int, tuple],
                 max_slow_sources: int = 3):
        self.originals = sorted(originals, key=lambda index: ranks.get(index, (0, 0, index)))
        self.fallbacks = [index for index in range(count) if index not in originals]
        self.slow: dict[int, float] = {}
        self.max_slow_sources = max_slow_sources

    def mark_slow(self, index: int, speed: float | None) -> None:
        self.slow[index] = speed or 0

    def attempts(self) -> Iterator[tuple[int, int, bool]]:
        tried: set[int] = set()
        for index in self.originals:
            if len(self.slow) >= self.max_slow_sources:
                break
            tried.add(index)
            yield index, 1, len(self.originals) > 1
        # Do not turn a working (but slow) original into a watermarked fallback.
        relaxed = sorted(self.slow, key=lambda index: -self.slow[index])
        for index in relaxed:
            yield index, 2, False
        for index in self.originals:
            if index not in tried:
                yield index, 1, False
        for index in self.originals:
            if index not in self.slow:
                yield index, 2, False
        for index in self.fallbacks:
            yield index, 1, False
            yield index, 2, False
