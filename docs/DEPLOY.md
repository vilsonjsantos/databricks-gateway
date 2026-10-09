# Deploy 24/7 na máquina da empresa (Windows)

Tudo aqui foi escrito sem acesso à máquina da empresa: **nada de `deploy/` foi executado** (só checagem de sintaxe).
Revise cada comando antes de rodar.

## Como levar o projeto para a máquina da empresa
Na origem: `python -m scripts.make_package` gera `dist\databricks-gateway-<data>.zip` **sem segredos** (recusa se achar chave
no código; deixa de fora `.env`, `databricks-token.txt`, `.venv`, `data\`, `logs\`, `browser-profile\`, `apps\demo.yaml`).
Na empresa: extrair, criar `.venv`, criar `.env` NOVO, e entregar ao Claude de lá `docs/INSTRUCOES_CLAUDE_EMPRESA.md`.

## Ordem
0. **Diagnóstico:** `python -m scripts.doctor` (e de novo depois de cada etapa que mude configuração). Só siga com
   zero `[FALHA]`. Ele também é o primeiro passo quando algo "estranho" acontece em produção.
1. **Pré-requisitos:** Python 3.12, NSSM (nssm.cc) no PATH, Edge ou Chrome instalado, acesso de rede ao workspace.
2. **Código:** copiar a pasta do projeto **sem** `databricks-token.txt`, `.env`, `.venv`, `browser-profile/`, `.auth/`, `logs/`.
3. **Ambiente:** `python -m venv .venv` → `.venv\Scripts\pip install -r requirements.txt -r requirements-auth.txt`
   → (só se usar Chromium em vez de Edge/Chrome) `.venv\Scripts\python -m playwright install chromium`.
4. **`.env`** (a partir de `.env.example`): `AUTH_MODE=browser`, `DATABRICKS_HOST`, `LLM_BASE_URL=<host>/serving-endpoints`,
   `TOKEN_SECRET`, `ADMIN_KEY`, `ALERT_WEBHOOK_URL`, etc. Valores completos em `docs/CONFIGURACAO.md` seção 2.
5. **Apps:** apagar `apps/demo.yaml`; criar `apps/<app_id>.yaml` por aplicação.
6. **Login humano (1ª vez):** com a conta que vai rodar o serviço, `python -m scripts.login`. O navegador abre
   VISÍVEL, você faz SSO/MFA; a sessão fica em `browser-profile/` e o refresh token no cache do SDK. Depois disso as
   renovações são automáticas.
7. **HTTPS:** ver abaixo. Sem HTTPS o `index.html` (página HTTPS) não consegue chamar o gateway.
8. **Windows:** `deploy\harden-windows.ps1` (Administrador) — sem suspensão, horas ativas de update, regra de firewall.
9. **Serviço:** `deploy\install-service.ps1 -User ".\conta" -Password "..."` (Administrador). Com `AUTH_MODE=browser` o serviço
   TEM que rodar na conta do passo 6.
10. **Vigia:** agendar `deploy\watchdog.ps1` a cada minuto (comando no cabeçalho do arquivo).
10b. **Painel executivo na tela:** com o usuário que fica logado na máquina, agendar `deploy\open-painel.ps1` no logon
    (comando no cabeçalho): abre o Edge em tela cheia em `/painel`, espera o gateway subir, esconde o cursor. Monitor/TV
    sem repouso. O painel mantém a última leitura se o serviço cair e avisa "Sem conexão com o serviço".
11. **Verificar:** `curl http://localhost:8080/health` → ok; `/ready` → `{"ready":true}`; abrir `http://<host>:8080/admin`
    e entrar com a `ADMIN_KEY`. O painel executivo (`/painel`) usa a `PANEL_KEY`.

## HTTPS (decisão com a TI)
- **A)** Certificado interno + uvicorn direto: definir `GATEWAY_SSL_CERT` e `GATEWAY_SSL_KEY` (variáveis de sistema) —
  o `run-gateway.ps1` passa `--ssl-certfile/--ssl-keyfile`.
- **B)** Proxy reverso com TLS (IIS/nginx/Caddy) na frente do gateway em `127.0.0.1:8080`.
- Em ambos: hostname interno que as estações resolvem + certificado de uma CA que elas confiam.
- Se a TI vetar HTTPS interno: alternativa é túnel/IP público (decisão de rede pendente; ver CONFIGURACAO seção 5).

## O que cada peça garante
| Peça | Cobre |
|---|---|
| NSSM `AppExit Default Restart` + `AppRestartDelay` | Processo caiu → sobe de novo em 5 s |
| `watchdog.ps1` | Processo vivo mas travado (`/health` sem resposta 3x) → reinicia o serviço e avisa |
| `TokenManager` (camadas silent → navegador) | Token expirando → renova sozinho antes de vencer |
| Estado `DEGRADED` + backoff + alerta | SSO exigiu humano (MFA): gateway segue de pé, responde 503 + Retry-After, avisa no webhook |
| Circuit breaker | Databricks fora → falha rápido em vez de empilhar requisições; volta sozinho |
| `/ready` | Balanceador/monitor sabe se pode mandar tráfego |
| `harden-windows.ps1` | Sem hibernar, relógio sincronizado, update fora do horário |

