# Instruções para o Claude que vai assumir este projeto dentro da empresa

Você está recebendo um projeto **construído fora do ambiente da empresa**, sem acesso ao workspace Databricks real.
Tudo foi testado com um LLM substituto (DeepSeek) e dados sintéticos. Seu trabalho: **validar, adaptar e colocar no ar
24/7** o que o código supõe, sem quebrar as restrições absolutas. Leia este arquivo inteiro antes de mexer em qualquer coisa.

## 0. Em uma frase
Um gateway FastAPI numa máquina da empresa recebe chamadas do navegador do operador (Google Apps Script `index.html`),
valida, e as repassa a um **agente que vive dentro do Databricks** (queries + LLM + skills em markdown). Autentica no
Databricks **só por login no navegador** (OAuth), renova sozinho e nunca cai. Os dados de cliente não saem do Databricks:
só o texto da pergunta entra e só a resposta final sai. Há um painel executivo (`/painel`) e um técnico (`/admin`).

## 1. Restrições ABSOLUTAS (não negociáveis; confirme com o usuário antes de qualquer exceção)
1. **Nunca** gerar/pedir PAT, token de Settings, service principal secret nem liberação da SI. Produção = login no navegador.
2. `databricks-token.txt`, `.env` e a pasta `acessos/` do ambiente de origem **não existem aqui e não devem ser criados
   por cópia**. O `.env` da empresa é novo, com segredos novos e longos.
3. Dado de cliente (CPF, tickets, texto de atendimento) **nunca** em log, evento, painel nem arquivo local. Só no Databricks.
4. O LLM **nunca escreve nem lê SQL**; queries só vêm das skills (SELECT parametrizado). Não relaxe `skills.py::_validate_query`.
5. Não derrube o que funciona para "limpar": o gateway foi desenhado para degradar (503 + `Retry-After`), não para cair.

## 2. Leia nesta ordem
1. `CLAUDE.md` (visão, decisões, estado)  2. este arquivo  3. `docs/CONFIGURACAO.md` (todas as variáveis, YAML dos apps, checklist da empresa)
4. `docs/ARQUITETURA_AGENTE.md` (skills, roteamento, conversa)  5. `docs/CONTRATO_AGENTE.md` (formato gateway↔agente)
6. `docs/DEPLOY.md` (Windows 24/7, guia "quando algo falha", rotação de segredo).

## 3. O que está testado e o que NÃO está (seja cético com a segunda coluna)
| Testado aqui (110 testes `python -m pytest -q` + execuções reais com DeepSeek) | **NÃO testado — valide você** |
|---|---|
| Rotas `/v1/run`, `/ready`, `/health`, `/admin/*`, `/painel*`; token HMAC; validação de campos; CORS + rede privada | Qualquer chamada a um Databricks real |
| Idempotência, limite de simultâneos, corpo grande, rotação de segredo, alertas, histórico que não derruba | Login OAuth real + Playwright (`gateway/auth/databricks_oauth.py`, `scripts/login.py`) e a renovação autônoma |
| TokenManager (camadas, backoff, DEGRADED), circuit breaker, retry em 401 (com fakes) | `databricks_agent/serving_entry.py` (MLflow ResponsesAgent), `log_and_deploy.py`, `DatabricksSqlRunner`, `DeltaConversationStore` |
| Roteamento de skills, seções, planejador de queries, conversa (com LLM real e executores falsos) | Qualidade do roteamento com skills e perguntas REAIS (os 100% medidos são de corpus sintético escrito por mim) |
| Painel visto no navegador (claro/escuro, 720p/1080p) | `deploy/*.ps1` (só sintaxe checada), cliente Apps Script, HTTPS/CORS/PNA no navegador do operador |

## 4. Roteiro (faça na ordem; cada etapa tem critério de pronto)
**A. Ambiente.** Python 3.12; `python -m venv .venv`; `.venv\Scripts\pip install -r requirements.txt -r requirements-auth.txt`;
copie `.env.example` para `.env` e preencha (`TOKEN_SECRET` ≥32 chars; `ADMIN_KEY` e `PANEL_KEY` ≥16 e **diferentes**; todos
aleatórios, gerados com `python -c "import secrets; print(secrets.token_urlsafe(32))"`; ver CONFIGURACAO §2). Em produção
(`AUTH_MODE=browser`) o gateway **não sobe** com segredo fraco; use a `PANEL_KEY` (nunca a `ADMIN_KEY`) na URL da tela fixa.
Apague `apps/demo.yaml` se existir. **Pronto quando:** `python -m scripts.doctor --offline` sem `[FALHA]`.

