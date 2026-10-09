"""Configuração do gateway. Tudo vem de variáveis de ambiente / .env.

Cada campo aqui está documentado em docs/CONFIGURACAO.md. Ao adicionar um campo, atualize o doc.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM (qualquer endpoint compatível com a API da OpenAI) ---
    # Teste local: DeepSeek.  Produção: Databricks Model Serving (ver docs/CONFIGURACAO.md).
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_api_key_file: str | None = None  # caminho de um arquivo de 1 linha com a chave/token
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2
    llm_temperature: float = 0.2
    llm_max_tokens: int = 300

    # --- Como falar com o agente no Databricks ---
    # chat = /serving-endpoints chat.completions (ChatAgent / foundation model)
    # responses = API Responses (agente criado com ResponsesAgent)
    agent_api: str = "chat"
    # json-content = payload inteiro como JSON na mensagem do usuário (funciona em qualquer endpoint)
    # custom-inputs = texto na mensagem e o restante em custom_inputs (agente precisa ler custom_inputs)
    agent_payload_mode: str = "json-content"

    # --- Autenticação no Databricks ---
    auth_mode: str = "static"  # static = LLM_API_KEY/arquivo | browser = OAuth U2M (produção)
    databricks_host: str | None = None  # https://<workspace>; obrigatório em auth_mode=browser
    browser_profile_dir: str = "browser-profile"  # perfil persistente do Playwright (sessão SSO)
    browser_channel: str = "msedge"  # msedge | chrome | chromium
    browser_headless: bool = True  # False só no login humano da primeira subida
    browser_login_timeout_s: int = 120
    token_refresh_margin_s: int = 300  # renova em background quando faltar menos que isso
    auth_backoff_s: float = 30.0  # espera mínima após falha total de renovação
    auth_backoff_max_s: float = 900.0

    # --- Painel executivo / histórico ---
    history_db: str = "data/history.sqlite"  # use :memory: para não persistir
    panel_title: str = "Assistente de Atendimento"
    panel_target_success: float = 95.0  # meta de respostas com sucesso (%), mostrada no painel
    panel_target_seconds: float = 8.0  # meta de tempo médio de resposta (s), mostrada no painel
    # True = quem abre o painel na PRÓPRIA máquina (localhost) não precisa de chave. NÃO ligar atrás de proxy reverso
    # (o proxy chega como localhost e abriria o painel para a rede).
    panel_localhost_no_key: bool = False

    # --- Robustez do gateway ---
    max_body_bytes: int = 65536  # corpo maior que isso é recusado com 413
    max_concurrency: int = 32  # atendimentos simultâneos no gateway inteiro (cada app tem o seu teto no YAML)
    request_cache_ttl_s: float = 120.0  # idempotência por request_id
    error_alert_rate: float = 0.3  # alerta se >= 30% dos atendimentos (5 min) falharem...
    error_alert_min_calls: int = 10  # ...com pelo menos 10 atendimentos na janela
    token_secret_previous: str | None = Field(default=None, repr=False)  # aceita o segredo antigo durante a rotação
    panel_demo: bool = False  # mostra "DADOS DE DEMONSTRAÇÃO" no painel

    # --- Resiliência / alertas ---
    breaker_failures: int = 5
    breaker_reset_s: float = 30.0
    alert_webhook_url: str | None = None  # POST {"text": "..."}; vazio = só log/painel
    alert_min_interval_s: float = 900.0

    # --- Roteamento / segurança ---
    apps_dir: str = "apps"
    # Segredo HMAC compartilhado com o Code.gs (Script Properties). Obrigatório para /v1/run.
    token_secret: str | None = Field(default=None, repr=False)
    # Origens permitidas no CORS (separadas por vírgula) e regex da origem do Apps Script.
    cors_origins: str = ""
    cors_origin_regex: str = r"^https://[a-z0-9-]+-script\.googleusercontent\.com$"
    # Chave do painel técnico /admin/* (vê e-mails dos operadores). Vazia = desligado.
    admin_key: str | None = Field(default=None, repr=False)
    # Chave SÓ do painel executivo (dados agregados, sem e-mail nem CPF). É a que vai na URL ?k= da tela fixa; assim
    # um vazamento dela (log de proxy, histórico do navegador) não dá acesso ao /admin. Vazia = só ADMIN_KEY abre o painel.
    panel_key: str | None = Field(default=None, repr=False)
    expose_docs: bool = False  # /docs, /redoc e /openapi.json (desligados por padrão: não ajudam a quem escaneia a rede)
    admin_max_failures: int = 10  # falhas de chave por cliente em 5 min antes de bloquear (429)
    events_buffer: int = 500

    def resolve_llm_key(self) -> str:
        """Chave direta tem prioridade; senão lê do arquivo. Nunca logar o retorno."""
        if self.llm_api_key:
            return self.llm_api_key.strip()
        if self.llm_api_key_file:
            return Path(self.llm_api_key_file).read_text(encoding="utf-8").strip()
        raise RuntimeError("Defina LLM_API_KEY ou LLM_API_KEY_FILE")


@lru_cache
def get_settings() -> Settings:
    return Settings()
