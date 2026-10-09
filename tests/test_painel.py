import sqlite3
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gateway import main
from gateway.databricks.llm_client import LLMResult
from gateway.observability.store import HistoryStore
from gateway.security import sign

NOW = datetime(2026, 10, 8, 15, 30).timestamp()


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m).timestamp()


@pytest.fixture
def st():
    s = HistoryStore(":memory:", salt="sal")
    # hoje (08/10): 3 ok, 1 falha de serviço; ontem (07/10) até 15:30: 2 chamadas
    s.record_call("app-a", "ana@x.com", 200, 2000, ["Lance com FGTS"], ts=at(8, 9, 10))
    s.record_call("app-a", "ana@x.com", 200, 4000, ["Lance com FGTS", "Boletos"], ts=at(8, 9, 40))
    s.record_call("app-b", "bia@x.com", 200, 3000, [], clarify=True, ts=at(8, 14, 5))
    s.record_call("app-a", "caio@x.com", 502, 500, ts=at(8, 14, 20))
    s.record_call("app-a", "ana@x.com", 200, 1000, ts=at(7, 10))
    s.record_call("app-a", "ana@x.com", 200, 1000, ts=at(7, 11))
    s.record_call("app-a", "ana@x.com", 200, 1000, ts=at(7, 18))  # depois das 15:30: fora da comparação
    s.record_incident("conexao", "Instabilidade", ts=at(8, 14, 20))
    return s


def test_numeros_do_dia(st):
    e = st.executive(NOW)
    t = e["today"]
    assert t["total"] == 4 and t["ok"] == 3 and t["success_rate"] == 75.0
    assert t["avg_s"] == 3.0 and t["people"] == 3 and t["clarifications"] == 1
    assert e["yesterday_same_time"] == 2 and e["delta_pct"] == 100  # 4 hoje vs 2 ontem até o mesmo horário
    assert e["hours"][9] == 2 and e["hours"][14] == 2 and sum(e["hours"]) == 4
    assert e["peak_hour"] in (9, 14) and e["current_hour"] == 15


def test_p90_e_ultima_hora(st):
    e = st.executive(NOW)
    assert e["today"]["p90_s"] == 4.0  # 3 respostas ok: 2s, 3s, 4s -> 90º percentil = 4s
    assert e["last_hour"] == 0  # NOW=15:30; os atendimentos de hoje foram até 14:20
    s2 = HistoryStore(":memory:")
    s2.record_call("a", "u", 200, 1000, ts=NOW - 600)
    s2.record_call("a", "u", 200, 1000, ts=NOW - 4000)
    assert s2.executive(NOW)["last_hour"] == 1


def test_apps_assuntos_e_dias(st):
    e = st.executive(NOW)
    assert e["apps"][0] == {"name": "app-a", "n": 2, "share": 67}
    assert e["topics"][0] == {"name": "Lance com FGTS", "n": 2} and {"name": "Boletos", "n": 1} in e["topics"]
    assert len(e["days"]) == 7 and e["days"][-1]["n"] == 4 and e["days"][-2]["n"] == 3
    assert e["incidents_today"] == 1 and e["last_incident"]["kind"] == "conexao"


def test_erros_de_uso_nao_entram_na_taxa_de_sucesso():
    s = HistoryStore(":memory:")
    s.record_call("a", "u", 200, 1000, ts=at(8, 9))
    s.record_call("a", "u", 422, 5, ts=at(8, 9, 5))  # validação do operador: não é falha do serviço
    s.record_call("a", "u", 429, 5, ts=at(8, 9, 6))
    assert s.executive(NOW)["today"]["success_rate"] == 100.0


def test_dia_vazio_nao_quebra():
    e = HistoryStore(":memory:").executive(NOW)
    assert e["today"]["total"] == 0 and e["today"]["success_rate"] is None and e["delta_pct"] is None
    assert e["peak_hour"] is None and e["apps"] == [] and e["topics"] == []


