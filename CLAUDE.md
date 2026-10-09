# Databricks Gateway — roteador 24/7

> ## ▶ Se você é o Claude da EMPRESA (recebeu este projeto pronto de outro ambiente)
> **Comece por `docs/INSTRUCOES_CLAUDE_EMPRESA.md`** (roteiro passo a passo, o que está testado e o que NÃO está,
> decisões que são do usuário, armadilhas). Depois `python -m scripts.doctor`. Nada aqui foi validado contra um
> Databricks real. Para gerar um pacote limpo (sem segredos) a partir da origem: `python -m scripts.make_package`.

> **Handoff:** tudo que é configurável fica mapeado em `docs/CONFIGURACAO.md`; o que o agente do Databricks precisa
> respeitar está em `docs/CONTRATO_AGENTE.md`. Ao criar qualquer config nova (variável, caminho, porta, permissão,
> decisão de rede), registre lá no mesmo passo. O código será levado para a máquina da empresa e configurado por outro
> Claude que só terá esses docs + este arquivo.

## O que é
Servidor FastAPI numa máquina da empresa ligada 24/7. É um **roteador fino** entre os front-ends (Google Apps Script
`index.html`, outros) e **agentes que vivem dentro do Databricks**. O agente (queries, dados, prompt, LLM) é construído
no Databricks; o gateway só leva o texto do operador (CPF, protocolo ou pergunta) até o agente certo e devolve a resposta.

```
Operador → index.html (navegador, rede da empresa) ──HTTPS+token──▶ Gateway (esta máquina)
                                                                        │ OAuth do navegador (sem PAT)
                                                                        ▼
                                                          Agente no Databricks (queries + LLM)
                                                                        │ só o texto final
Operador ◀─────────────────────────────── resposta ◀───────────────────┘
```
- **Dados e chaves de LLM nunca saem do Databricks.** Só trafega o texto de entrada e a resposta final.
- O `Code.gs` do Apps Script **não** chama o gateway (roda nos servidores do Google e não alcança a rede interna).
  Ele só emite um **token assinado de vida curta**; quem chama é o `index.html`, no navegador do operador.
- Extra: painel de acompanhamento (chamadas, app de origem, latência, estado do token, erros).

## Restrições ABSOLUTAS (não negociáveis)
1. **Nunca** gerar/obter PAT, token de Settings, service principal secret nem pedir liberação à SI. Não existe essa via.
2. A autenticação em **produção** é sempre via **login no navegador** (OAuth U2M do Databricks).
3. `databricks-token.txt` (raiz) e a pasta de credenciais pessoais do ambiente de origem (`acessos/`) são **só do ambiente de teste pessoal**: nunca commitar,
   nunca logar, nunca levar para a máquina da empresa.
4. Dados de clientes (CPF, tickets) não ficam em disco nem em log. Eventos guardam só metadados; CPF é mascarado.
5. Tem que rodar 24/7 sem ninguém olhando (após a validação humana da primeira subida).

## Decisões tomadas
- **Python 3.12 + FastAPI.** LLM/agente via pacote `openai` (Model Serving do Databricks é compatível com OpenAI:
  `base_url=https://<host>/serving-endpoints`, `model`=nome do endpoint). Sem `dbx` (descontinuado) e sem SQL no gateway.
- **Roteamento:** `apps/<app_id>.yaml` mapeia app → endpoint do agente + tipo de entrada + limites.
- **Autenticação do chamador:** token HMAC-SHA256 (`gateway/security.py`) emitido pelo `Code.gs` com `{app, sub=email, exp}`,
  validade ~5 min, segredo só no Script Properties e no gateway. `Authorization: Bearer <token>`.
- **Chamada do navegador:** HTTPS obrigatório, CORS restrito à origem do Apps Script, `Access-Control-Allow-Private-Network`.
- Teste local usa DeepSeek (`apps/demo.yaml`) no lugar do agente. Não vai para a empresa.

