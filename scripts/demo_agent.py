"""Demo local do agente com DeepSeek no lugar do LLM do Databricks e dados SINTÉTICOS no lugar do SQL.
Uso:  python -m scripts.demo_agent"""
from pathlib import Path

from openai import OpenAI

from databricks_agent.agent import AgentCore, OpenAILLM
from databricks_agent.skills import SkillStore
from databricks_agent.sql_runner import FakeSqlRunner
from gateway.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    llm = OpenAILLM(OpenAI(base_url=s.llm_base_url, api_key=s.resolve_llm_key()), "deepseek-chat", "deepseek-chat")
    store = SkillStore.from_dir(Path(__file__).resolve().parent.parent / "databricks_agent" / "skills")
    sql = FakeSqlRunner({"atendimento.tickets": [
        {"protocolo": "P-1001", "assunto": "Segunda via de boleto", "status": "Aberto", "aberto_em": "2026-10-05",
         "previsao_retorno": "2026-10-10"},
        {"protocolo": "P-0990", "assunto": "Dúvida sobre assembleia", "status": "Resolvido", "aberto_em": "2026-09-20",
         "previsao_retorno": None}]})
    payload = {"app": "assistente-operadores", "user": "operador@teste", "prompt": "Quais tickets esse cliente tem?",
               "input": "", "fields": {"cpf": "12345678909", "assunto": "consorcio", "urgente": True}}
    r = AgentCore(llm, store, sql).handle(payload)
    print(f"skills={r.skills} via={r.route_method} queries={r.queries_ran} pulou={r.queries_skipped} ms={r.ms}")
    print("---\n" + r.text)
