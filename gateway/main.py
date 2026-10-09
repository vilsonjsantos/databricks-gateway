import hashlib
import hmac
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import openai
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from gateway.alerts import Alerter
from gateway.auth.token_manager import AuthUnavailable, TokenManager
from gateway.config import get_settings
from gateway.databricks.llm_client import ask, pack_payload
from gateway.observability.events import EventLog
from gateway.observability.store import HistoryStore
from gateway.resilience.breaker import CircuitBreaker
from gateway.resilience.concurrency import Busy, ConcurrencyLimiter
from gateway.resilience.idempotency import IdempotencyCache
from gateway.resilience.ratelimit import RateLimiter
from gateway.routing.registry import get_registry
from gateway.resilience.throttle import FailureThrottle
from gateway.security import TokenError, secret_problems, verify
from gateway.watch import start_error_watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gateway")

settings = get_settings()
events = EventLog(settings.events_buffer)
limiter = RateLimiter()
concurrency = ConcurrencyLimiter(settings.max_concurrency)
idem = IdempotencyCache(settings.request_cache_ttl_s, wait_s=settings.llm_timeout_s + 10)
alerter = Alerter(settings.alert_webhook_url, settings.alert_min_interval_s)


def _safe(fn, *a, **k) -> None:
    """Histórico/painel nunca podem derrubar um atendimento: erro de disco, banco bloqueado etc. só viram log."""
    try:
        fn(*a, **k)
    except Exception:  # noqa: BLE001
        log.exception("falha ao gravar histórico (atendimento segue normal)")


history = HistoryStore(settings.history_db, salt=settings.token_secret or "")


def _breaker_changed(st: str) -> None:
    alerter.send("breaker", f"[gateway] circuito do Databricks: {st}")
    if st == "OPEN":
        _safe(history.record_incident, "conexao", "Instabilidade na conexão com o Databricks")


breaker = CircuitBreaker(settings.breaker_failures, settings.breaker_reset_s, on_change=_breaker_changed)
tokens: TokenManager | None = None  # só em AUTH_MODE=browser
DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
PAINEL = Path(__file__).resolve().parent.parent / "dashboard" / "painel.html"


def _build_tokens() -> TokenManager | None:
    if settings.auth_mode != "browser":
        return None
    if not settings.databricks_host:
        raise RuntimeError("AUTH_MODE=browser exige DATABRICKS_HOST")
    from gateway.auth.databricks_oauth import make_interactive, make_silent

    def on_state(state: str, msg: str) -> None:
        events.add(kind="auth", status=0, reason=f"{state}: {msg}")
        alerter.send("auth", f"[gateway] autenticação Databricks {state}: {msg}")
        if state == "DEGRADED":
            _safe(history.record_incident, "autenticacao", "Reautenticação no Databricks necessária")

    return TokenManager(make_silent(settings), make_interactive(settings), settings.token_refresh_margin_s,
                        settings.auth_backoff_s, settings.auth_backoff_max_s, on_state_change=on_state)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global tokens
    problems = secret_problems(settings)
    if problems:
        if settings.auth_mode == "browser":  # produção: não sobe com segredo fraco/de exemplo
            raise RuntimeError("configuração insegura para produção: " + "; ".join(problems))
        log.warning("segredos fracos (aceitável só em desenvolvimento): %s", "; ".join(problems))
    tokens = tokens or _build_tokens()
    if tokens:  # start() pode ficar minutos no login humano da 1ª subida: não bloqueia o servidor
        threading.Thread(target=tokens.start, name="auth-start", daemon=True).start()
    stop_watch = start_error_watcher(events, alerter, 300, settings.error_alert_min_calls, settings.error_alert_rate)
    yield
    stop_watch.set()
    if tokens:
        tokens.stop()


_docs = {} if settings.expose_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Databricks Gateway", lifespan=lifespan, **_docs)


class _TooBig(Exception):
    pass