## Estratégia de autenticação no Databricks (implementada, NÃO testada em Databricks real)
Código: `gateway/auth/token_manager.py` (testado, lógica de camadas/backoff) e `gateway/auth/databricks_oauth.py`
(OAuth U2M via databricks-sdk + Playwright; só validável na empresa). Login humano da 1ª subida: `python -m scripts.login`.
Camadas; cada uma só roda se a anterior falhar:
1. Token em memória válido (renovar proativamente com margem de ~5 min).
2. Refresh token OAuth (silencioso), guardado criptografado fora do repo.
3. Re-login automático no navegador com **perfil persistente** (Playwright): a sessão SSO salva conclui o OAuth sem clique.
4. `AUTH_DEGRADED`: se o IdP exigir humano (MFA, senha), responde 503 + `Retry-After`, alerta (canal a definir) e
   tenta de novo com backoff; nunca derruba o processo.

Regras: um único lock de renovação; 401 do Databricks → renovar + **1 retry**; todo evento de auth vai ao painel.
**Risco conhecido:** se o SSO da empresa forçar MFA a cada login ou não permitir reaproveitar a sessão, a renovação
100% autônoma não é garantida. Só dá para saber na máquina da empresa → piloto de 7 dias antes do go-live.

## Agente dentro do Databricks (`databricks_agent/`)
Não é RAG clássico: **roteamento de skills em 2 estágios** para muitas skills parecidas (~20+, queries grandes):
candidatas (filtro por campo do formulário + todas as fichas, ou busca com sinônimos se >30 skills) → LLM escolhe entre
fichas (com `not_when`, `exclusive_with`, pergunta de esclarecimento) → só as seções relevantes → queries planejadas
pela ficha `answers` (o LLM nunca lê nem escreve SQL; execução paralela com timeout) → resposta. Medido em corpus
sintético: a busca só por palavras perde paráfrases (recall@6 67%); modo direto e busca+sinônimos acertaram 12/12 —
**não vale como prova**, valide com perguntas reais (`scripts/eval_routing.py`, `scripts/lint_skills.py`).
Detalhes e passos na empresa: `docs/ARQUITETURA_AGENTE.md`.

## Painel executivo (`/painel`)
Visão para diretoria, sem jargão técnico, em tela fixa/quiosque: volume (vs ontem), % sucesso, tempo médio, pessoas
atendidas, por hora, 7 dias, por aplicação, assuntos, instabilidades, status em português. Alimentado por
`gateway/observability/store.py` (SQLite só de metadados; e-mail em hash). A visão técnica continua em `/admin`.

## Conversa e robustez (adicionados depois)
- **Histórico de conversa** (opcional por app): memória DENTRO do Databricks (Delta), chave HMAC amarrada a
  app+usuário, pergunta de continuação reescrita, campos herdados, falha de memória não derruba o atendimento.
  `docs/ARQUITETURA_AGENTE.md` "Histórico de conversa".
- **Gateway à prova de erro:** `request_id` (idempotência), `X-Request-Id` em todo retorno, limite de simultâneos
  (global/por app), corpo máx. 64 KB, rotação de segredo (`TOKEN_SECRET_PREVIOUS`), gravação de histórico que nunca
  derruba o atendimento, vigia de taxa de erro com alerta.
- **`python -m scripts.doctor`**: diagnóstico de pré-voo (PASS/WARN/FAIL). Primeiro comando na máquina da empresa.
- **`python -m scripts.demo_painel`**: mostra o painel executivo com dados fictícios (marcado como demonstração).
- Guia "quando algo falha" e rotação de segredo: `docs/DEPLOY.md`.

- **Painel executivo (identidade visual da empresa):** uma página sem rolagem (só listas/gráficos longos rolam por dentro), metas e
  p90 visíveis, faixa de alerta, nomes amigáveis; cores só em `dashboard/tema.css` (aproximação NÃO oficial — trocar
  pelo manual da marca) e logo opcional `dashboard/logo.svg`. Variante `?tema=escuro`.
