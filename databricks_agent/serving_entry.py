"""Adaptador do agente para o Databricks Model Serving (MLflow ResponsesAgent).

ESBOÇO NÃO VALIDADO: escrito sem acesso a um workspace e sem o mlflow instalado. Conferir nomes e assinaturas na versão
de `mlflow` / `databricks-agents` da empresa (docs: "Author AI agents in code" / ResponsesAgent). O núcleo
(`agent.py`) é o que foi testado. Se a empresa preferir `ChatAgent`, só este arquivo muda.

Configuração por variáveis de ambiente do endpoint (definidas no deploy, ver log_and_deploy.py):
  AGENT_ROUTER_MODEL   endpoint de LLM barato/rápido para escolher skills (ex.: um Llama 8B/Haiku-like do workspace)
  AGENT_ANSWER_MODEL   endpoint de LLM da resposta final
  SQL_WAREHOUSE_ID     SQL Warehouse onde as queries das skills rodam
  SKILLS_DIR           opcional; padrão = pasta skills/ ao lado deste arquivo (vai junto via code_paths)
"""
import json
import os
from pathlib import Path

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from databricks_agent.agent import AgentCore, OpenAILLM
from databricks_agent.skills import SkillStore
from databricks_agent.sql_runner import DatabricksSqlRunner


def _build_core() -> AgentCore:
    from databricks.sdk import WorkspaceClient

    skills = SkillStore.from_dir(os.environ.get("SKILLS_DIR") or Path(__file__).resolve().parent / "skills")
    client = WorkspaceClient().serving_endpoints.get_open_ai_client()
    llm = OpenAILLM(client, os.environ["AGENT_ROUTER_MODEL"], os.environ["AGENT_ANSWER_MODEL"])
    return AgentCore(llm, skills, DatabricksSqlRunner(os.environ["SQL_WAREHOUSE_ID"]),
                     conversations=_conversation_store())


def _conversation_store():
    """CONVERSATION_STORE: delta (produção; exige CONVERSATION_TABLE) | memory (dev; some ao reiniciar e não é
    compartilhada entre réplicas) | off (padrão: sem histórico)."""
    mode = os.environ.get("CONVERSATION_STORE", "off")
    if mode == "delta":
        from databricks_agent.conversation import DeltaConversationStore

        return DeltaConversationStore(os.environ["CONVERSATION_TABLE"], os.environ["SQL_WAREHOUSE_ID"])
    if mode == "memory":
        from databricks_agent.conversation import InMemoryConversationStore

        return InMemoryConversationStore()
    return None


def _payload_from(request: ResponsesAgentRequest) -> dict:
    """O gateway manda o payload como JSON na última mensagem do usuário (AGENT_PAYLOAD_MODE=json-content) ou em
    custom_inputs (custom-inputs). Aceita os dois; texto puro vira {"input": texto}."""
    payload: dict = {}
    for item in reversed(request.input):
        if getattr(item, "role", None) == "user":
            content = item.content if isinstance(item.content, str) else "".join(
                getattr(p, "text", "") for p in item.content)
            try:
                loaded = json.loads(content)
                payload = loaded if isinstance(loaded, dict) else {"input": content}
            except ValueError:
                payload = {"input": content}
            break
    if request.custom_inputs:
        payload = {**payload, **request.custom_inputs}
    payload.setdefault("fields", {})
    return payload


class SkillsAgent(ResponsesAgent):
    def __init__(self) -> None:
        self._core: AgentCore | None = None

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        self._core = self._core or _build_core()  # cria na primeira chamada (credenciais do endpoint)
        result = self._core.handle(_payload_from(request))
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=result.text, id="msg_1")],
            # só metadados de diagnóstico; nunca dados de cliente
            custom_outputs={"skills": result.skills, "topics": result.topics, "route": result.route_method,
                            "clarify": result.clarify, "ms": result.ms},
        )


mlflow.models.set_model(SkillsAgent())