## Quando o alerta "autenticação DEGRADED" chega
1. Entrar na máquina com a conta do serviço. 2. `python -m scripts.login` (navegador visível) e concluir SSO/MFA.
3. Em ~30 s–15 min (backoff) o gateway volta a OK sozinho; ver `/admin`. Se a causa for sessão SSO que expira
   sempre (ex.: a cada 8 h com MFA), a renovação 100% autônoma **não é possível** — conversar com a TI sobre sessão
   mais longa/dispositivo confiável para esta conta. O piloto de 7 dias existe para descobrir isso.

## Guia de operação: quando algo falha
Todo erro que o operador vê traz um **[código]** (= `X-Request-Id`). Ele localiza o atendimento em `/admin` → eventos
(`rid`) e nos logs. Matriz rápida:

| O que o operador/painel mostra | Causa provável | O que fazer |
|---|---|---|
| 401 "token expirado/assinatura inválida" | relógio da máquina ou do PC do operador errado; `TOKEN_SECRET` diferente do Script Properties | `scripts.doctor` (relógio); conferir o segredo nos dois lados |
| 403 "app desconhecido" | `APP_ID` do Apps Script ≠ `app_id` do YAML, ou app `enabled: false` | alinhar nomes; reiniciar o serviço após mudar YAML |
| 422 "campo ..." | o formulário manda campo que não está em `fields:` do YAML | declarar o campo no YAML |
| 429 "muitos atendimentos" | teto de concorrência/limite por minuto | conferir `MAX_CONCURRENCY`/`max_concurrent`/`rate_limit_per_min`; o `Retry-After` já orienta a tela |
| 503 "autenticação indisponível" + alerta DEGRADED | sessão SSO expirou/MFA exigido | entrar na máquina → `python -m scripts.login` |
| 503 "circuito aberto" / 502 "agente indisponível" | Databricks/endpoint fora ou lento; warehouse parado (cold start) | ver status do endpoint e do warehouse; volta sozinho (circuito testa a cada 30 s) |
| Painel "Sem conexão com o serviço" | gateway parado | `Get-Service DatabricksGateway`; watchdog deve reiniciar sozinho; ver `logs\gateway.err.log` |
| Alerta "X de Y atendimentos falharam" | falha em massa (>=30% em 5 min) | abrir `/admin`, olhar `reason` dos últimos eventos |

**Rotação do segredo (`TOKEN_SECRET`)** sem derrubar ninguém: (1) gerar o novo; (2) no gateway: `TOKEN_SECRET_PREVIOUS=<antigo>`
e `TOKEN_SECRET=<novo>`, reiniciar; (3) atualizar `GATEWAY_TOKEN_SECRET` no Script Properties; (4) após alguns minutos
(os tokens duram 5 min) remover `TOKEN_SECRET_PREVIOUS`. Conversas abertas recomeçam (a chave de conversa usa o segredo atual).

**Backup/retenção:** `data\history.sqlite` (histórico do painel) é o único estado local; copiar de vez em quando se a
diretoria quiser preservar o histórico. Perdê-lo não afeta o atendimento. O histórico de conversa fica no Databricks
(tabela `conversas`, ver `ARQUITETURA_AGENTE.md`) com job de retenção.

**Proteções que já existem (não precisam de ação):** idempotência por `request_id` (duplo clique/retry não duplica),
teto de simultâneos (global e por app) com `Retry-After`, recusa de corpo grande (413), circuito do Databricks,
falha do histórico/painel nunca derruba o atendimento, vigia de taxa de erro com alerta, `/ready` para monitor.

## Segurança operacional
- O cache de token do SDK (`%USERPROFILE%\.config\databricks-sdk-py\oauth\`) e `browser-profile\` equivalem a uma
  credencial: ACL só para a conta do serviço e administradores; incluir no processo de offboarding.
- `TOKEN_SECRET`, `ADMIN_KEY` e `PANEL_KEY` longos, aleatórios e **distintos**; a tela fixa usa só a `PANEL_KEY` (a
  `ADMIN_KEY` nunca vai em URL). O painel não mostra conteúdo de clientes, mas restrinja a rede (firewall só para as
  estações que precisam). Falhas repetidas de chave bloqueiam o cliente por alguns minutos (429).
- Logs em `logs\` (rotativos, 10 MB) não contêm CPF, token nem texto do operador.
- Uma instância apenas (limite por app e eventos ficam em memória).
