import time

import openai
import pytest
from fastapi.testclient import TestClient

from gateway import main
from gateway.auth.token_manager import AuthUnavailable
from gateway.databricks.llm_client import LLMResult
from gateway.resilience.breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from gateway.security import sign

client = TestClient(main.app)
OK_RESULT = LLMResult("resposta", "m", 5, 1, 1)


def tok(app="demo"):
    return sign({"app": app, "sub": "op@x.com", "exp": time.time() + 300}, "segredo-de-teste")


def post(body=None):
    return client.post("/v1/run", json=body or {"input": "oi"}, headers={"Authorization": f"Bearer {tok()}"})


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    main.limiter._hits.clear()
    main.breaker.success()
    monkeypatch.setattr(main, "tokens", None)


def test_breaker_abre_meia_abre_e_fecha():
    t = {"v": 0.0}
    b = CircuitBreaker(failures=2, reset_s=10, clock=lambda: t["v"])
    b.failure(); b.failure()
    assert b.state == OPEN and not b.allow()
    t["v"] = 11
    assert b.allow() and b.state == HALF_OPEN and not b.allow()  # só uma sonda
    b.success()
    assert b.state == CLOSED and b.allow()


def test_breaker_sonda_falha_reabre():
    t = {"v": 0.0}
    b = CircuitBreaker(failures=1, reset_s=10, clock=lambda: t["v"])
    b.failure()
    t["v"] = 11
    assert b.allow()
    b.failure()
    assert b.state == OPEN


def test_run_com_campos_envia_payload_completo(monkeypatch):
    seen = {}

    def fake(content, **kw):
        seen["content"] = content
        return OK_RESULT

    monkeypatch.setattr(main, "ask", fake)
    r = post({"input": "pergunta", "prompt": "seja breve", "fields": {"cpf": "123.456.789-09", "assunto": "consorcio",
                                                                      "urgente": True}})
    assert r.status_code == 200
    import json
    p = json.loads(seen["content"])
    assert p["app"] == "demo" and p["user"] == "op@x.com" and p["prompt"] == "seja breve"
    assert p["input"] == "pergunta" and p["fields"] == {"cpf": "12345678909", "assunto": "consorcio", "urgente": True}


@pytest.mark.parametrize("fields", [{"nao_existe": "x"}, {"assunto": "invalido"}, {"cpf": "1"}, {"urgente": "sim"}])
def test_campos_invalidos_422(fields, monkeypatch):
    monkeypatch.setattr(main, "ask", lambda *a, **k: OK_RESULT)
    assert post({"input": "x", "fields": fields}).status_code == 422


def test_erro_422_nao_ecoa_valor(monkeypatch):
    monkeypatch.setattr(main, "ask", lambda *a, **k: OK_RESULT)
    r = post({"input": "x", "fields": {"cpf": "SEGREDO123"}})
    assert r.status_code == 422 and "SEGREDO123" not in r.text


def test_falhas_de_conexao_abrem_circuito(monkeypatch):
    def boom(*a, **k):
        raise openai.APIConnectionError(request=None)

    monkeypatch.setattr(main, "ask", boom)
    codes = [post().status_code for _ in range(7)]
    assert codes[:5] == [502] * 5 and codes[5:] == [503, 503]
    assert client.get("/ready").status_code == 503


def test_auth_indisponivel_503_com_retry_after(monkeypatch):
    class T:
        state = "DEGRADED"

        def get_token(self):
            raise AuthUnavailable(42)

    monkeypatch.setattr(main, "tokens", T())
    r = post()
    assert r.status_code == 503 and r.headers["retry-after"] == "42"
    assert client.get("/ready").status_code == 503


def test_401_do_databricks_renova_e_tenta_uma_vez(monkeypatch):
    calls = []

    class T:
        state = "OK"
        n = 0

        def get_token(self):
            T.n += 1
            return f"tok{T.n}"

        def invalidate(self):
            calls.append("inv")

    def ask_(content, api_key=None, **k):
        calls.append(api_key)
        if api_key == "tok1":
            raise openai.AuthenticationError("x", response=type("R", (), {"status_code": 401, "headers": {},
                                                                          "request": None})(), body=None)
        return OK_RESULT

    monkeypatch.setattr(main, "tokens", T())
    monkeypatch.setattr(main, "ask", ask_)
    assert post().status_code == 200
    assert calls == ["tok1", "inv", "tok2"]


def test_ready_ok_e_status():
    assert client.get("/ready").json() == {"ready": True}
    assert client.get("/admin/status").status_code == 401
    s = client.get("/admin/status", headers={"X-Admin-Key": "admin-teste"}).json()
    assert s["auth"]["state"] == "STATIC" and s["breaker"] == "CLOSED"
    assert any(a["app_id"] == "demo" for a in s["apps"])


def test_painel_html():
    r = client.get("/admin")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