class BodyLimit:
    """Limita o corpo da requisição MESMO sem Content-Length (envio em partes/chunked), contando os bytes lidos.
    Adicionado ANTES do CORS: a resposta 413 ainda passa pelo CORS e o navegador mostra o erro certo."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total, rejected = 0, False

        async def reject():
            nonlocal rejected
            rejected = True
            body = b'{"detail":"corpo da requisi\\u00e7\\u00e3o grande demais"}'
            await send({"type": "http.response.start", "status": 413, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        async def recv():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request" and not rejected:
                total += len(msg.get("body", b""))
                if total > self.max:
                    # O FastAPI converte erros na leitura do corpo em 400; por isso respondemos 413 AQUI e damos
                    # "desconexão" ao app, que aborta a leitura. Qualquer resposta que ele tente enviar depois é descartada.
                    await reject()
                    return {"type": "http.disconnect"}
            return msg

        async def snd(msg):
            if not rejected:
                await send(msg)

        declared = next((v for k, v in scope["headers"] if k == b"content-length"), b"")
        if declared.isdigit() and int(declared) > self.max:
            return await reject()
        await self.app(scope, recv, snd)


app.add_middleware(BodyLimit, max_bytes=settings.max_body_bytes)

_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    h, path = resp.headers, request.url.path
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"  # a chave ?k= da tela fixa não vaza por Referer
    if path.startswith(("/admin", "/painel", "/v1/")):
        h["Cache-Control"] = "no-store"
    if path in ("/admin", "/painel"):  # páginas HTML: sem iframe, sem recursos externos
        h["Content-Security-Policy"] = _CSP
        h["X-Frame-Options"] = "DENY"
    return resp


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
    expose_headers=["Retry-After", "X-Request-Id"],
    # Chrome/Edge exigem Access-Control-Allow-Private-Network quando página pública chama host privado.
    allow_private_network=True,
    max_age=600,
)


@app.get("/health")
def health() -> dict:
    """Liveness: o processo está de pé."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: pode atender? (auth ok, circuito fechado, configuração mínima)."""
    reasons = []
    if not settings.token_secret:
        reasons.append("TOKEN_SECRET não configurado")
    if not get_registry().apps:
        reasons.append("nenhum app em apps/")
    if tokens and tokens.state != "OK":
        reasons.append(f"autenticação {tokens.state}")
    if breaker.state == "OPEN":
        reasons.append("circuito do Databricks aberto")
    if reasons:
        return JSONResponse({"ready": False, "reasons": reasons}, status_code=503)
    return {"ready": True}


_ID = r"^[A-Za-z0-9_-]{8,64}$"


class RunBody(BaseModel):
    input: str = Field(default="", max_length=20000)  # texto principal do operador (CPF, protocolo, pergunta...)
    prompt: str | None = Field(default=None, max_length=20000)  # instrução/prompt escolhido ou digitado na interface
    fields: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=50)  # demais campos
    request_id: str | None = Field(default=None, pattern=_ID)  # opcional: evita duplicar atendimento em retry/duplo clique
    conversation_id: str | None = Field(default=None, pattern=_ID)  # opcional: continua a conversa (se o app permitir)


def _fail(app_id: str, user: str, started: float, rid: str, status: int, reason: str,
          headers: dict | None = None) -> HTTPException:
    ms = int((time.perf_counter() - started) * 1000)
    events.add(kind="run", app=app_id, user=user, status=status, reason=reason, ms=ms, rid=rid)
    if status >= 500 and app_id != "-":  # falha do serviço entra no histórico executivo (erro de uso, não)
        _safe(history.record_call, app_id, user, status, ms)
    return HTTPException(status, reason, headers={"X-Request-Id": rid, **(headers or {})})


def _verify(token: str) -> dict:
    """Aceita o segredo atual e, durante a rotação, o anterior."""
    first: TokenError | None = None
    for secret in (settings.token_secret, settings.token_secret_previous):
        if not secret:
            continue
        try:
            return verify(token, secret)
        except TokenError as e:
            first = first or e
    raise first or TokenError("sem segredo configurado")


def _conversation_key(app_id: str, user: str, conversation_id: str) -> str:
    """Chave da conversa amarrada a app+usuário: outro operador não abre a conversa mesmo sabendo o id."""
    msg = f"conv|{app_id}|{user}|{conversation_id}".encode()
    return hmac.new((settings.token_secret or "").encode(), msg, hashlib.sha256).hexdigest()[:40]


