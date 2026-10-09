"""Login humano da primeira subida (ou quando o alerta pedir). Abre o navegador VISÍVEL com o perfil persistente
do gateway, você faz o SSO/MFA uma vez, e a sessão fica salva para as renovações automáticas.

Uso (na máquina da empresa, com o usuário que roda o serviço):
    python -m scripts.login            # navegador visível via Playwright (perfil persistente)
    python -m scripts.login --default  # abre o navegador padrão do Windows (sem automação)
"""
import sys

from gateway.auth.databricks_oauth import _token_from_sdk, make_interactive
from gateway.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    if not s.databricks_host:
        raise SystemExit("Defina DATABRICKS_HOST no .env")
    if "--default" in sys.argv:
        tok = _token_from_sdk(s.databricks_host)
    else:
        s.browser_headless = False
        s.browser_login_timeout_s = 300  # tempo para você completar SSO/MFA
        tok = make_interactive(s)()
    print(f"login ok; token válido até epoch {int(tok.expires_at)} (valor não exibido)")
