import json
import threading
import time

import openai
import pytest
from fastapi.testclient import TestClient

from gateway import main
from gateway.databricks.llm_client import LLMResult
from gateway.resilience.concurrency import ConcurrencyLimiter
from gateway.routing.registry import AppConfig, ConversationCfg, Registry
from gateway.security import sign
from gateway.watch import check_error_rate

client = TestClient(main.app)
OK = LLMResult("resposta", "m", 5, 1, 1)
# chave FALSA montada em tempo de execução: o código-fonte não deve conter nada com formato de segredo (o varredor do
# pacote reprovaria o próprio teste)
FAKE_PAT = "da" + "pi" + "0123456789abcdef" * 2


def tok(app="demo", sub="op@x.com", secret="segredo-de-teste", ttl=300):
    return sign({"app": app, "sub": sub, "exp": time.time() + ttl}, secret)


def post(body=None, token=None, headers=None):
    h = {"Authorization": f"Bearer {token or tok()}", **(headers or {})}
    return client.post("/v1/run", json=body or {"input": "oi"}, headers=h)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    main.limiter._hits.clear()
    main.breaker.success()
    monkeypatch.setattr(main, "tokens", None)
    monkeypatch.setattr(main, "concurrency", ConcurrencyLimiter(32))
    monkeypatch.setattr(main.idem, "_d", {})


# ---------- identificação do atendimento ----------
def test_request_id_vem_em_sucesso_e_erro(monkeypatch):
    monkeypatch.setattr(main, "ask", lambda *a, **k: OK)
    r = post()
    assert r.status_code == 200 and len(r.json()["request_id"]) == 12
    e = client.post("/v1/run", json={"input": "x"})
    assert e.status_code == 401 and len(e.headers["x-request-id"]) == 12


# ---------- limites de entrada ----------
def test_corpo_grande_demais_413():
    r = client.post("/v1/run", content=b"x" * 70000, headers={"Authorization": f"Bearer {tok()}",
                                                              "Content-Type": "application/json"})
    assert r.status_code == 413


def test_ids_com_formato_invalido_422(monkeypatch):
    monkeypatch.setattr(main, "ask", lambda *a, **k: OK)
    assert post({"input": "x", "request_id": "curto"}).status_code == 422
    assert post({"input": "x", "conversation_id": "tem espaço e símbolos!!"}).status_code == 422


# ---------- idempotência ----------
def test_mesmo_request_id_roda_o_agente_uma_vez(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "ask", lambda *a, **k: calls.append(1) or OK)
    a = post({"input": "oi", "request_id": "req-12345678"}).json()
    b = post({"input": "oi", "request_id": "req-12345678"}).json()
    assert len(calls) == 1 and b["repeated"] is True and b["text"] == a["text"]


def test_request_id_de_outro_usuario_nao_reaproveita(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "ask", lambda *a, **k: calls.append(1) or OK)
    post({"input": "oi", "request_id": "req-12345678"}, token=tok(sub="ana@x.com"))
    post({"input": "oi", "request_id": "req-12345678"}, token=tok(sub="bia@x.com"))
    assert len(calls) == 2


