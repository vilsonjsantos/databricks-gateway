"""Teste único do LLM configurado.  Uso (na raiz do projeto):  python -m scripts.test_llm"""
from gateway.config import get_settings
from gateway.databricks.llm_client import ask

if __name__ == "__main__":
    s = get_settings()
    print(f"endpoint={s.llm_base_url} model={s.llm_model}")
    r = ask("Responda apenas com a palavra: ok")
    print(f"resposta={r.text!r} latência={r.latency_ms}ms tokens={r.prompt_tokens}+{r.completion_tokens}")
