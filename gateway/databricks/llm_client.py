"""Cliente do agente/LLM via pacote `openai`.

O Databricks Model Serving expõe API compatível com OpenAI em `https://<workspace>/serving-endpoints`
(`model` = nome do endpoint, `api_key` = token OAuth). O mesmo código serve DeepSeek (teste) e Databricks.

Dois formatos de endpoint (config AGENT_API): `chat` (chat.completions) e `responses` (API Responses, usada por
agentes criados com ResponsesAgent). É o único lugar a mudar se o agente real usar outro formato.
"""
import json
import time
from dataclasses import dataclass

from openai import OpenAI

from gateway.config import Settings, get_settings


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    custom_outputs: dict | None = None  # metadados do agente (ex.: skills/assuntos usados); nunca dados de cliente


def _custom_outputs(r) -> dict | None:
    c = getattr(r, "custom_outputs", None)
    if c is None:
        c = (getattr(r, "model_extra", None) or {}).get("custom_outputs")
    return c if isinstance(c, dict) else None


def build_client(settings: Settings | None = None, api_key: str | None = None) -> OpenAI:
    """`api_key` injeta o token vigente do TokenManager a cada chamada."""
    s = settings or get_settings()
    return OpenAI(
        base_url=s.llm_base_url,
        api_key=api_key or s.resolve_llm_key(),
        timeout=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )


def pack_payload(payload: dict, settings: Settings) -> tuple[str, dict | None]:
    """(conteúdo da mensagem, custom_inputs) conforme AGENT_PAYLOAD_MODE."""
    if settings.agent_payload_mode == "custom-inputs":
        return payload.get("input") or payload.get("prompt") or "", payload
    return json.dumps(payload, ensure_ascii=False), None


def ask(content: str, system: str | None = None, settings: Settings | None = None,
        api_key: str | None = None, model: str | None = None, max_tokens: int | None = None,
        custom_inputs: dict | None = None) -> LLMResult:
    """`model` = nome do serving endpoint (por app). Vazio = LLM_MODEL."""
    s = settings or get_settings()
    client = build_client(s, api_key)
    extra = {"extra_body": {"custom_inputs": custom_inputs}} if custom_inputs else {}
    t0 = time.perf_counter()
    if s.agent_api == "responses":
        r = client.responses.create(
            model=model or s.llm_model, input=[{"role": "user", "content": content}],
            instructions=system, max_output_tokens=max_tokens or s.llm_max_tokens, **extra)
        text, used_model = r.output_text or "", r.model
        pt, ct = getattr(r.usage, "input_tokens", None), getattr(r.usage, "output_tokens", None)
    else:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": content}]
        r = client.chat.completions.create(
            model=model or s.llm_model, messages=messages, temperature=s.llm_temperature,
            max_tokens=max_tokens or s.llm_max_tokens, **extra)
        text, used_model = r.choices[0].message.content or "", r.model
        pt, ct = getattr(r.usage, "prompt_tokens", None), getattr(r.usage, "completion_tokens", None)
    return LLMResult(text, used_model, int((time.perf_counter() - t0) * 1000), pt, ct, _custom_outputs(r))
