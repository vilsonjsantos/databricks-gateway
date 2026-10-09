"""Agente de atendimento que roda DENTRO do Databricks (Model Serving).

Núcleo em Python puro e testável localmente: skills (markdown) -> roteador (LLM escolhe skills) -> queries
declaradas pelas skills -> resposta (LLM). `serving_entry.py` é só o adaptador para o Databricks.
"""
