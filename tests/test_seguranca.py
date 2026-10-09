import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from gateway import main
from gateway.resilience.throttle import FailureThrottle
from gateway.security import secret_problems, sign
from scripts.check_leaks import scan as leak_scan

client = TestClient(main.app)
FAKE_PAT = "da" + "pi" + "0123456789abcdef" * 2  # montada em runtime: o código-fonte não pode ter cara de segredo


# ---------- chaves de admin / painel ----------
def test_throttle_bloqueia_e_libera_pela_janela():
    t = {"v": 0.0}
    th = FailureThrottle(max_failures=3, window_s=100, clock=lambda: t["v"])
    for _ in range(3):
        th.record("ip")
    assert th.blocked("ip") and not th.blocked("outro")
    t["v"] = 101
    assert not th.blocked("ip")


def test_tentativa_e_erro_bloqueia_ate_com_a_chave_certa():
    for _ in range(main.settings.admin_max_failures):
        assert client.get("/admin/status", headers={"X-Admin-Key": "errada"}).status_code == 401
    r = client.get("/admin/status", headers={"X-Admin-Key": "admin-teste"})
    assert r.status_code == 429 and r.headers["retry-after"] == "300"


def test_panel_key_abre_so_o_painel_executivo(monkeypatch):
    monkeypatch.setattr(main.settings, "panel_key", "chave-do-painel-123")
    assert client.get("/painel/dados?k=chave-do-painel-123").status_code == 200
    assert client.get("/admin/events", headers={"X-Admin-Key": "chave-do-painel-123"}).status_code == 401
    assert client.get("/admin/status?k=chave-do-painel-123").status_code == 401  # nem pela URL
    assert client.get("/painel/dados?k=admin-teste").status_code == 200  # a ADMIN_KEY também abre o painel


def test_painel_desligado_sem_nenhuma_chave(monkeypatch):
    monkeypatch.setattr(main.settings, "admin_key", None)
    monkeypatch.setattr(main.settings, "panel_key", None)
    assert client.get("/painel/dados?k=qualquer").status_code == 503


def test_comparacao_de_chave_em_tempo_constante_aceita_so_igual():
    assert main._eq("abc", "abc") and not main._eq("abc", "abd") and not main._eq(None, "abc") and not main._eq("", "")


# ---------- superfície exposta ----------
def test_documentacao_interativa_desligada_por_padrao():
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_cabecalhos_de_seguranca():
    r = client.get("/painel")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    d = client.get("/painel/dados?k=admin-teste")
    assert d.headers["cache-control"] == "no-store" and "content-security-policy" not in d.headers


def test_corpo_em_partes_acima_do_limite_e_recusado():
    def partes():
        for _ in range(5):
            yield b"x" * 20000  # 100 KB sem Content-Length (chunked); limite = 64 KB

    tok = sign({"app": "demo", "sub": "u", "exp": time.time() + 300}, "segredo-de-teste")
    r = client.post("/v1/run", content=partes(), headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    assert r.status_code == 413


# ---------- segredos fracos ----------
def test_segredos_fracos_ou_de_exemplo_sao_apontados():
    class S:  # noqa: D401 - objeto mínimo no lugar de Settings
        token_secret, admin_key, panel_key = "demo-exemplo-qualquer-coisa-de-teste-aaaa", "demo", None

    assert len(secret_problems(S)) == 2
    S.token_secret, S.admin_key, S.panel_key = "a" * 40, "b" * 20, "b" * 20
    assert any("igual" in p for p in secret_problems(S))
    S.panel_key = "c" * 20
    assert secret_problems(S) == []


def test_producao_recusa_subir_com_segredo_fraco(monkeypatch):
    monkeypatch.setattr(main.settings, "auth_mode", "browser")

    async def sobe():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="insegura"):
        asyncio.run(sobe())


# ---------- varredor anti-vazamento ----------
def test_varredor_acha_valor_real_sem_imprimir_e_respeita_nosec(tmp_path):
    segredo_falso = "valor-" + "bem-especifico-" + "do-teste-98765"
    (tmp_path / "a.py").write_text(f'x = "{segredo_falso}"\n')
    (tmp_path / "b.py").write_text(f'K = "{FAKE_PAT}"\n')
    (tmp_path / "c.py").write_text(f'K = "{FAKE_PAT}"  # nosec-secret exemplo\n')
    (tmp_path / "ok.md").write_text("nada aqui\n")
    fails, _ = leak_scan([tmp_path / n for n in ("a.py", "b.py", "c.py", "ok.md")], tmp_path, [segredo_falso])
    texto = " | ".join(fails)
    assert "a.py" in texto and "b.py" in texto and "c.py" not in texto and "ok.md" not in texto
    assert segredo_falso not in texto and FAKE_PAT not in texto  # o relatório nunca repete o valor


def test_varredor_barra_arquivos_proibidos_mas_libera_env_example(tmp_path):
    for n in (".env", "databricks-token.txt", "x.pem", "data/h.sqlite", ".env.example"):
        f = tmp_path / n
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("TOKEN_SECRET=<aleatorio>")
    fails, _ = leak_scan([p for p in tmp_path.rglob("*") if p.is_file()], tmp_path, [])
    nomes = " ".join(fails)
    assert ".env:" in nomes and "databricks-token.txt" in nomes and "x.pem" in nomes and "h.sqlite" in nomes
    assert ".env.example" not in nomes


def test_varredor_avisa_email_real_e_caminho_local(tmp_path):
    email = "alguem" + "@" + "dominio-real.com.br"  # montados em runtime: o código-fonte não deve disparar os próprios avisos
    caminho = "C" + ":" + "\\Users\\fulano\\x"
    (tmp_path / "n.md").write_text(f"fale com {email} e veja {caminho}\n")
    _, warns = leak_scan([tmp_path / "n.md"], tmp_path, [])
    assert any("e-mail" in w for w in warns) and any("caminho" in w for w in warns)
