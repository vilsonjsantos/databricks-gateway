"""Gera um token de teste (equivale ao que o Code.gs faz).
Uso:  python -m scripts.mint_token <app_id> [email] [ttl_segundos]"""
import sys
import time

from gateway.config import get_settings
from gateway.security import sign

if __name__ == "__main__":
    app_id = sys.argv[1]
    sub = sys.argv[2] if len(sys.argv) > 2 else "teste@local"
    ttl = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    secret = get_settings().token_secret
    if not secret:
        raise SystemExit("Defina TOKEN_SECRET")
    print(sign({"app": app_id, "sub": sub, "exp": time.time() + ttl}, secret))