**B. Conectividade e formato do agente.** Descubra: URL do workspace, cloud, endpoint de LLM/agente liberado, se aceita
`chat.completions` (`AGENT_API=chat`) ou Responses (`responses`), e se lê `custom_inputs` (`AGENT_PAYLOAD_MODE`).
Com um token de sessão **copiado à mão só para este teste** rode `python -m scripts.test_llm`. Erros prováveis: 401/403 =
token/permissão `CAN QUERY`; 404 = nome do endpoint/URL; TLS = proxy corporativo (`SSL_CERT_FILE`, `HTTPS_PROXY`).
**Pronto quando:** `scripts.doctor` (sem `--offline`) passa na rede e a chamada de teste responde.

**C. Publicar o agente.** Escreva as skills reais (ver ARQUITETURA_AGENTE "Como escrever uma skill"; as de exemplo têm
tabelas FICTÍCIAS). Rode `python -m scripts.lint_skills`. Execute `databricks_agent/log_and_deploy.py` célula a célula
num notebook; **espere divergências de API do mlflow/databricks-agents** e corrija só `serving_entry.py`/`log_and_deploy.py`.
Conceda permissões (CAN QUERY ao usuário do gateway; SELECT/USE/CAN USE à identidade do endpoint). Monte com o usuário
20-50 perguntas reais anonimizadas e rode `python -m scripts.eval_routing --skills databricks_agent/skills --cases casos.jsonl --real`.
**Pronto quando:** o endpoint responde via gateway e o acerto do roteamento é aceito pelo usuário (veja as confusões).

**D. Autenticação por navegador (parte mais arriscada).** `AUTH_MODE=browser`, `DATABRICKS_HOST`. Com a conta que rodará o
serviço: `python -m scripts.login` (navegador visível; o usuário faz SSO/MFA). Depois teste **a renovação sem humano**:
(1) aguarde/force expiração do access token e confirme renovação silenciosa; (2) apague o cache do SDK e confirme que o
perfil persistente refaz o login sozinho (headless); (3) se o IdP exigir MFA/interação, o estado deve ir a `DEGRADED`
(503 + alerta) e voltar a `OK` após `scripts.login`. Se (2) não for possível por política do SSO, **diga ao usuário
claramente** que a renovação 100% autônoma não é garantida e proponha: sessão SSO mais longa/dispositivo confiável para
a conta de serviço, ou rotina operacional com o alerta. Não contorne MFA. Verifique também a porta `8020` livre (callback fixo do SDK).
**Pronto quando:** 24 h seguidas sem intervenção, ou um plano aceito para o que exigir humano.

**E. Apps e front-end.** Um `apps/<app_id>.yaml` por aplicação (`fields:` = exatamente os campos do formulário; campo
desconhecido dá 422). No Apps Script: `Code.gs` + `index.html` de `clients/appscript/` e Script Properties
(`GATEWAY_TOKEN_SECRET`, `GATEWAY_URL`, `APP_ID`). **HTTPS interno** com certificado que as estações confiem (CONFIGURACAO §5),
`CORS_ORIGIN_REGEX` conferido no DevTools, e Private Network Access (pode exigir GPO do Edge/Chrome). **Pronto quando:** um
operador de teste consulta pelo navegador e vê a resposta.

**F. Serviço 24/7.** `deploy\harden-windows.ps1` → `deploy\install-service.ps1` (conta do passo D) → `deploy\watchdog.ps1`
agendado → `deploy\open-painel.ps1` no logon. Todos NÃO testados: leia cada comando antes de rodar. **Pronto quando:**
matar o processo o religa em segundos; reiniciar a máquina sobe tudo sozinho; o painel abre na tela.

**G. Conversa (somente para apps que precisam).** Crie a tabela `conversas` (DDL em ARQUITETURA_AGENTE), `CONVERSATION_STORE=delta`
+ `CONVERSATION_TABLE` no endpoint do agente, `conversation.enabled: true` no YAML do app. Defina retenção com o jurídico.

**H. Piloto de 7 dias.** Acompanhe o painel e os alertas; cada dia registre: instabilidades, reautenticações, p90, falhas.
Teste de caos antes: parar o warehouse, derrubar a rede, expirar o token, matar o navegador, reiniciar a máquina.

