"""Diagnóstico de pré-voo: roda ANTES de subir o gateway (e sempre que algo estranho acontecer) e diz, em PASS/WARN/FAIL,
o que está errado na configuração, no ambiente ou na conectividade. É a primeira coisa que o Claude da empresa deve rodar.

Uso:  python -m scripts.doctor [--offline] [--port 8080]
  --offline  não testa rede/Databricks (só configuração local)
Saída 1 se houver qualquer FAIL.
"""
import argparse
import importlib
import os
import shutil
import socket
import ssl
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from gateway.config import Settings, get_settings

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Check:
    level: str  # PASS | WARN | FAIL
    name: str
    detail: str = ""


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            pass
        return True
    except OSError:
        return False


def _browser_found(channel: str) -> str | None:
    names = {"msedge": ["msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
             "chrome": ["chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"]}.get(channel, [])
    for n in names:
        if shutil.which(n) or Path(n).exists():
            return n
    return None


def run_checks(s: Settings, offline: bool = False, port: int = 8080) -> list[Check]:
    out: list[Check] = []
    add = lambda lvl, name, detail="": out.append(Check(lvl, name, detail))  # noqa: E731
    browser_mode = s.auth_mode == "browser"

    add("PASS" if sys.version_info >= (3, 10) else "FAIL", "Python >= 3.10", sys.version.split()[0])
    mods = ["fastapi", "uvicorn", "openai", "yaml", "pydantic_settings"] + (["databricks.sdk", "playwright"] if browser_mode else [])
    for m in mods:
        try:
            importlib.import_module(m)
            add("PASS", f"pacote {m}")
        except ImportError:
            add("FAIL", f"pacote {m}", "faltando: pip install -r requirements.txt -r requirements-auth.txt")

    # segredos
    if not s.token_secret:
        add("FAIL", "TOKEN_SECRET", "não configurado: /v1/run devolve 503")
    elif s.token_secret.startswith("dev-") or len(s.token_secret) < 32:
        add("WARN", "TOKEN_SECRET", "fraco ou de desenvolvimento: use 32+ caracteres aleatórios na empresa")
    else:
        add("PASS", "TOKEN_SECRET")
    if not s.admin_key:
        add("WARN", "ADMIN_KEY", "vazia: /admin e /painel ficam desligados")
    elif s.admin_key.startswith("dev-") or len(s.admin_key) < 12:
        add("WARN", "ADMIN_KEY", "fraca ou de desenvolvimento")
    else:
        add("PASS", "ADMIN_KEY")

    from gateway.security import secret_problems
    for problem in secret_problems(s):
        add("FAIL" if browser_mode else "WARN", "segredos", problem + (" (em produção o gateway não sobe)" if browser_mode else ""))
    if not s.panel_key:
        add("WARN", "PANEL_KEY", "vazia: a tela fixa do painel teria que usar a ADMIN_KEY na URL (?k=); prefira uma PANEL_KEY só-leitura")
    if s.expose_docs:
        add("WARN", "EXPOSE_DOCS", "ligado: /docs e /openapi.json ficam expostos na rede; desligue em produção")

    # CORS / HTTPS
    try:
        import re
        re.compile(s.cors_origin_regex)
        add("PASS", "CORS_ORIGIN_REGEX compila")
    except Exception as e:  # noqa: BLE001
        add("FAIL", "CORS_ORIGIN_REGEX", str(e))
    cert, key = os.environ.get("GATEWAY_SSL_CERT"), os.environ.get("GATEWAY_SSL_KEY")
    if cert and key and Path(cert).exists() and Path(key).exists():
        add("PASS", "HTTPS direto (GATEWAY_SSL_CERT/KEY)")
    else:
        add("WARN", "HTTPS", "sem certificado no uvicorn: sem proxy reverso com TLS, o index.html (HTTPS) não consegue chamar o gateway")

    # apps e skills
    try:
        from gateway.routing.registry import load_registry
        reg = load_registry(s.apps_dir)
        add("PASS" if reg.apps else "FAIL", "apps/*.yaml", f"{len(reg.apps)} app(s): {', '.join(reg.apps) or '-'}")
        if "demo" in reg.apps and browser_mode:
            add("WARN", "apps/demo.yaml", "app de teste local presente: apague na empresa")
    except Exception as e:  # noqa: BLE001
        add("FAIL", "apps/*.yaml", f"{type(e).__name__}: {e}")
    skills_dir = ROOT / "databricks_agent" / "skills"
    if skills_dir.exists():
        try:
            from databricks_agent.skills import SkillStore
            from scripts.lint_skills import lint
            errs, warns = lint(SkillStore.from_dir(skills_dir))
            add("FAIL" if errs else ("WARN" if warns else "PASS"), "skills (lint)", f"{len(errs)} erro(s), {len(warns)} aviso(s)")
        except Exception as e:  # noqa: BLE001
            add("FAIL", "skills", f"{type(e).__name__}: {e}")

    # disco
    for label, p in [("logs/", ROOT / "logs"), ("histórico do painel", Path(s.history_db).parent if s.history_db != ":memory:" else ROOT)]:
        add("PASS" if _writable(p) else "FAIL", f"gravável: {label}", str(p))
    if browser_mode:
        add("PASS" if _writable(Path(s.browser_profile_dir)) else "FAIL", "gravável: perfil do navegador", s.browser_profile_dir)
        add("PASS" if (s.databricks_host or "").startswith("https://") else "FAIL", "DATABRICKS_HOST", s.databricks_host or "não configurado")
        found = _browser_found(s.browser_channel)
        add("PASS" if found else "FAIL", f"navegador {s.browser_channel}", found or "não encontrado")

    # porta
    # No Windows, bind em 0.0.0.0 "passa" mesmo com outro processo escutando em 127.0.0.1: o teste confiável é conectar.
    with socket.socket() as probe:
        probe.settimeout(1)
        in_use = probe.connect_ex(("127.0.0.1", port)) == 0
    if in_use:
        add("WARN", f"porta {port}", "em uso (o gateway já está rodando? ou outro programa)")
    else:
        add("PASS", f"porta {port} livre")

    if offline:
        return out

    # rede
    host = urlparse(s.databricks_host or s.llm_base_url).hostname or ""
    try:
        socket.getaddrinfo(host, 443)
        add("PASS", f"DNS {host}")
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as raw, ctx.wrap_socket(raw, server_hostname=host):
            add("PASS", f"TLS {host}:443")
    except ssl.SSLCertVerificationError as e:
        add("FAIL", f"TLS {host}", f"certificado não confiável ({e.verify_message}); proxy corporativo? defina SSL_CERT_FILE/REQUESTS_CA_BUNDLE")
    except OSError as e:
        add("FAIL", f"rede {host}", f"{type(e).__name__}: {e} (firewall/proxy? HTTPS_PROXY/NO_PROXY)")
    else:
        try:  # relógio: tokens e assinatura HMAC dependem de hora correta
            req = urllib.request.Request(f"https://{host}/", method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as r:
                skew = abs(parsedate_to_datetime(r.headers["Date"]).timestamp() - time.time())
        except urllib.error.HTTPError as e:
            skew = abs(parsedate_to_datetime(e.headers["Date"]).timestamp() - time.time()) if e.headers.get("Date") else 0
        except Exception:  # noqa: BLE001
            skew = 0
        add("PASS" if skew < 60 else "WARN", "relógio da máquina", f"diferença de {skew:.0f}s para o servidor")

    # autenticação / LLM
    if browser_mode:
        try:
            from gateway.auth.databricks_oauth import make_silent
            make_silent(s)()
            add("PASS", "token Databricks (renovação silenciosa)")
        except Exception as e:  # noqa: BLE001
            add("WARN", "token Databricks", f"{type(e).__name__}: precisa do login humano: python -m scripts.login")
    else:
        try:
            s.resolve_llm_key()
        except Exception:  # noqa: BLE001
            add("FAIL", "chave do LLM", "defina LLM_API_KEY ou LLM_API_KEY_FILE")
        else:
            try:
                from gateway.databricks.llm_client import ask
                r = ask("Responda apenas: ok", max_tokens=5)
                add("PASS", "chamada de teste ao LLM/agente", f"{r.latency_ms} ms (modelo {r.model})")
            except Exception as e:  # noqa: BLE001 - só o tipo do erro
                add("FAIL", "chamada de teste ao LLM/agente", f"{type(e).__name__} (401/403 = token ou permissão; 404 = nome do endpoint)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    checks = run_checks(get_settings(), a.offline, a.port)
    icon = {"PASS": "[ OK ]", "WARN": "[AVISO]", "FAIL": "[FALHA]"}
    for c in checks:
        print(f"{icon[c.level]} {c.name}" + (f" - {c.detail}" if c.detail else ""))
    n = {k: sum(1 for c in checks if c.level == k) for k in ("PASS", "WARN", "FAIL")}
    print(f"\n{n['PASS']} ok, {n['WARN']} aviso(s), {n['FAIL']} falha(s)")
    sys.exit(1 if n["FAIL"] else 0)
