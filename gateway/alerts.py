"""Alertas por webhook (Slack/Teams/qualquer um que aceite POST {"text": ...}). Sem URL, só registra em log.
Limitado por chave para não inundar o canal. Nunca inclui dado de cliente."""
import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("gateway.alerts")


class Alerter:
    def __init__(self, url: str | None, min_interval_s: float = 900.0):
        self._url, self._min = url, min_interval_s
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def send(self, key: str, text: str) -> None:
        log.warning("ALERTA [%s] %s", key, text)
        if not self._url:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._last.get(key, -1e9) < self._min:
                return
            self._last[key] = now
        threading.Thread(target=self._post, args=(text,), daemon=True).start()

    def _post(self, text: str) -> None:
        try:
            req = urllib.request.Request(self._url, data=json.dumps({"text": text}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # noqa: BLE001
            log.warning("falha ao enviar alerta: %s", type(e).__name__)
