# Contrato entre o gateway e o agente no Databricks

O agente é construído **dentro do Databricks** (queries, prompt, chamada ao LLM, regras). O gateway só leva o texto
do operador até lá e devolve a resposta. Este documento é o que o agente precisa respeitar.

## Chamada (gateway → Databricks)
`POST https://<WORKSPACE_HOST>/serving-endpoints/chat/completions` (feito pelo pacote `openai`; ver `llm_client.py`):
```json
{ "model": "<endpoint do apps/<app_id>.yaml>",
  "messages": [ { "role": "user", "content": "<texto validado e normalizado>" } ],
  "temperature": 0.2, "max_tokens": <max_tokens do app ou LLM_MAX_TOKENS> }
```
- `content` (AGENT_PAYLOAD_MODE=`json-content`, padrão) é **um JSON** com tudo que o operador enviou, já validado:
  `{"app":"<app_id>","user":"<email>","prompt":"...","input":"...","fields":{"cpf":"12345678909","assunto":"consorcio"}}`.
  `app` e `user` vêm do token assinado (não do corpo). CPF chega com 11 dígitos; enums/datas/bools já validados.
  Em `AGENT_PAYLOAD_MODE=custom-inputs`, `content` é só o texto e o mesmo JSON vai em `custom_inputs`.
- Se `AGENT_API=responses`, a chamada é a API Responses (`input=[...]`) em vez de `chat.completions`.
- Nada mais viaja: sem chave de LLM, sem SQL, sem dado de cliente além do que o operador digitou.
- Header: `Authorization: Bearer <token OAuth da sessão no navegador>` (gerenciado pelo TokenManager, Fase 3).

## Resposta esperada (Databricks → gateway)
Formato OpenAI `chat.completion`: o gateway devolve `choices[0].message.content` ao navegador.
Se o agente for publicado em outro formato (ex.: `ResponsesAgent` / `/invocations` com `input`), é preciso trocar o
corpo da chamada em `gateway/databricks/llm_client.py::ask` — **é o único ponto** a mudar.

## O que o agente deve garantir
- Rodar as queries e o LLM dentro do workspace; devolver só o texto final.
- Não incluir na resposta mais dados do que o operador precisa (a resposta sai do Databricks).
- Responder em tempo razoável (o gateway usa `LLM_TIMEOUT_S`, padrão 60 s); cold start do endpoint conta.
- Dar permissão `CAN QUERY` no endpoint ao usuário cuja sessão de navegador o gateway usa.

## Para testar localmente sem agente
`apps/demo.yaml` aponta o gateway para `deepseek-chat` (com `system_prompt` só para teste). Na empresa, apagar o
`demo.yaml` e criar um `apps/<app_id>.yaml` por aplicação (modelo em `apps/_example.yaml`).
