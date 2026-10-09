# Configuração e handoff para a máquina da empresa

Para o Claude que for configurar o gateway na máquina da empresa. **Mantenha atualizado**: todo item configurável novo
entra aqui. Veja `CLAUDE.md` (arquitetura e restrições absolutas) e `docs/CONTRATO_AGENTE.md` (o que o agente do
Databricks recebe/devolve).

## 1. Estado atual
**Testado em casa (104 testes `python -m pytest -q` + chamadas reais ao DeepSeek + painel visto no navegador):** `/health`, `/ready`, `/v1/run`
(texto + prompt + campos validados), `/admin/status`, `/admin/events`, painel `/admin`, registro de apps, token assinado,
limite por app, CORS + rede privada, `TokenManager` (camadas, backoff, DEGRADED, invalidate), circuit breaker,
retry em 401, núcleo do agente (skills, roteador, queries parametrizadas, defesas).

**Escrito mas NÃO testado (precisa do ambiente da empresa):**
- Login real: `gateway/auth/databricks_oauth.py` (OAuth U2M via databricks-sdk + Playwright), `scripts/login.py`.
- `databricks_agent/serving_entry.py`, `log_and_deploy.py`, `DatabricksSqlRunner` (Databricks real).
- `deploy/*.ps1` (só sintaxe checada), cliente Apps Script (`clients/appscript/`).
- Nenhuma parte foi testada contra um workspace Databricks.

Docs relacionados: `CLAUDE.md` (visão e restrições), `ARQUITETURA_AGENTE.md` (agente, skills, RAG?),
`CONTRATO_AGENTE.md` (formato gateway↔agente), `DEPLOY.md` (Windows 24/7).