def test_duplicatas_simultaneas_esperam_a_primeira(monkeypatch):
    calls = []

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.4)
        return OK

    monkeypatch.setattr(main, "ask", slow)
    out = []
    ts = [threading.Thread(target=lambda: out.append(post({"input": "oi", "request_id": "req-simult01"}).json()))
          for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and len(out) == 3 and sum(1 for o in out if o.get("repeated")) == 2


def test_falha_da_primeira_nao_envenena_o_request_id(monkeypatch):
    seq = iter([RuntimeError("x"), OK])

    def flaky(*a, **k):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(main, "ask", flaky)
    assert post({"input": "oi", "request_id": "req-retry001"}).status_code == 502
    assert post({"input": "oi", "request_id": "req-retry001"}).status_code == 200  # nova tentativa roda de verdade


# ---------- concorrência ----------
def test_saturacao_recusa_com_429_e_retry_after(monkeypatch):
    monkeypatch.setattr(main, "concurrency", ConcurrencyLimiter(1))
    gate, started = threading.Event(), threading.Event()

    def blocked(*a, **k):
        started.set()
        gate.wait(5)
        return OK

    monkeypatch.setattr(main, "ask", blocked)
    first = threading.Thread(target=lambda: post())
    first.start()
    assert started.wait(3)
    r = post()
    gate.set()
    first.join()
    assert r.status_code == 429 and r.headers["retry-after"] == "3"
    assert post().status_code == 200  # liberou


def test_limite_por_app_nao_afeta_outro_app():
    lim = ConcurrencyLimiter(10)
    with lim.slot("a", 1):
        with pytest.raises(Exception):
            with lim.slot("a", 1):
                pass
        with lim.slot("b", 1):  # outro app segue livre
            pass


# ---------- rotação de segredo ----------
def test_rotacao_de_segredo_aceita_o_anterior(monkeypatch):
    monkeypatch.setattr(main, "ask", lambda *a, **k: OK)
    old = tok(secret="segredo-antigo")
    assert post(token=old).status_code == 401  # sem o anterior configurado
    monkeypatch.setattr(main.settings, "token_secret_previous", "segredo-antigo")
    assert post(token=old).status_code == 200
    assert post(token=tok(secret="outro-qualquer")).status_code == 401


# ---------- histórico nunca derruba o atendimento ----------
def test_falha_ao_gravar_historico_nao_afeta_a_resposta(monkeypatch):
    monkeypatch.setattr(main, "ask", lambda *a, **k: OK)

    def boom(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(main.history, "record_call", boom)
    assert post().status_code == 200


# ---------- alerta de taxa de erro ----------
class FakeAlerter:
    def __init__(self):
        self.sent = []

    def send(self, key, text):
        self.sent.append((key, text))


def test_alerta_quando_muitas_falhas_do_servico():
    from gateway.observability.events import EventLog

    ev, al = EventLog(), FakeAlerter()
    for _ in range(6):
        ev.add(kind="run", status=502, app="a")
    for _ in range(4):
        ev.add(kind="run", status=200, app="a")
    ev.add(kind="run", status=422, app="a")  # erro de uso não conta
    assert check_error_rate(ev, al, 300, 10, 0.3) is True and al.sent[0][0] == "error-rate"


def test_sem_alerta_com_pouco_volume_ou_so_erro_de_uso():
    from gateway.observability.events import EventLog

    ev, al = EventLog(), FakeAlerter()
    for _ in range(30):
        ev.add(kind="run", status=422, app="a")
    assert check_error_rate(ev, al, 300, 10, 0.3) is False
    ev2 = EventLog()
    for _ in range(3):
        ev2.add(kind="run", status=502, app="a")
    assert check_error_rate(ev2, al, 300, 10, 0.3) is False


# ---------- doctor (diagnóstico de pré-voo) ----------
def _by_name(checks, name):
    return next(c for c in checks if c.name.startswith(name))


def test_doctor_offline_sem_falhas_e_avisa_segredo_fraco():
    from scripts.doctor import run_checks

    checks = run_checks(main.settings, offline=True, port=59123)
    assert not [c for c in checks if c.level == "FAIL"]
    assert _by_name(checks, "TOKEN_SECRET").level == "WARN"  # "segredo-de-teste" tem < 32 caracteres


def test_doctor_detecta_porta_ocupada_e_segredo_ausente(monkeypatch):
    import socket

    from scripts.doctor import run_checks

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        monkeypatch.setattr(main.settings, "token_secret", None)
        checks = run_checks(main.settings, offline=True, port=port)
    assert _by_name(checks, f"porta {port}").level == "WARN"
    assert _by_name(checks, "TOKEN_SECRET").level == "FAIL"


# ---------- pacote para a empresa ----------
def test_pacote_exclui_segredos_e_gera_zip(tmp_path):
    import zipfile

    from scripts.make_package import build

    proj = tmp_path / "proj"
    for rel, content in {"gateway/main.py": "x=1", "apps/demo.yaml": "app_id: demo", "apps/real.yaml": "app_id: r",
                         ".env": "TOKEN_SECRET=abc", "databricks-token.txt": FAKE_PAT,
                         "data/history.sqlite": "bin", ".venv/lib/x.py": "y", "logs/a.log": "z",
                         "browser-profile/cookies": "c", ".env.example": "TOKEN_SECRET=<aleatório>"}.items():
        f = proj / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    z, files, skipped, problems = build(proj, tmp_path / "dist")
    assert z and not problems
    names = set(zipfile.ZipFile(z).namelist())
    assert "databricks-project/gateway/main.py" in names and "databricks-project/apps/real.yaml" in names
    assert "databricks-project/.env.example" in names
    for proibido in (".env", "databricks-token.txt", "apps/demo.yaml", "data/history.sqlite", ".venv/lib/x.py",
                     "browser-profile/cookies", "logs/a.log"):
        assert f"databricks-project/{proibido}" not in names


def test_pacote_recusa_quando_acha_chave_no_codigo(tmp_path):
    from scripts.make_package import build

    proj = tmp_path / "proj"
    (proj / "gateway").mkdir(parents=True)
    (proj / "gateway" / "config.py").write_text(f'KEY = "{FAKE_PAT}"\n')
    z, _, _, problems = build(proj, tmp_path / "dist")
    assert z is None and "gateway/config.py" in problems[0]
    assert not (tmp_path / "dist").exists()  # nada é gerado


def test_pacote_real_do_projeto_esta_limpo():
    from scripts.make_package import ROOT, collect, scan

    files, skipped = collect(ROOT)
    assert scan(files, ROOT) == []  # nenhum segredo no código que seria levado
    assert "apps/demo.yaml" in skipped  # app de teste local nunca vai no pacote (independe de existir token/.env na pasta)


# ---------- conversa no gateway ----------
@pytest.fixture
def conv_app(monkeypatch):
    cfg = AppConfig(app_id="chat", endpoint="agente", input_required=False,
                    conversation=ConversationCfg(enabled=True, max_turns=4))
    monkeypatch.setattr(main, "get_registry", lambda: Registry({"chat": cfg}))
    seen = []

    def fake(content, **k):
        seen.append(json.loads(content))
        return LLMResult("ok", "m", 1, 1, 1, {"clarify": len(seen) == 1})

    monkeypatch.setattr(main, "ask", fake)
    return seen


def test_gateway_cria_conversa_e_amarra_ao_usuario(conv_app):
    r1 = post({"prompt": "oi"}, token=tok(app="chat", sub="ana@x.com")).json()
    cid = r1["conversation_id"]
    assert r1["clarify"] is True and len(cid) == 32
    post({"prompt": "continua", "conversation_id": cid}, token=tok(app="chat", sub="ana@x.com"))
    post({"prompt": "continua", "conversation_id": cid}, token=tok(app="chat", sub="bia@x.com"))
    k1, k2, k_bia = (conv_app[0]["conversation_key"], conv_app[1]["conversation_key"], conv_app[2]["conversation_key"])
    assert conv_app[0]["conversation_max_turns"] == 4
    assert k1 == k2  # a 1ª chamada gerou o id; a 2ª, com o id devolvido, continua a MESMA conversa
    assert k1 == main._conversation_key("chat", "ana@x.com", cid)
    assert k_bia != conv_app[1]["conversation_key"]  # mesmo id, outro operador -> outra conversa


def test_app_sem_conversa_ignora_o_id(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "ask", lambda c, **k: seen.append(json.loads(c)) or OK)
    r = post({"input": "oi", "conversation_id": "abcdefgh1234"}).json()
    assert "conversation_id" not in r and "conversation_key" not in seen[0]