def test_banco_nao_guarda_email_nem_conteudo(tmp_path):
    p = tmp_path / "h.sqlite"
    s = HistoryStore(str(p), salt="sal")
    s.record_call("app", "ana@empresa.com", 200, 10, ["Boletos"], ts=at(8, 9))
    raw = sqlite3.connect(p).execute("SELECT * FROM calls").fetchall()
    assert "ana@empresa.com" not in str(raw) and "sal" not in str(raw)
    assert HistoryStore(str(p), salt="outro-sal")._user("ana@empresa.com") != s._user("ana@empresa.com")


def test_retencao_apaga_antigos():
    s = HistoryStore(":memory:", retention_days=30)
    s.record_call("a", "u", 200, 1, ts=time.time() - 40 * 86400)
    s.record_call("a", "u", 200, 1, ts=time.time())
    s.prune()
    assert s._db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1


# ---------- rotas ----------
client = TestClient(main.app)


def test_painel_html_e_dados_exigem_chave():
    assert client.get("/painel").status_code == 200
    assert client.get("/painel/dados").status_code == 401
    r = client.get("/painel/dados", headers={"X-Admin-Key": "admin-teste"})
    assert r.status_code == 200 and r.json()["status"] == {"level": "ok", "text": "Operando normalmente"}
    assert client.get("/painel/dados?k=admin-teste").status_code == 200  # chave na URL (quiosque)


def test_painel_serve_tema_e_logo_opcional(tmp_path, monkeypatch):
    r = client.get("/painel/tema.css")
    assert r.status_code == 200 and "text/css" in r.headers["content-type"] and "--band" in r.text
    assert client.get("/painel/logo").status_code == 404  # sem dashboard/logo.* o painel só não mostra
    fake = tmp_path / "painel.html"
    fake.write_text("x")
    (tmp_path / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    monkeypatch.setattr(main, "PAINEL", fake)
    assert client.get("/painel/logo").status_code == 200


def test_painel_traz_metas_e_nome_amigavel_da_aplicacao(monkeypatch):
    from gateway.routing.registry import AppConfig, Registry

    monkeypatch.setattr(main, "get_registry", lambda: Registry({"app-x": AppConfig(app_id="app-x", name="Assistente dos Operadores", endpoint="e")}))
    monkeypatch.setattr(main, "history", HistoryStore(":memory:"))
    main.history.record_call("app-x", "u", 200, 1000, ["Boletos"])
    d = client.get("/painel/dados?k=admin-teste").json()
    assert d["targets"] == {"success": 95.0, "seconds": 8.0}
    assert d["apps"][0]["name"] == "Assistente dos Operadores"  # nome do YAML, não o id


def test_localhost_sem_chave_so_com_opt_in(monkeypatch):
    # TestClient chega como "testclient": mesmo com opt-in ligado, não é localhost -> continua exigindo chave
    monkeypatch.setattr(main.settings, "panel_localhost_no_key", True)
    assert client.get("/painel/dados").status_code == 401


def test_status_em_linguagem_simples(monkeypatch):
    class T:
        state = "DEGRADED"

    monkeypatch.setattr(main, "tokens", T())
    assert main._service_status()["level"] == "atencao"
    monkeypatch.setattr(main.breaker, "state", "OPEN")
    assert main._service_status() == {"level": "fora", "text": "Fora do ar. A equipe técnica foi avisada."}


def test_atendimento_alimenta_o_painel(monkeypatch):
    monkeypatch.setattr(main, "ask", lambda *a, **k: LLMResult("ok", "m", 5, 1, 1, {"topics": ["Boletos"], "clarify": False}))
    main.limiter._hits.clear()
    tok = sign({"app": "demo", "sub": "painel@x.com", "exp": time.time() + 300}, "segredo-de-teste")
    before = client.get("/painel/dados?k=admin-teste").json()["today"]["total"]
    assert client.post("/v1/run", json={"input": "oi"}, headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    d = client.get("/painel/dados?k=admin-teste").json()
    assert d["today"]["total"] == before + 1 and {"name": "Boletos", "n": 1} in d["topics"]