## 2. Variáveis de ambiente (`.env`; nomes em `.env.example`)
| Variável | Padrão | Casa (teste) | Empresa | Observação |
|---|---|---|---|---|
| `LLM_BASE_URL` | `https://api.deepseek.com` | idem | `https://<WORKSPACE_HOST>/serving-endpoints` | Sem `/` no fim. |
| `LLM_MODEL` | `deepseek-chat` | idem | qualquer; **cada app usa o `endpoint` do próprio YAML** | Só é usado se o app não definir endpoint. |
| `LLM_API_KEY` | — | — | token OAuth da sessão (injetado pelo TokenManager na Fase 3) | Tem prioridade sobre o arquivo. |
| `LLM_API_KEY_FILE` | — | arquivo do DeepSeek | **não usar** | Só teste local. |
| `LLM_TIMEOUT_S` | `60` | | subir se o endpoint tiver cold start | |
| `LLM_MAX_RETRIES` | `2` | | | Retries do SDK `openai` (429/5xx/rede). |
| `LLM_TEMPERATURE` | `0.2` | | confirmar se o agente aceita/ignora | |
| `LLM_MAX_TOKENS` | `300` | | por app via `max_tokens` no YAML | |
| `TOKEN_SECRET` | — (**obrigatório**) | `dev-local-...` | string aleatória longa (≥32 bytes) | Igual a `GATEWAY_TOKEN_SECRET` do Apps Script. Sem ela `/v1/run` devolve 503. |
| `APPS_DIR` | `apps` | | | Pasta dos YAMLs. |
| `CORS_ORIGIN_REGEX` | `^https://[a-z0-9-]+-script\.googleusercontent\.com$` | | **conferir a origem real** | Ver seção 5. |
| `CORS_ORIGINS` | vazio | | origens extras, separadas por vírgula | Ex.: outro front-end. |
| `ADMIN_KEY` | vazio = painel técnico desligado | valor de desenvolvimento (só local) | aleatória, ≥16 caracteres | Header `X-Admin-Key` em `/admin/*` (vê e-mails dos operadores). Em `AUTH_MODE=browser` o gateway **não sobe** se for fraca/de exemplo. |
| `PANEL_KEY` | vazio | | aleatória, ≥16, **diferente** da `ADMIN_KEY` | Chave SÓ-LEITURA do painel executivo (dados agregados, sem e-mail/CPF). É a que vai na URL `?k=` da tela fixa: se vazar (log de proxy, histórico), não abre o `/admin`. |
| `EXPOSE_DOCS` | `false` | | `false` | Liga `/docs`, `/redoc`, `/openapi.json` (deixe desligado). |
| `ADMIN_MAX_FAILURES` | `10` | | | Falhas de chave por cliente em 5 min → 429 por alguns minutos (anti tentativa e erro). |
| `EVENTS_BUFFER` | `500` | | | Eventos em memória (perde ao reiniciar). |
| `AGENT_API` | `chat` | `chat` | `chat` (ChatAgent/foundation model) ou `responses` (ResponsesAgent) | Define se o gateway chama `chat.completions` ou a API Responses. Descobrir qual o agente publicado expõe. |
| `AGENT_PAYLOAD_MODE` | `json-content` | idem | `json-content` (padrão) ou `custom-inputs` | Como o payload `{app,user,prompt,input,fields}` chega ao agente. `serving_entry.py` aceita os dois. |
| `AUTH_MODE` | `static` | `static` | **`browser`** | `static` usa `LLM_API_KEY`; `browser` usa OAuth U2M + TokenManager (exige `DATABRICKS_HOST`). |
| `DATABRICKS_HOST` | — | — | `https://<workspace>` | Só em `AUTH_MODE=browser`. |
| `BROWSER_PROFILE_DIR` | `browser-profile` | | caminho fixo, ACL só da conta do serviço | Perfil persistente do navegador (sessão SSO). |
| `BROWSER_CHANNEL` | `msedge` | | `msedge` ou `chrome` (ou `chromium` após `playwright install`) | Qual navegador a automação usa. |
| `BROWSER_HEADLESS` | `true` | | `true` (24/7); o login humano usa `scripts.login` (visível) | Se o SSO exigir tela, headless falha → DEGRADED + alerta. |
| `BROWSER_LOGIN_TIMEOUT_S` | `120` | | subir se o SSO for lento | Prazo da automação. O SDK não tem timeout próprio. |
| `TOKEN_REFRESH_MARGIN_S` | `300` | | | Renova em background quando faltar menos que isso. |
| `AUTH_BACKOFF_S` / `AUTH_BACKOFF_MAX_S` | `30` / `900` | | | Espera após falha total (dobra até o teto). |
| `BREAKER_FAILURES` / `BREAKER_RESET_S` | `5` / `30` | | | Circuit breaker do Databricks. |
| `ALERT_WEBHOOK_URL` | vazio | | webhook Slack/Teams/etc. (POST `{"text":...}`) | Alertas de auth DEGRADED e circuito aberto. Vazio = só log/painel. |
| `ALERT_MIN_INTERVAL_S` | `900` | | | Anti-flood por tipo de alerta. |
| `TOKEN_SECRET_PREVIOUS` | vazio | | só durante a rotação do segredo | O gateway aceita tokens assinados com o segredo atual **ou** este. Conversas abertas recomeçam após a troca (a chave de conversa usa o segredo atual). |
| `MAX_BODY_BYTES` | `65536` | | | Corpo maior → 413 (checado pelo `Content-Length`; proxy reverso deve limitar também). |
| `MAX_CONCURRENCY` | `32` | | ajustar à capacidade do warehouse/endpoint | Atendimentos simultâneos no gateway todo; acima → 429 + `Retry-After: 3`. Cada app tem seu teto (`max_concurrent` no YAML). |
| `REQUEST_CACHE_TTL_S` | `120` | | | Por quanto tempo um `request_id` repetido devolve a mesma resposta (idempotência). |
| `ERROR_ALERT_RATE` / `ERROR_ALERT_MIN_CALLS` | `0.3` / `10` | | | Alerta no webhook se ≥30% dos atendimentos (janela de 5 min, mínimo 10) falharem por culpa do serviço (5xx). |
| `PANEL_DEMO` | `false` | `true` só em `scripts.demo_painel` | **false** | Marca "DADOS DE DEMONSTRAÇÃO" no painel. |
| `CONVERSATION_STORE` / `CONVERSATION_TABLE` | `off` | | **no endpoint do agente** (variável de ambiente do deploy): `delta` + `<cat>.<schema>.conversas` | Memória de conversa do agente. Ver `ARQUITETURA_AGENTE.md`. |
| `HISTORY_DB` | `data/history.sqlite` | idem | caminho fixo em disco local (não em pasta de rede) | Histórico do painel executivo (SQLite). Só metadados; e-mail do operador vira hash com sal (`TOKEN_SECRET`). `:memory:` = não guarda. Retenção 400 dias. Fazer backup se o histórico importar. |
| `PANEL_TARGET_SUCCESS` / `PANEL_TARGET_SECONDS` | `95` / `8` | | metas reais da operação | Metas exibidas nos indicadores do painel (✓ quando atingidas). |
| `PANEL_TITLE` | `Assistente de Atendimento` | | nome que a diretoria reconhece | Título do painel executivo. |
| `PANEL_LOCALHOST_NO_KEY` | `false` | `false` | `true` **só** se NÃO houver proxy reverso | Quem abre `/painel` na própria máquina não precisa da chave. Atrás de proxy, o proxy chega como localhost e abriria o painel para a rede. |
| `GATEWAY_SSL_CERT` / `GATEWAY_SSL_KEY` | — | — | caminhos .crt/.key (variáveis de **sistema**, lidas por `run-gateway.ps1`) | HTTPS direto no uvicorn; alternativa: proxy reverso. |

