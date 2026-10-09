"""Buffer circular de eventos para o painel. Nunca guarda o conteúdo da entrada nem da resposta,
só metadados (app, usuário, status, duração, tamanho, NOMES dos campos). Dados de cliente não entram aqui."""
import re
import threading
import time
from collections import deque
from itertools import count

_CPF_ANY = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")


def mask(text: str) -> str:
    return _CPF_ANY.sub("***.***.***-**", text)


def _pct(sorted_vals: list[int], p: float) -> int:
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))] if sorted_vals else 0


class EventLog:
    def __init__(self, size: int = 500) -> None:
        self._buf: deque[dict] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._seq = count(1)
        self.started = time.time()

    def add(self, **fields) -> dict:
        ev = {"id": next(self._seq), "ts": time.time(), **fields}
        for k, v in ev.items():
            if isinstance(v, str):
                ev[k] = mask(v)
        with self._lock:
            self._buf.append(ev)
        return ev

    def recent(self, limit: int = 100, since_id: int = 0) -> list[dict]:
        with self._lock:
            items = [e for e in self._buf if e["id"] > since_id]
        return items[-limit:]

    def summary(self, window_s: int = 900) -> dict:
        """Totais, erros e latência (p50/p95) por app nos últimos `window_s` segundos."""
        cutoff = time.time() - window_s
        with self._lock:
            runs = [e for e in self._buf if e.get("kind") == "run" and e["ts"] >= cutoff]
        per_app: dict[str, dict] = {}
        for e in runs:
            a = per_app.setdefault(e.get("app", "-"), {"total": 0, "errors": 0, "ms": []})
            a["total"] += 1
            if e.get("status") != 200:
                a["errors"] += 1
            elif "ms" in e:
                a["ms"].append(e["ms"])
        out = {}
        for app, a in per_app.items():
            ms = sorted(a["ms"])
            out[app] = {"total": a["total"], "errors": a["errors"], "p50_ms": _pct(ms, 0.5), "p95_ms": _pct(ms, 0.95)}
        return {"window_s": window_s, "total": len(runs),
                "errors": sum(1 for e in runs if e.get("status") != 200),
                "failures": sum(1 for e in runs if e.get("status", 0) >= 500),  # falha do serviço
                "client_errors": sum(1 for e in runs if 400 <= e.get("status", 0) < 500), "per_app": out,
                "uptime_s": int(time.time() - self.started)}
