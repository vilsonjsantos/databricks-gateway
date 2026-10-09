# Databricks Gateway

Gateway FastAPI que fica entre os front-ends de atendimento (por exemplo, uma página do Google Apps Script) e um **agente
que roda dentro do Databricks** (skills em markdown, queries parametrizadas e LLM). A autenticação no Databricks é feita
por **login no navegador (OAuth)** com renovação automática, e o serviço degrada com elegância (503 + `Retry-After`)
em vez de cair. Os dados de cliente ficam no Databricks: só o texto da pergunta entra e só a resposta final sai.

> Documentação em português. Este repositório **não contém segredos**: configurações reais vão num `.env` local
> (veja `.env.example`) e nunca são versionadas.

## O que tem
- `gateway/` — API (`/v1/run`), token HMAC de curta duração, validação de campos por app, idempotência, limites de
  concorrência, circuit breaker, renovação de token, painel técnico (`/admin`) e **painel executivo** (`/painel`).
- `databricks_agent/` — agente: roteamento de skills (busca + escolha por LLM + regras), planejador de queries, memória
  de conversa opcional, adaptador para o Databricks Model Serving.
- `apps/` — um YAML por aplicação cliente. `clients/appscript/` — exemplo de `Code.gs` + `index.html`.
- `deploy/` — scripts do Windows (serviço 24/7, vigia, quiosque do painel). `scripts/` — diagnóstico, avaliação, pacote.
- `docs/` — arquitetura, configuração, deploy e instruções de implantação.

## Comece por aqui
```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (Linux/macOS: .venv/bin/pip)
python -m pytest -q                       # testes (não precisam de Databricks nem de internet)
python -m scripts.demo_painel             # painel executivo com dados FICTÍCIOS
python -m scripts.doctor --offline        # diagnóstico de configuração
```
Para implantar: `CLAUDE.md` → `docs/INSTRUCOES_CLAUDE_EMPRESA.md` → `docs/CONFIGURACAO.md` → `docs/DEPLOY.md`.

## Estado e limites
Testado com um LLM substituto (API compatível com OpenAI) e dados sintéticos. **Não foi validado contra um workspace
Databricks real**: login OAuth, agente publicado, tabela de conversas e scripts do Windows precisam ser validados no
ambiente de destino (as instruções listam cada ponto).

## Segurança
Modelo de ameaças, proteções e como reportar problemas: [`SECURITY.md`](SECURITY.md). Antes de qualquer publicação rode
`python -m scripts.check_leaks --git`.
