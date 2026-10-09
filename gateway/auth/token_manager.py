"""Gerenciador do token do Databricks: renova sozinho, nunca derruba o processo.

Camadas (em ordem): token em memória -> renovação silenciosa (refresh token) -> login interativo/automatizado
(navegador). Se tudo falhar entra em DEGRADED, aplica backoff e alerta; as requisições recebem AuthUnavailable
(o gateway responde 503 + Retry-After) em vez de ficar tentando abrir navegador a cada chamada.

As funções `silent` e `interactive` são injetadas (ver databricks_oauth.py), por isso a lógica é testável sem Databricks.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("gateway.auth")

OK, DEGRADED, STARTING = "OK", "DEGRADED", "STARTING"


@dataclass
class Token:
    value: str
    expires_at: float  # epoch


class AuthUnavailable(Exception):
    def __init__(self, retry_after: float):
        super().__init__("autenticação indisponível")
        self.retry_after = max(1, int(retry_after))


class TokenManager:
    def __init__(self, silent: Callable[[], Token] | None, interactive: Callable[[], Token] | None,
                 margin_s: float = 300, backoff_s: float = 30, backoff_max_s: float = 900,
                 clock: Callable[[], float] = time.time,
                 on_state_change: Callable[[str, str], None] | None = None,
                 lock_wait_s: float = 5.0):
        self._silent, self._interactive = silent, interactive
        self._margin, self._backoff, self._backoff_max = margin_s, backoff_s, backoff_max_s
        self._clock, self._on_state_change, self._lock_wait = clock, on_state_change, lock_wait_s
        self._lock = threading.Lock()  # um único lock de renovação: nunca abre dois navegadores
        self._tok: Token | None = None
        self.state = STARTING
        self._fails = 0
        self._next_attempt = 0.0
        self._last_ok: float | None = None
        self._last_error = ""
        self._stop = threading.Event()

    # ---- API usada pelo gateway ----
    def get_token(self) -> str:
        tok = self._tok
        if tok and tok.expires_at - self._clock() > 30:
            return tok.value
        return self._renew(self._clock_min_valid)

    def invalidate(self) -> None:
        """Chamado quando o Databricks devolveu 401: descarta o token e força renovação na próxima leitura."""
        self._tok = None

    def start(self) -> None:
        """Primeira subida: tenta autenticar (pode abrir navegador para validação humana) e inicia a renovação
        proativa em background."""
        try:
            self._renew(self._clock_min_valid, ignore_backoff=True)
        except AuthUnavailable:
            pass  # processo segue de pé em DEGRADED; o loop tenta de novo
        threading.Thread(target=self._loop, name="token-renewer", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        tok = self._tok
        return {"state": self.state, "expires_at": tok.expires_at if tok else None, "last_ok": self._last_ok,
                "fails": self._fails, "next_attempt": self._next_attempt or None, "last_error": self._last_error}

    # ---- internos ----
    def _clock_min_valid(self, tok: Token | None) -> bool:
        return bool(tok and tok.expires_at - self._clock() > 30)

    def _needs_margin(self, tok: Token | None) -> bool:
        return not (tok and tok.expires_at - self._clock() > self._margin)

    def _loop(self) -> None:
        while not self._stop.wait(30):
            try:
                if self._needs_margin(self._tok):
                    self._renew(lambda t: not self._needs_margin(t))
            except AuthUnavailable:
                pass
            except Exception:  # noqa: BLE001 - o renovador nunca pode morrer
                log.exception("erro inesperado no renovador de token")

    def _renew(self, is_valid: Callable[[Token | None], bool], ignore_backoff: bool = False) -> str:
        if not self._lock.acquire(timeout=self._lock_wait):
            raise AuthUnavailable(self._lock_wait)  # outra thread está renovando (login pode demorar)
        try:
            if is_valid(self._tok):
                return self._tok.value  # type: ignore[union-attr]
            now = self._clock()
            if not ignore_backoff and now < self._next_attempt:
                raise AuthUnavailable(self._next_attempt - now)
            for name, fn in (("silent", self._silent), ("interactive", self._interactive)):
                if fn is None:
                    continue
                try:
                    tok = fn()
                except Exception as e:  # noqa: BLE001
                    self._last_error = f"{name}: {type(e).__name__}"
                    log.warning("renovação %s falhou: %s", name, type(e).__name__)
                    continue
                self._tok, self._fails, self._next_attempt = tok, 0, 0.0
                self._last_ok, self._last_error = self._clock(), ""
                self._set_state(OK, f"token renovado via {name}")
                return tok.value
            self._fails += 1
            wait = min(self._backoff * 2 ** (self._fails - 1), self._backoff_max)
            self._next_attempt = self._clock() + wait
            self._set_state(DEGRADED, f"renovação falhou ({self._last_error}); nova tentativa em {int(wait)}s")
            raise AuthUnavailable(wait)
        finally:
            self._lock.release()

    def _set_state(self, new: str, msg: str) -> None:
        changed = new != self.state
        self.state = new
        if changed and self._on_state_change:
            try:
                self._on_state_change(new, msg)
            except Exception:  # noqa: BLE001
                log.exception("callback de estado falhou")
