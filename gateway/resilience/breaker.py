"""Circuit breaker simples para o Databricks: abre após N falhas seguidas e testa de novo depois de `reset_s`."""
import threading
import time
from typing import Callable

CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, failures: int = 5, reset_s: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[str], None] | None = None):
        self._max, self._reset, self._clock, self._on_change = failures, reset_s, clock, on_change
        self._fails = 0
        self._opened_at = 0.0
        self._probe_out = False
        self.state = CLOSED
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == CLOSED:
                return True
            if self.state == OPEN and self._clock() - self._opened_at >= self._reset:
                self._set(HALF_OPEN)
            if self.state == HALF_OPEN and not self._probe_out:
                self._probe_out = True  # só uma chamada de teste por vez
                return True
            return False

    def retry_after(self) -> int:
        with self._lock:
            return max(1, int(self._reset - (self._clock() - self._opened_at)))

    def success(self) -> None:
        with self._lock:
            self._fails, self._probe_out = 0, False
            if self.state != CLOSED:
                self._set(CLOSED)

    def failure(self) -> None:
        with self._lock:
            self._probe_out = False
            self._fails += 1
            if self.state == HALF_OPEN or self._fails >= self._max:
                self._opened_at = self._clock()
                if self.state != OPEN:
                    self._set(OPEN)

    def _set(self, new: str) -> None:
        self.state = new
        if self._on_change:
            self._on_change(new)
