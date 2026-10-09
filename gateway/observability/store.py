"""Histórico de uso em SQLite (arquivo local) para o painel executivo. Sobrevive a reinícios (o EventLog técnico não).

Guarda SÓ metadados de cada atendimento: hora, aplicação, status, duração, assuntos (títulos das skills) e um hash
com sal do operador (para contar pessoas distintas sem guardar e-mail). Nunca guarda texto digitado, CPF nem resposta.
"""
import hashlib
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


class HistoryStore:
    def __init__(self, path: str = ":memory:", salt: str = "", retention_days: int = 400):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._salt, self._retention = salt, retention_days
        with self._lock:
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(
                "CREATE TABLE IF NOT EXISTS calls(ts REAL, day TEXT, hour INTEGER, app TEXT, usr TEXT, status INTEGER,"
                " ms INTEGER, topics TEXT, clarify INTEGER);"
                "CREATE INDEX IF NOT EXISTS ix_calls_ts ON calls(ts);"
                "CREATE TABLE IF NOT EXISTS incidents(ts REAL, kind TEXT, detail TEXT);"
                "CREATE INDEX IF NOT EXISTS ix_inc_ts ON incidents(ts);")
        self.prune()

    def _user(self, user: str) -> str:
        return hashlib.sha256(f"{self._salt}|{user}".encode()).hexdigest()[:16]

    def record_call(self, app: str, user: str, status: int, ms: int, topics=(), clarify: bool = False,
                    ts: float | None = None) -> None:
        t = time.time() if ts is None else ts
        d = datetime.fromtimestamp(t)
        with self._lock:
            self._db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?)",
                             (t, d.strftime("%Y-%m-%d"), d.hour, app, self._user(user), status, ms,
                              "|".join(str(x) for x in topics), int(clarify)))
            self._db.commit()

    def record_incident(self, kind: str, detail: str, ts: float | None = None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO incidents VALUES (?,?,?)", (time.time() if ts is None else ts, kind, detail))
            self._db.commit()

    def prune(self) -> None:
        cutoff = time.time() - self._retention * 86400
        with self._lock:
            self._db.execute("DELETE FROM calls WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM incidents WHERE ts < ?", (cutoff,))
            self._db.commit()

    def executive(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        nd = datetime.fromtimestamp(now)
        start = datetime(nd.year, nd.month, nd.day).timestamp()
        with self._lock:
            today = self._db.execute(
                "SELECT status, ms, usr, topics, clarify, hour, app, ts FROM calls WHERE ts >= ? AND ts <= ?",
                (start, now)).fetchall()
            yest = self._db.execute("SELECT COUNT(*) FROM calls WHERE ts >= ? AND ts < ?",
                                    (start - 86400, now - 86400)).fetchone()[0]
            days = dict(self._db.execute("SELECT day, COUNT(*) FROM calls WHERE ts >= ? GROUP BY day",
                                         (start - 6 * 86400,)).fetchall())
            inc = self._db.execute("SELECT ts, kind, detail FROM incidents WHERE ts >= ? ORDER BY ts DESC",
                                   (start,)).fetchall()
        # só o que é responsabilidade do serviço conta para sucesso (4xx = erro de uso, fica de fora)
        counted = [r for r in today if r[0] == 200 or r[0] >= 500]
        ok = [r for r in counted if r[0] == 200]
        hours = [0] * 24
        for r in counted:
            hours[r[5]] += 1
        apps: dict[str, int] = {}
        topics: dict[str, int] = {}
        for r in ok:
            apps[r[6]] = apps.get(r[6], 0) + 1
            for t in filter(None, r[3].split("|")):
                topics[t] = topics.get(t, 0) + 1
        total = len(counted)
        ms_sorted = sorted(r[1] for r in ok)
        p90 = ms_sorted[min(len(ms_sorted) - 1, int(len(ms_sorted) * 0.9))] if ms_sorted else None
        last_hour = sum(1 for r in counted if r[7] >= now - 3600)
        day_list = []
        for i in range(6, -1, -1):
            d = datetime.fromtimestamp(start - i * 86400 + 43200)
            day_list.append({"d": d.strftime("%Y-%m-%d"), "w": DIAS[d.weekday()], "n": days.get(d.strftime("%Y-%m-%d"), 0)})
        top = lambda m, n: [{"name": k, "n": v} for k, v in sorted(m.items(), key=lambda x: -x[1])[:n]]  # noqa: E731
        return {
            "generated_at": now,
            "today": {
                "total": total, "ok": len(ok),
                "success_rate": round(100 * len(ok) / total, 1) if total else None,
                "avg_s": round(sum(r[1] for r in ok) / len(ok) / 1000, 1) if ok else None,
                "p90_s": round(p90 / 1000, 1) if p90 is not None else None,  # 9 em cada 10 respondidos em até isto
                "people": len({r[2] for r in counted}),
                "clarifications": sum(r[4] for r in ok),
            },
            "yesterday_same_time": yest,
            "delta_pct": round(100 * (total - yest) / yest) if yest else None,
            "last_hour": last_hour,
            "hours": hours, "current_hour": nd.hour,
            "peak_hour": hours.index(max(hours)) if total else None,
            "days": day_list,
            "apps": [{"name": a["name"], "n": a["n"], "share": round(100 * a["n"] / len(ok))} for a in top(apps, 12)] if ok else [],
            "topics": top(topics, 12),
            "incidents_today": len(inc),
            "last_incident": {"ts": inc[0][0], "kind": inc[0][1], "detail": inc[0][2]} if inc else None,
        }
