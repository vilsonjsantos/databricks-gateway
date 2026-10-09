"""Idempotência por `request_id`: duplo clique, retry do navegador ou reenvio após timeout não rodam o agente duas vezes.

Mesma chave (app, usuário, request_id): se já terminou -> devolve a resposta guardada; se ainda está rodando -> espera
a primeira terminar e devolve o mesmo resultado; se a primeira falhou -> quem esperava tenta por conta própria.
Guarda só o texto da resposta por poucos minutos, em memória (some ao reiniciar)."""
import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Entry:
    event: threading.Event = field(default_factory=threading.Event)
    value: dict | None = None
    expires: float = 0.0


class IdempotencyCache:
    def __init__(self, ttl_s: float = 120.0, wait_s: float = 70.0, max_entries: int = 2000):
        self._ttl, self._wait, self._max = ttl_s, wait_s, max_entries
        self._d: dict[tuple, _Entry] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        if len(self._d) > self._max:
            for k in [k for k, e in self._d.items() if e.event.is_set() and e.expires < now][: self._max // 2]:
                del self._d[k]

    def claim(self, key: tuple) -> tuple[str, dict | None]:
        """("hit", resposta) | ("owner", None). O dono DEVE chamar done() ou fail()."""
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            e = self._d.get(key)
            if e is None or (e.event.is_set() and (e.value is None or e.expires < now)):
                self._d[key] = _Entry()
                return "owner", None
            if e.event.is_set():
                return "hit", e.value
        e.event.wait(self._wait)  # primeira ainda rodando: espera
        if e.value is not None:
            return "hit", e.value
        return self.claim(key) if e.event.is_set() else ("owner", None)

    def done(self, key: tuple, value: dict) -> None:
        with self._lock:
            e = self._d.setdefault(key, _Entry())
            e.value, e.expires = value, time.monotonic() + self._ttl
            e.event.set()

    def fail(self, key: tuple) -> None:
        with self._lock:
            e = self._d.get(key)
            if e:
                e.value = None
                e.event.set()
