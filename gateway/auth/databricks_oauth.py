"""Implementações reais das camadas de login (OAuth U2M do Databricks via databricks-sdk).

NÃO TESTADO contra um workspace real (casa não alcança o Databricks da empresa). Verificado no código do SDK
instalado (databricks-sdk 0.149): `auth_type="external-browser"` carrega o cache de token em disco, tenta o refresh e,
se falhar, abre o navegador com `webbrowser.open_new(url)` e espera o callback em http://localhost:8020 (SEM timeout).

Estratégia:
  * silent      -> mesmo fluxo do SDK, mas com `webbrowser.open_new` trocado por uma função que levanta erro:
                   se o refresh funciona, devolve o token; se exigir navegador, falha sem abrir nada.
  * interactive -> `webbrowser.open_new` trocado por um Playwright com perfil persistente (sessão SSO salva),
                   rodando em thread com prazo; se estourar, destrava o servidor do callback para liberar a porta 8020.
O refresh token fica no cache do SDK (arquivo no perfil do usuário do Windows): proteger com ACL do usuário do serviço.
"""
import base64
import contextlib
import json
import logging
import threading
import time
import urllib.request
import webbrowser

from gateway.auth.token_manager import Token
from gateway.config import Settings

log = logging.getLogger("gateway.auth")
CALLBACK = "http://localhost:8020"  # fixo no SDK (redirect_url do external-browser)


class BrowserNeeded(Exception):
    pass


def jwt_exp(token: str) -> float | None:
    """Expiração (epoch) lida do JWT; None se o token não for JWT."""
    try:
        part = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"])
    except Exception:  # noqa: BLE001
        return None


@contextlib.contextmanager
def _patched_browser(opener):
    saved = webbrowser.open_new
    webbrowser.open_new = opener
    try:
        yield
    finally:
        webbrowser.open_new = saved


def _token_from_sdk(host: str) -> Token:
    from databricks.sdk.core import Config

    cfg = Config(host=host, auth_type="external-browser")
    value = cfg.authenticate()["Authorization"].split(" ", 1)[1]
    return Token(value, jwt_exp(value) or time.time() + 1800)  # sem exp legível: assume 30 min (conservador)


def make_silent(settings: Settings):
    def _no_browser(_url, *a, **k):
        raise BrowserNeeded("login exige navegador")

    def silent() -> Token:
        with _patched_browser(_no_browser):
            return _token_from_sdk(settings.databricks_host)  # type: ignore[arg-type]

    return silent


def _drive_playwright(url: str, s: Settings, done: threading.Event) -> None:
    """Abre a URL de login num navegador com perfil persistente e espera o redirect para o callback local."""
    try:
        from playwright.sync_api import sync_playwright

        time.sleep(1.0)  # o SDK abre o navegador ANTES de subir o servidor do callback
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                s.browser_profile_dir, headless=s.browser_headless,
                channel=None if s.browser_channel == "chromium" else s.browser_channel)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url)
                page.wait_for_url(lambda u: u.startswith(CALLBACK), timeout=s.browser_login_timeout_s * 1000)
                time.sleep(1.0)  # deixa o SDK responder ao callback
            finally:
                ctx.close()
    except Exception as e:  # noqa: BLE001 - MFA/timeout/navegador ausente: o interactive vai estourar o prazo
        log.warning("automação do navegador falhou: %s", type(e).__name__)
    finally:
        done.set()


def make_interactive(settings: Settings):
    def interactive() -> Token:
        result: dict = {}
        browser_done = threading.Event()

        def opener(url, *a, **k):
            threading.Thread(target=_drive_playwright, args=(url, settings, browser_done), daemon=True).start()
            return True

        def work():
            try:
                with _patched_browser(opener):
                    result["tok"] = _token_from_sdk(settings.databricks_host)  # type: ignore[arg-type]
            except Exception as e:  # noqa: BLE001
                result["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(settings.browser_login_timeout_s + 15)
        if t.is_alive():
            # handle_request() do SDK não tem timeout: uma requisição qualquer o destrava e libera a porta 8020.
            with contextlib.suppress(Exception):
                urllib.request.urlopen(CALLBACK + "/?error=timeout", timeout=3)
            t.join(5)
            raise TimeoutError("login no navegador não concluiu (MFA? sessão SSO expirada?)")
        if "err" in result:
            raise result["err"]
        return result["tok"]

    return interactive
