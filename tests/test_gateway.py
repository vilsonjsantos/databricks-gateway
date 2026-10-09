import time

import pytest
from fastapi.testclient import TestClient

from gateway import main
from gateway.databricks.llm_client import LLMResult
from gateway.observability.events import mask
from gateway.routing.registry import AppConfig
from gateway.security import TokenError, sign, verify

SECRET = "segredo-de-teste"
client = TestClient(main.app)


def token(app="demo", sub="op@empresa.com", ttl=300, secret=SECRET):
    return sign({"app": app, "sub": sub, "exp": time.time() + ttl}, secret)


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    calls = []

    def fake_ask(prompt, system=None, model=None, max_tokens=None, **kw):
        calls.append({"prompt": prompt, "model": model})
        return LLMResult(text="resposta", model=model or "m", latency_ms=5,
                         prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(main, "ask", fake_ask)
    main.limiter._hits.clear()
    return calls


def post(tok, text="oi"):
    return client.post("/v1/run", json={"input": text}, headers={"Authorization": f"Bearer {tok}"} if tok else {})


def test_ok_roteia_para_endpoint_do_app(fake_llm):
    r = post(token())
    assert r.status_code == 200 and r.json()["text"] == "resposta"
    assert fake_llm[0]["model"] == "deepseek-chat"  # endpoint vem do apps/demo.yaml


def test_sem_token_401():
    assert post(None).status_code == 401


def test_token_assinatura_errada_401():
    assert post(token(secret="outro")).status_code == 401


def test_token_expirado_401():
    assert post(token(ttl=-1)).status_code == 401


def test_app_desconhecido_403():
    assert post(token(app="nao-existe")).status_code == 403


def test_entrada_longa_demais_422():
    assert post(token(), "x" * 501).status_code == 422  # demo: max_input_chars = 500


def test_texto_obrigatorio_quando_configurado():
    cfg = AppConfig(app_id="x", endpoint="e", input_required=True)
    with pytest.raises(ValueError):
        cfg.clean_request("  ", None, {})


def test_rate_limit_429():
    main.limiter._hits.clear()
    codes = [post(token()).status_code for _ in range(31)]  # demo = 30/min
    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_cpf_validacao_e_normalizacao():
    cfg = AppConfig(app_id="x", endpoint="e", input_kind="cpf")
    assert cfg.clean_request("123.456.789-09", None, {})[0] == "12345678909"
    with pytest.raises(ValueError):
        cfg.clean_request("123", None, {})


def test_eventos_nao_vazam_cpf():
    assert "123.456.789-09" not in mask("cliente 123.456.789-09")
    assert "12345678909" not in mask("cliente 12345678909")


def test_painel_exige_chave():
    assert client.get("/admin/events").status_code == 401
    assert client.get("/admin/events", headers={"X-Admin-Key": "admin-teste"}).status_code == 200


def test_preflight_cors_e_rede_privada():
    r = client.options("/v1/run", headers={
        "Origin": "https://abc123-script.googleusercontent.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
        "Access-Control-Request-Private-Network": "true",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://abc123-script.googleusercontent.com"
    assert r.headers["access-control-allow-private-network"] == "true"


def test_verify_token_malformado():
    with pytest.raises(TokenError):
        verify("lixo", SECRET)