@app.post("/v1/run")
def run(body: RunBody, authorization: str | None = Header(default=None)) -> dict:
    """Recebe texto + prompt + campos de um app, manda ao agente no Databricks e devolve a resposta."""
    started, rid = time.perf_counter(), uuid.uuid4().hex[:12]
    if not settings.token_secret:
        raise HTTPException(503, "TOKEN_SECRET não configurado", headers={"X-Request-Id": rid})
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _fail("-", "-", started, rid, 401, "token ausente")
    try:
        claims = _verify(authorization[7:].strip())
    except TokenError as e:
        raise _fail("-", "-", started, rid, 401, f"token: {e}") from e
    app_id, user = claims["app"], str(claims.get("sub", "-"))

    cfg = get_registry().get(app_id)
    if cfg is None or not cfg.enabled:
        raise _fail(app_id, user, started, rid, 403, "app desconhecido ou desativado")
    try:
        text, prompt, fields = cfg.clean_request(body.input, body.prompt, body.fields)
    except ValueError as e:
        raise _fail(app_id, user, started, rid, 422, str(e)) from e

    key = (app_id, user, body.request_id) if body.request_id else None
    if key:
        state, cached = idem.claim(key)
        if state == "hit":  # mesmo request_id já atendido (ou em andamento): devolve a mesma resposta
            events.add(kind="run", app=app_id, user=user, status=200, reason="repetido (idempotente)", rid=rid, ms=0)
            return {**cached, "request_id": rid, "repeated": True}  # type: ignore[arg-type]
    try:
        result = _serve(cfg, app_id, user, text, prompt, fields, body, started, rid)
    except BaseException:
        if key:
            idem.fail(key)
        raise
    if key:
        idem.done(key, result)
    return result


def _upstream_key(app_id: str, user: str, started: float, rid: str) -> str | None:
    if tokens is None:
        return None
    try:
        return tokens.get_token()
    except AuthUnavailable as e:
        raise _fail(app_id, user, started, rid, 503, "autenticação com o Databricks indisponível",
                    {"Retry-After": str(e.retry_after)}) from e


def _serve(cfg, app_id: str, user: str, text: str, prompt: str, fields: dict, body: RunBody,
           started: float, rid: str) -> dict:
    if not limiter.allow(app_id, cfg.rate_limit_per_min):
        raise _fail(app_id, user, started, rid, 429, "limite de requisições do app", {"Retry-After": "10"})
    try:
        with concurrency.slot(app_id, cfg.max_concurrent):
            return _call_agent(cfg, app_id, user, text, prompt, fields, body, started, rid)
    except Busy as e:
        raise _fail(app_id, user, started, rid, 429, "muitos atendimentos ao mesmo tempo; tente em instantes",
                    {"Retry-After": "3"}) from e


def _call_agent(cfg, app_id: str, user: str, text: str, prompt: str, fields: dict, body: RunBody,
                started: float, rid: str) -> dict:
    if not breaker.allow():
        raise _fail(app_id, user, started, rid, 503, "Databricks indisponível (circuito aberto)",
                    {"Retry-After": str(breaker.retry_after())})

    # Tudo que o agente recebe. A identidade do app e do usuário vem do token, nunca do corpo.
    payload = {"app": app_id, "user": user, "prompt": prompt, "input": text, "fields": fields}
    conversation_id = None
    if cfg.conversation.enabled:
        conversation_id = body.conversation_id or uuid.uuid4().hex
        payload["conversation_key"] = _conversation_key(app_id, user, conversation_id)
        payload["conversation_max_turns"] = cfg.conversation.max_turns
    content, custom = pack_payload(payload, settings)

    def call(key: str | None):
        return ask(content, system=cfg.system_prompt, model=cfg.endpoint, max_tokens=cfg.max_tokens,
                   api_key=key, custom_inputs=custom)

    try:
        try:
            r = call(_upstream_key(app_id, user, started, rid))
        except openai.AuthenticationError:
            if tokens is None:
                raise
            tokens.invalidate()  # 401: o token foi revogado/expirou antes do previsto -> renova e tenta 1 vez
            r = call(_upstream_key(app_id, user, started, rid))
    except HTTPException:
        raise
    except (openai.APIConnectionError, openai.InternalServerError) as e:  # inclui timeout e 5xx
        breaker.failure()
        raise _fail(app_id, user, started, rid, 502, f"agente indisponível: {type(e).__name__}") from e
    except Exception as e:  # noqa: BLE001 - só o tipo do erro; nunca texto da requisição
        raise _fail(app_id, user, started, rid, 502, f"falha no agente: {type(e).__name__}") from e
    breaker.success()

    ms = int((time.perf_counter() - started) * 1000)
    co = r.custom_outputs or {}
    topics = [str(t) for t in (co.get("topics") or co.get("skills") or [])][:5]
    clarify = bool(co.get("clarify"))
    events.add(kind="run", app=app_id, user=user, status=200, ms=ms, llm_ms=r.latency_ms, rid=rid,
               in_chars=len(text) + len(prompt), out_chars=len(r.text), fields=sorted(fields), topics=topics)
    _safe(history.record_call, app_id, user, 200, ms, topics, clarify)
    out = {"text": r.text, "app": app_id, "ms": ms, "request_id": rid}
    if conversation_id:
        out["conversation_id"] = conversation_id
    if clarify:
        out["clarify"] = True  # o agente fez uma pergunta de volta: a tela deve mostrar e manter o conversation_id
    return out


