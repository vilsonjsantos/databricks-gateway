# Databricks notebook source
# ESBOÇO NÃO VALIDADO (sem acesso a workspace). Rodar célula a célula num notebook do Databricks, com a pasta
# `databricks_agent/` importada (Repos/Git folder). Conferir as assinaturas na versão de mlflow / databricks-agents.
#
# O que faz: testa localmente -> registra o agente no Unity Catalog (com as skills dentro do pacote, versionadas)
# -> publica num endpoint de Model Serving. Para atualizar uma skill: editar o .md, rodar de novo, nova versão.

# COMMAND ----------
# MAGIC %pip install -U mlflow databricks-agents databricks-sdk pyyaml
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
CATALOG, SCHEMA, MODEL = "<catalogo>", "<schema>", "agente_atendimento"   # AJUSTAR
ROUTER_MODEL = "<endpoint-llm-rapido>"      # AJUSTAR: endpoint de foundation model liberado no workspace
ANSWER_MODEL = "<endpoint-llm-resposta>"    # AJUSTAR
WAREHOUSE_ID = "<sql-warehouse-id>"         # AJUSTAR
UC_NAME = f"{CATALOG}.{SCHEMA}.{MODEL}"

# COMMAND ----------
# Teste local do núcleo (sem MLflow): usa os mesmos objetos que o endpoint usará
import os
os.environ.update(AGENT_ROUTER_MODEL=ROUTER_MODEL, AGENT_ANSWER_MODEL=ANSWER_MODEL, SQL_WAREHOUSE_ID=WAREHOUSE_ID)
from databricks_agent.serving_entry import SkillsAgent
from mlflow.types.responses import ResponsesAgentRequest
import json

req = ResponsesAgentRequest(input=[{"role": "user", "content": json.dumps(
    {"app": "teste", "user": "eu", "prompt": "tickets deste cliente", "input": "",
     "fields": {"cpf": "<cpf-de-teste>"}})}])
print(SkillsAgent().predict(req).output[0])

# COMMAND ----------
import mlflow
from mlflow.models.resources import DatabricksServingEndpoint, DatabricksSQLWarehouse

mlflow.set_registry_uri("databricks-uc")
with mlflow.start_run():
    info = mlflow.pyfunc.log_model(
        name="agent",  # em versões antigas do mlflow o argumento é artifact_path="agent"
        python_model="databricks_agent/serving_entry.py",   # "models from code"
        code_paths=["databricks_agent"],                     # leva skills/*.md junto, versionadas com o modelo
        pip_requirements=["mlflow", "databricks-sdk", "pyyaml", "openai"],
        resources=[DatabricksServingEndpoint(endpoint_name=ROUTER_MODEL),
                   DatabricksServingEndpoint(endpoint_name=ANSWER_MODEL),
                   DatabricksSQLWarehouse(warehouse_id=WAREHOUSE_ID)],
    )
reg = mlflow.register_model(info.model_uri, UC_NAME)

# COMMAND ----------
from databricks import agents

d = agents.deploy(UC_NAME, reg.version, environment_vars={
    "AGENT_ROUTER_MODEL": ROUTER_MODEL, "AGENT_ANSWER_MODEL": ANSWER_MODEL, "SQL_WAREHOUSE_ID": WAREHOUSE_ID})
print(d.endpoint_name)  # este nome vai no campo `endpoint:` do apps/<app_id>.yaml do gateway

# COMMAND ----------
# PERMISSÕES (fora deste notebook): o usuário/identidade usado pelo gateway precisa de CAN QUERY no endpoint;
# a identidade do endpoint precisa de USE CATALOG/USE SCHEMA/SELECT nas tabelas das skills e CAN USE no warehouse.
