"""Limite de atendimentos simultâneos (global e por app). Protege o Databricks e a máquina: em vez de empilhar
requisições até tudo ficar lento, recusa na hora com 429 + Retry-After e o operador tenta de novo."""
import threading
from contextlib import contextmanager


class Busy(Exception):
    def __init__(self, scope: str):
        super().__init__(scope)
        self.scope = scope  # "global" | "app"


class ConcurrencyLimiter:
    def __init__(self, global_max: int):
        self._global = threading.BoundedSemaphore(max(1, global_max))
        self._per: dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()

    def _app_sem(self, app: str, limit: int) -> threading.BoundedSemaphore:
        with self._lock:
            return self._per.setdefault(app, threading.BoundedSemaphore(max(1, limit)))

    @contextmanager
    def slot(self, app: str, app_max: int):
        app_sem = self._app_sem(app, app_max)
        if not self._global.acquire(blocking=False):
            raise Busy("global")
        if not app_sem.acquire(blocking=False):
            self._global.release()
            raise Busy("app")
        try:
            yield
        finally:
            app_sem.release()
            self._global.release()