- **`python -m scripts.make_package`**: zip limpo para levar à empresa (varre segredos; recusa se achar).

- **Segurança revisada antes de publicar o repositório:** ver `SECURITY.md`. Comparação de chave em tempo constante +
  bloqueio de tentativa e erro; `PANEL_KEY` só-leitura separada da `ADMIN_KEY`; limite de corpo também em envio em partes;
  cabeçalhos (CSP, no-store, no-referrer, nosniff, frame deny); `/docs` desligado; recusa de subir em produção com segredo
  fraco; `python -m scripts.check_leaks --git` (padrões + valores reais dos segredos locais) antes de todo push;
  `pip-audit` limpo. O repositório pode ser público: **não contém segredos nem referências à empresa**.

## Estado do código (2026-10-08) — 123 testes `pytest` passando
**Testado localmente (inclui chamadas reais ao DeepSeek):** `/health` `/ready` `/v1/run` (texto+prompt+campos)
`/admin/status` `/admin/events` `/admin` (painel), token HMAC, registro de apps com validação de campos, limite por app,
CORS + rede privada, TokenManager, circuit breaker, retry em 401, estado DEGRADED/503+Retry-After, alertas por webhook,
núcleo do agente (skills/roteador/queries/defesas), demo `python -m scripts.demo_agent`.

**Escrito, NÃO testado (só dá na empresa):** login OAuth real + Playwright (`databricks_oauth.py`, `scripts/login.py`),
`serving_entry.py` / `log_and_deploy.py` / `DatabricksSqlRunner`, `deploy/*.ps1` (sintaxe ok), cliente Apps Script.
Nada foi validado contra um workspace Databricks real.

## Estrutura
```
gateway/  main.py config.py security.py alerts.py
  auth/ (token_manager, databricks_oauth)   databricks/llm_client.py   routing/registry.py
  resilience/ (ratelimit, breaker)          observability/events.py
databricks_agent/  skills.py router.py sql_runner.py agent.py serving_entry.py log_and_deploy.py  skills/*.md
apps/      um YAML por app (_example.yaml = modelo, demo.yaml = teste local, NÃO levar)
clients/appscript/   Code.gs (assina token) + index.html (formulário que chama o gateway)
dashboard/index.html (painel)   deploy/ (*.ps1: serviço NSSM, watchdog, hardening)
scripts/   test_llm, mint_token, login, demo_agent     tests/   pytest
docs/      CONFIGURACAO.md  ARQUITETURA_AGENTE.md  CONTRATO_AGENTE.md  DEPLOY.md
```

## Confiabilidade 24/7 (requisitos)
Serviço com auto-restart + watchdog; `/health` e `/ready`; timeouts em tudo; circuit breaker; logs rotativos sem dados
sensíveis; sem suspensão/hibernação; updates fora do horário; teste de caos (expirar token, matar navegador, cair rede).

## Fases
1. ✅ Fundação + LLM via `openai` + roteamento por app + token + eventos + testes.
2. ✅ (escrito) Auth por navegador, resiliência, painel, agente com skills, scripts de deploy — falta validar na empresa.
3. **Na empresa, nesta ordem:** `docs/CONFIGURACAO.md` seção 4 (conectividade) → `docs/ARQUITETURA_AGENTE.md` "Passos na
   empresa" (publicar o agente) → `docs/DEPLOY.md` (serviço 24/7) → corrigir o que o ambiente real exigir.
4. Piloto de 7 dias (expiração de token/SSO, queda de rede, warehouse parado) antes do go-live.
5. Pendentes de decisão: canal de alerta (webhook), HTTPS interno com a TI, reaproveitamento de sessão SSO.

## Convenções
Python tipado, `pydantic`, `pytest`; relógio injetável nos testes de auth; segredos só por ambiente/arquivo fora do repo;
responder ao usuário em português.