## 5. Decisões que são do USUÁRIO (pergunte; não assuma)
Canal do alerta (`ALERT_WEBHOOK_URL`: Slack/Teams?) · hostname/certificado HTTPS com a TI · política de retenção das conversas ·
metas do painel (`PANEL_TARGET_SUCCESS`, `PANEL_TARGET_SECONDS`) · quais apps/skills entram primeiro · o que fazer se o SSO
exigir MFA a cada login · quem recebe os alertas e o horário de plantão.

## 6. Identidade visual do painel (a da empresa)
Todas as cores/fonte estão **só** em `dashboard/tema.css`; os valores atuais são uma **aproximação genérica NÃO oficial**
em azul (o projeto foi feito fora da empresa, sem acesso ao manual de marca). Troque cada variável `--*` pelos valores oficiais, ajuste `--font` se houver
fonte corporativa instalada, e coloque o logo em `dashboard/logo.svg` (ou `.png`). Confira contraste (≥ 4,5:1 texto).
O painel é uma página sem rolagem; só as listas/gráficos longos rolam por dentro (rolam sozinhos devagar na tela fixa).
Não crie nem copie logos de fontes não oficiais.

## 7. Armadilhas já encontradas (economize tempo)
- **YAML do frontmatter:** `description`/`questions` com `:` precisam de aspas (o parser agora aponta o arquivo).
- **SDK Databricks:** o login `external-browser` espera o callback em `http://localhost:8020` **sem timeout**; o código já
  destrava a porta ao estourar o prazo. Não troque por chamada bloqueante.
- **Windows:** `bind` em `0.0.0.0` "passa" com outro processo em `127.0.0.1` (use conexão para testar porta); `taskkill` por
  imagem mata TODOS os `python.exe`: pare processos pelo PID/porta.
- **Serviço:** com `AUTH_MODE=browser` o serviço precisa rodar **na conta que fez o login** (perfil do navegador e cache do SDK são dela).
- **Pool de threads:** não use `with ThreadPoolExecutor` onde o timeout importa (o `__exit__` espera a query travada).
- **Tokens HMAC:** relógio errado (>60 s) invalida tokens; `scripts.doctor` confere.
- **Gate/hook de revisão:** neste repositório de origem havia hooks que pedem justificativa antes de criar arquivos; na
  empresa podem não existir. Não dependa deles.

## 7b. Segurança: o que você NÃO deve afrouxar (revisado antes da publicação do repositório)
Veja `SECURITY.md`. Em especial: não relaxe a recusa de segredo fraco, a validação estrita de campos, o `SELECT`-somente das
skills, o bloqueio de tentativa e erro, `EXPOSE_DOCS=false`, nem os cabeçalhos de segurança. Não coloque `ADMIN_KEY`/tokens em
URL, log, ticket ou chat. Se este código veio de um repositório público: **trate-o como público** (nenhuma informação interna
da empresa — nomes de tabelas/endpoints reais, hostnames, manual de marca — deve ser commitada de volta; mantenha isso no
`.env` e em arquivos fora do git) e rode `python -m scripts.check_leaks --git` antes de qualquer `push`.

## 8. Definição de pronto
- [ ] `scripts.doctor` sem `[FALHA]`; avisos lidos e aceitos pelo usuário.
- [ ] Endpoint do agente real respondendo via gateway; permissões mínimas; skills reais com `not_when`/`questions`/`answers`.
- [ ] Avaliação de roteamento com perguntas reais aprovada pelo usuário.
- [ ] Renovação do token sem humano validada (ou plano aceito); alerta de DEGRADED chega no canal escolhido.
- [ ] HTTPS + CORS + PNA funcionando no navegador de um operador real.
- [ ] Serviço sobe sozinho após reinício; watchdog e painel em quiosque ativos; tema da marca aplicado.
- [ ] Piloto de 7 dias sem incidente não explicado. **Documentação atualizada** (abaixo).

## 9. Ao terminar, deixe registrado
Atualize `docs/CONFIGURACAO.md` (§1 estado, §2 valores finais NÃO secretos, §9 registro de mudanças) e `CLAUDE.md` (estado
do código: o que passou a ser **testado em Databricks real** e o que mudou de formato). Se corrigiu algo que contradiz estas
instruções, **corrija este arquivo**. Nunca registre segredos nos docs.