### Painel executivo (tela fixa na máquina)
- **Identidade visual:** `dashboard/tema.css` (todas as cores e a fonte; valores atuais = aproximação NÃO oficial em azul,
  trocar pelo manual de marca) e `dashboard/logo.svg|png` (opcional, aparece no cabeçalho). Nada mais precisa mudar.
- Layout: uma página sem rolagem; só os blocos longos (lista de aplicações/assuntos, gráfico de horas em tela estreita)
  rolam por dentro, e as listas rolam sozinhas devagar (pausam com o mouse). Abaixo de 900 px de largura a página rola.
- Página: `http://<host>:8080/painel` (quiosque: `deploy\open-painel.ps1`). Dados em `/painel/dados` (exige a **`PANEL_KEY`**
  — só-leitura, dados agregados — ou a `ADMIN_KEY`; via header `X-Admin-Key` ou `?k=` na URL; a página guarda a chave no
  navegador). **Use a `PANEL_KEY` na URL da tela fixa**, nunca a `ADMIN_KEY`: a URL aparece em histórico/log de proxy,
  e a `ADMIN_KEY` abre `/admin` (e-mails dos operadores). `?tema=claro|escuro`.
- Mostra: atendimentos hoje (e comparação com ontem no mesmo horário), % de respostas com sucesso, tempo médio, pessoas
  atendidas, atendimentos por hora, últimos 7 dias, por aplicação, assuntos mais consultados, instabilidades do dia e
  status em português ("Operando normalmente" / "Instabilidade..." / "Fora do ar" / "Sem conexão com o serviço").
  Sem jargão técnico (a visão técnica continua em `/admin`).
- **Assuntos** dependem do agente devolver `custom_outputs.topics` (`serving_entry.py` já devolve os `title:` das skills).
  Se o endpoint real não expuser `custom_outputs` pela API usada, o quadro de assuntos fica vazio — conferir no teste.
- "Sucesso" = HTTP 200 sobre (200 + falhas do serviço 5xx). Erros de uso (validação 422, limite 429, token) não contam.
- Instabilidade = autenticação DEGRADED ou circuito do Databricks aberto.

## 3. Um app = um arquivo `apps/<app_id>.yaml` (modelo: `apps/_example.yaml`)
**O que o app envia** (`POST /v1/run`, `Authorization: Bearer <token do Code.gs>`):
`{"input": "<texto do operador>", "prompt": "<prompt escolhido/digitado>", "fields": {"cpf": "...", "assunto": "..."}}`.
Campos opcionais: `request_id` (8-64 chars `A-Za-z0-9_-`; reenviar o MESMO id num retry/duplo clique devolve a mesma
resposta sem rodar o agente de novo) e `conversation_id` (mesmo formato). Toda resposta e todo erro trazem
`request_id` / header `X-Request-Id`: **é o código que o operador informa ao suporte** para achar o atendimento nos eventos.
Respostas de falha temporária trazem `Retry-After` (429 e 503).
A identificação da aplicação e do usuário vem **do token** (campos `app` e `sub`), nunca do corpo. O gateway valida tudo
contra o YAML e entrega ao agente `{"app","user","prompt","input","fields"}`.