throttle = FailureThrottle(settings.admin_max_failures, 300.0)


def _eq(a: str | None, b: str | None) -> bool:
    """Comparação em tempo constante (não vaza, pelo tempo de resposta, quantos caracteres da chave estão certos)."""
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())  # type: ignore[union-attr]


def _admin(key: str | None, request: Request, panel: bool = False) -> None:
    """ADMIN_KEY abre tudo; PANEL_KEY (se `panel=True`) abre só os dados agregados do painel executivo.
    Falhas repetidas de um mesmo cliente bloqueiam por alguns minutos (429), mesmo com a chave certa depois."""
    keys = [settings.admin_key] + ([settings.panel_key] if panel else [])
    if not any(keys):
        raise HTTPException(503, "painel desligado (chave não configurada)")
    who = request.client.host if request.client else "?"
    if throttle.blocked(who):
        raise HTTPException(429, "muitas tentativas inválidas; aguarde alguns minutos", headers={"Retry-After": "300"})
    if not any(_eq(key, k) for k in keys):
        throttle.record(who)
        raise HTTPException(401, "chave inválida")


@app.get("/admin/events")
def admin_events(request: Request, limit: int = 100, since_id: int = 0,
                 x_admin_key: str | None = Header(default=None)) -> dict:
    _admin(x_admin_key, request)
    return {"events": events.recent(min(limit, 500), since_id)}


@app.get("/admin/status")
def admin_status(request: Request, x_admin_key: str | None = Header(default=None)) -> dict:
    _admin(x_admin_key, request)
    reg = get_registry()
    return {
        "auth": tokens.status() if tokens else {"state": "STATIC"},
        "breaker": breaker.state,
        "summary": events.summary(),
        "apps": [{"app_id": a.app_id, "endpoint": a.endpoint, "enabled": a.enabled} for a in reg.apps.values()],
        "config": {"auth_mode": settings.auth_mode, "agent_api": settings.agent_api,
                   "payload_mode": settings.agent_payload_mode, "host": settings.llm_base_url},
    }


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(DASHBOARD)


# ---------------- painel executivo (tela fixa na máquina, linguagem não técnica) ----------------
def _service_status() -> dict:
    auth_bad = bool(tokens and tokens.state == "DEGRADED")
    net_bad = breaker.state == "OPEN"
    if auth_bad and net_bad:
        return {"level": "fora", "text": "Fora do ar. A equipe técnica foi avisada."}
    if auth_bad or net_bad:
        return {"level": "atencao", "text": "Instabilidade: reconectando ao Databricks. A equipe técnica foi avisada."}
    if tokens and tokens.state == "STARTING":
        return {"level": "atencao", "text": "Iniciando o serviço..."}
    return {"level": "ok", "text": "Operando normalmente"}


@app.get("/painel", include_in_schema=False)
def painel_page():
    return FileResponse(PAINEL)  # só a casca estática; os números vêm de /painel/dados (com chave)


@app.get("/painel/dados", include_in_schema=False)
def painel_data(request: Request, k: str | None = None, x_admin_key: str | None = Header(default=None)) -> dict:
    local = settings.panel_localhost_no_key and request.client and request.client.host in ("127.0.0.1", "::1")
    if not local:
        _admin(x_admin_key or k, request, panel=True)  # aceita a PANEL_KEY (só leitura agregada) além da ADMIN_KEY
    data = history.executive()
    names = {a.app_id: (a.name or a.app_id) for a in get_registry().apps.values()}  # nome amigável do YAML
    data["apps"] = [{**a, "name": names.get(a["name"], a["name"])} for a in data["apps"]]
    return {"title": settings.panel_title, "demo": settings.panel_demo, "status": _service_status(),
            "targets": {"success": settings.panel_target_success, "seconds": settings.panel_target_seconds}, **data}


@app.get("/painel/tema.css", include_in_schema=False)
def painel_tema():
    return FileResponse(PAINEL.with_name("tema.css"), media_type="text/css")


@app.get("/painel/logo", include_in_schema=False)
def painel_logo():
    """Logo opcional da empresa: coloque dashboard/logo.svg (ou .png). Sem arquivo, o painel simplesmente não mostra."""
    for name in ("logo.svg", "logo.png"):
        p = PAINEL.with_name(name)
        if p.exists():
            return FileResponse(p)
    raise HTTPException(404, "sem logo")
