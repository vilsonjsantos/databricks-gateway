"""Registro de aplicações: cada arquivo apps/<app_id>.yaml liga um app a um agente dentro do Databricks.

O gateway NÃO executa SQL nem monta prompt: o agente no Databricks faz isso. Aqui só se decide
para qual endpoint mandar e se o que o operador enviou (texto, prompt, campos) é aceitável.
Arquivos que começam com '_' são ignorados.
"""
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from gateway.config import get_settings

_CPF = re.compile(r"^\d{11}$")
_PROTOCOLO = re.compile(r"^[A-Za-z0-9\-/._]{3,40}$")
Kind = Literal["text", "cpf", "protocolo", "enum", "int", "bool", "date"]


class FieldSpec(BaseModel):
    type: Kind = "text"
    required: bool = False
    max_len: int = 500
    values: list[str] | None = None  # obrigatório quando type=enum


def _clean(kind: str, raw: object, max_len: int, values: list[str] | None = None) -> object:
    """Valida/normaliza um valor. Levanta ValueError sem ecoar o dado."""
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        raise ValueError("deve ser verdadeiro/falso")
    if kind == "int":
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError) as e:
            raise ValueError("deve ser inteiro") from e
    text = str(raw).strip()
    if len(text) > max_len:
        raise ValueError("longo demais")
    if kind == "cpf":
        digits = re.sub(r"\D", "", text)
        if not _CPF.match(digits):
            raise ValueError("CPF inválido")
        return digits
    if kind == "protocolo" and not _PROTOCOLO.match(text):
        raise ValueError("protocolo inválido")
    if kind == "enum" and text not in (values or []):
        raise ValueError("valor fora das opções")
    if kind == "date":
        try:
            date.fromisoformat(text)
        except ValueError as e:
            raise ValueError("data deve ser AAAA-MM-DD") from e
    return text


class ConversationCfg(BaseModel):
    enabled: bool = False  # liga o histórico de conversa para este app (guardado DENTRO do Databricks)
    max_turns: int = 6  # quantos turnos anteriores o agente recebe


class AppConfig(BaseModel):
    app_id: str
    max_concurrent: int = 8  # atendimentos simultâneos deste app (acima disso: 429 + Retry-After)
    conversation: ConversationCfg = ConversationCfg()
    name: str = ""
    enabled: bool = True
    endpoint: str  # nome do serving endpoint do agente no Databricks
    input_kind: Kind = "text"  # tipo do campo principal "input" (texto do operador)
    input_required: bool = True
    max_input_chars: int = 2000
    max_prompt_chars: int = 4000
    fields: dict[str, FieldSpec] = {}
    rate_limit_per_min: int = 60
    max_tokens: int | None = None
    # SÓ para teste local sem agente (ex.: DeepSeek). Em produção o prompt vive dentro do agente.
    system_prompt: str | None = None

    def clean_request(self, text: str, prompt: str | None, fields: dict) -> tuple[str, str, dict]:
        """Valida tudo que o operador mandou. Mensagens de erro nunca incluem os valores."""
        text = (text or "").strip()
        if not text and self.input_required:
            raise ValueError("texto obrigatório")
        if text:
            text = str(_clean(self.input_kind, text, self.max_input_chars))
        prompt = (prompt or "").strip()
        if len(prompt) > self.max_prompt_chars:
            raise ValueError("prompt longo demais")
        unknown = set(fields) - set(self.fields)
        if unknown:
            raise ValueError(f"campo desconhecido: {sorted(unknown)[0]}")
        clean: dict = {}
        for name, spec in self.fields.items():
            raw = fields.get(name)
            if raw is None or raw == "":
                if spec.required:
                    raise ValueError(f"campo obrigatório: {name}")
                continue
            try:
                clean[name] = _clean(spec.type, raw, spec.max_len, spec.values)
            except ValueError as e:
                raise ValueError(f"campo {name}: {e}") from e
        return text, prompt, clean


class Registry:
    def __init__(self, apps: dict[str, AppConfig]):
        self.apps = apps

    def get(self, app_id: str) -> AppConfig | None:
        return self.apps.get(app_id)


def load_registry(apps_dir: str) -> Registry:
    apps: dict[str, AppConfig] = {}
    for path in sorted(Path(apps_dir).glob("*.y*ml")):
        if path.name.startswith("_"):
            continue
        cfg = AppConfig(**yaml.safe_load(path.read_text(encoding="utf-8")))
        if cfg.app_id in apps:
            raise ValueError(f"app_id duplicado: {cfg.app_id}")
        for n, f in cfg.fields.items():
            if f.type == "enum" and not f.values:
                raise ValueError(f"{path.name}: campo {n} é enum sem 'values'")
        apps[cfg.app_id] = cfg
    return Registry(apps)


@lru_cache
def get_registry() -> Registry:
    return load_registry(get_settings().apps_dir)