| Campo | Obrigatório | Significado |
|---|---|---|
| `app_id` | sim | Identificador. **Tem que ser igual** ao `APP_ID` do Script Properties do Apps Script (vai no token). |
| `endpoint` | sim | Nome do serving endpoint do **agente** no Databricks (vira o `model` da chamada). |
| `input_kind` | não (`text`) | Tipo do campo principal `input`: `text`, `cpf`, `protocolo`, `enum`... Validado antes de sair da máquina. |
| `input_required` | não (true) | `false` permite `input` vazio (só prompt/campos). |
| `max_prompt_chars` | não (4000) | Limite do prompt. |
| `max_concurrent` | não (8) | Atendimentos simultâneos deste app; acima → 429 + `Retry-After`. |
| `conversation` | não | `{enabled: true, max_turns: 6}` liga o histórico de conversa (guardado no Databricks, ver `ARQUITETURA_AGENTE.md`). O corpo da requisição passa a aceitar `conversation_id`, e a resposta devolve `conversation_id` (e `clarify: true` quando o agente fez uma pergunta de volta). |
| `fields` | não | Mapa `nome: {type, required, max_len, values}` — um item por campo do formulário. Tipos: `text`, `cpf` (normaliza para 11 dígitos), `protocolo` (regex `[A-Za-z0-9\-/._]{3,40}`), `enum` (exige `values`), `int`, `bool` (true/false), `date` (AAAA-MM-DD). **Campo fora da lista é rejeitado (422).** Erros de validação nunca repetem o valor. |
| `max_input_chars` | não (2000) | Limite do texto. |
| `rate_limit_per_min` | não (60) | Requisições/min por app (janela deslizante, em memória, por processo). |
| `max_tokens` | não | Limite de saída; vazio = `LLM_MAX_TOKENS`. |
| `enabled` | não (true) | `false` responde 403. |
| `system_prompt` | não | **Só teste local.** Em produção o prompt vive dentro do agente; não usar. |
Arquivos que começam com `_` são ignorados. Reiniciar o serviço após mudar YAML (registro é carregado uma vez).
**Na empresa: apagar `apps/demo.yaml`** e criar um YAML por aplicação real.

## 4. Primeiro teste no Databricks da empresa
0. **Rode `python -m scripts.doctor`** (e `--offline` se ainda sem rede): ele confere pacotes, segredos, apps, skills,
   permissões de disco, porta, navegador, DNS/TLS/relógio e faz uma chamada de teste. Resolva todo `[FALHA]` e leia
   cada `[AVISO]` antes de seguir. Repita sempre que algo estranho acontecer.
1. `python -m venv .venv` → `.venv\Scripts\pip install -r requirements.txt`
2. `.env`: `LLM_BASE_URL=https://<host>/serving-endpoints`, `LLM_API_KEY=<token temporário de sessão>`, `TOKEN_SECRET`, `ADMIN_KEY`.
3. Criar `apps/<app_id>.yaml` com o `endpoint` do agente.
4. `python -m scripts.test_llm` (usa `LLM_MODEL`; ponha o nome do endpoint lá só para este teste) → resposta do agente.
   - 401/403: token expirado ou sem permissão `CAN QUERY` no endpoint. 404: nome do endpoint/URL errados.
   - Erro de TLS/proxy: ver seção 6.
5. Subir: `python -m uvicorn gateway.main:app --host 0.0.0.0 --port 8080`; gerar token com
   `python -m scripts.mint_token <app_id> email@empresa.com`; `POST /v1/run` com `Authorization: Bearer <token>` e
   `{"input":"<cpf>"}`.
6. Se o agente não responder no formato OpenAI `chat.completion`, ajustar `llm_client.ask` (único ponto) — ver `CONTRATO_AGENTE.md`.
7. Obter o token **pelo navegador** (nunca PAT). A automação é a Fase 3; até lá, token de sessão copiado à mão só para testar.

## 5. Rede e navegador (o `index.html` chama o gateway direto do navegador do operador)
Motivo: o `Code.gs` roda nos servidores do Google e não alcança a rede interna; o navegador do operador alcança.
- [ ] **HTTPS obrigatório**: a página do Apps Script é HTTPS e o navegador bloqueia chamada para `http://` (conteúdo misto).
      Precisa de hostname interno + certificado que as máquinas dos operadores confiem (CA da empresa). Pedir à TI.
      Rodar o uvicorn com `--ssl-keyfile/--ssl-certfile` ou colocar um proxy reverso (IIS/nginx/Caddy) na frente.
