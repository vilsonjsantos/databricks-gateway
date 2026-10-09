"""Bloqueio de tentativa e erro nas chaves de admin/painel: depois de N falhas de um mesmo cliente numa janela,
as próximas tentativas (mesmo com a chave certa) recebem 429 até a janela esvaziar."""
import threading
import time
from collections import defaultdict, deque


class FailureThrottle:
    def __init__(self, max_failures: int = 10, window_s: float = 300.0, clock=time.monotonic):
        self._max, self._window, self._clock = max_failures, window_s, clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] > self._window:
            q.popleft()

    def blocked(self, who: str) -> bool:
        now = self._clock()
        with self._lock:
            q = self._fails[who]
            self._trim(q, now)
            return len(q) >= self._max

    def record(self, who: str) -> None:
        now = self._clock()
        with self._lock:
            q = self._fails[who]
            self._trim(q, now)
            q.append(now)
            if len(self._fails) > 5000:  # não cresce sem limite sob varredura de IPs
                for k in [k for k, v in self._fails.items() if not v][:2500]:
                    del self._fails[k]
