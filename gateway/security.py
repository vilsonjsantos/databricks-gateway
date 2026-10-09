"""Token assinado (HMAC-SHA256) emitido pelo Code.gs e validado pelo gateway.

Formato:  <base64url(json payload)>.<base64url(hmac_sha256(secret, parte1))>
Payload:  {"app": "<app_id>", "sub": "<email do operador>", "exp": <epoch em segundos>}

O segredo fica só no Script Properties do Apps Script e no gateway; o navegador só carrega o token (vida curta).
"""
import base64
import hashlib
import hmac
import json
import time


class TokenError(Exception):
    pass


# Prefixos de segredos de exemplo/desenvolvimento: nunca aceitáveis em produção.
WEAK_PREFIXES = ("dev-", "demo", "segredo", "test", "change", "troque", "senha", "password", "1234", "admin")


def _weak(value: str | None, min_len: int) -> bool:
    return not value or len(value) < min_len or value.lower().startswith(WEAK_PREFIXES)


def secret_problems(s) -> list[str]:
    """Problemas nos segredos configurados. Em AUTH_MODE=browser (produção) qualquer problema impede a subida."""
    out = []
    if _weak(s.token_secret, 32):
        out.append("TOKEN_SECRET ausente, curto (<32) ou de exemplo/desenvolvimento")
    if _weak(s.admin_key, 16):
        out.append("ADMIN_KEY ausente, curta (<16) ou de exemplo/desenvolvimento")
    if s.panel_key and _weak(s.panel_key, 16):
        out.append("PANEL_KEY curta (<16) ou de exemplo/desenvolvimento")
    if s.panel_key and s.admin_key and s.panel_key == s.admin_key:
        out.append("PANEL_KEY igual à ADMIN_KEY (a do painel deve ser outra, só de leitura)")
    return out


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mac(body: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def sign(payload: dict, secret: str) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_mac(body, secret)}"


def verify(token: str, secret: str, now: float | None = None) -> dict:
    try:
        body, sig = token.split(".")
    except ValueError as e:
        raise TokenError("formato inválido") from e
    # Apps Script pode devolver base64 com padding '='; a comparação ignora isso.
    if not hmac.compare_digest(sig.rstrip("="), _mac(body, secret)):
        raise TokenError("assinatura inválida")
    try:
        payload = json.loads(_unb64(body))
    except ValueError as e:
        raise TokenError("payload ilegível") from e
    if float(payload.get("exp", 0)) < (time.time() if now is None else now):
        raise TokenError("token expirado")
    if not payload.get("app"):
        raise TokenError("token sem app")
    return payload