- [ ] **CORS**: a origem das páginas Apps Script é `https://<hash>-script.googleusercontent.com`. Confirmar no DevTools
      (aba Rede, header `Origin`) e ajustar `CORS_ORIGIN_REGEX` se diferir.
- [ ] **Private Network Access**: Chrome/Edge restringem site público → host privado. O gateway já responde
      `Access-Control-Allow-Private-Network: true` no preflight. Testar nos navegadores dos operadores; políticas
      corporativas (GPO `InsecurePrivateNetworkRequestsAllowedForUrls` ou equivalente) podem ser necessárias.
- [ ] Firewall: porta do gateway (sugerida 8080/443) liberada **a partir das estações dos operadores**.
- [ ] Apps Script: publicar como Web App; preencher Script Properties `GATEWAY_TOKEN_SECRET`, `GATEWAY_URL`, `APP_ID`;
      `Session.getActiveUser().getEmail()` só devolve e-mail em domínio Workspace com "executar como usuário que acessa".
- Se algo disso for vetado pela TI, alternativa é túnel/IP público — **decisão de rede pendente**.

## 6. Checklist: o que só dá para descobrir na empresa
- [ ] `WORKSPACE_HOST`, cloud (Azure/AWS/GCP) — muda a URL do OAuth.
- [ ] Nome do endpoint de cada agente e se aceita o formato `chat.completions`.
- [ ] App OAuth `databricks-cli` habilitado? Escopos permitidos? (admin pode desativar)
- [ ] IdP do SSO (Entra ID/Okta), MFA a cada login?, duração da sessão SSO e do refresh token → decide se a Fase 3 é 100% autônoma.
- [ ] Navegador instalado (Chrome/Edge) e automação permitida (Playwright com perfil persistente).
- [ ] Proxy corporativo: `HTTPS_PROXY`/`NO_PROXY`, certificado raiz (`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`).
- [ ] Canal de alerta para quando a auth precisar de humano (e-mail/Slack/Teams) — indefinido.
- [ ] Python 3.12; usuário do Windows que roda o serviço (o perfil do navegador logado pertence a ele).
- [ ] Windows: sem suspensão/hibernação, janela de updates, serviço com auto-restart (NSSM/Task Scheduler) e watchdog.
- [ ] Instância única: o limite por app e os eventos estão em memória; se rodar mais de um processo, mover para Redis/DB.

## 7. Segurança (não violar)
- `databricks-token.txt` e `acessos/` não vão para a empresa nem para repositório.
- Nenhuma chave/token/CPF em log. Eventos guardam só metadados (app, usuário, status, ms, tamanhos).
- `TOKEN_SECRET` (≥32), `ADMIN_KEY` e `PANEL_KEY` (≥16, **distintas entre si**) novos e aleatórios na empresa; nunca
  reutilizar os de desenvolvimento. Gerar: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
  Em `AUTH_MODE=browser` o gateway **recusa subir** com segredo curto/de exemplo (`gateway/security.py::secret_problems`).
- Antes de publicar/copiar o projeto: `python -m scripts.check_leaks --git` (ver `SECURITY.md`).
- O token do Apps Script dura ~5 min; o `index.html` busca um novo a cada envio.

## 8. Comandos úteis
`python -m pytest -q` · `python -m scripts.test_llm` · `python -m scripts.mint_token <app_id> [email] [ttl_s]`
· `python -m uvicorn gateway.main:app --port 8080` · No Windows use `PYTHONIOENCODING=utf-8` para ver acentos.

## 9. Registro de mudanças
- 2026-10-08: criação; LLM via `openai` testado com DeepSeek.
- 2026-10-08: texto+prompt+campos no `/v1/run`; TokenManager + login OAuth (não testado), breaker, alertas, `/ready`,
  painel `/admin`, agente com skills (`databricks_agent/`), scripts de deploy Windows, docs ARQUITETURA_AGENTE e DEPLOY.
- 2026-10-08: arquitetura com agente dentro do Databricks; `/v1/run`, token HMAC, registro de apps, CORS + rede privada, eventos, testes.
