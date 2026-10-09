# Segurança

## Como reportar
Abra um *security advisory* privado neste repositório (aba **Security → Report a vulnerability**) ou contate o mantenedor
diretamente. Não publique detalhes de uma falha em issues públicas.

## Política de segredos (regra inegociável)
- Nenhum segredo entra no repositório: `.env`, tokens, chaves, bancos locais e perfis de navegador estão no `.gitignore`.
- `python -m scripts.check_leaks --git` varre exatamente o que seria publicado (padrões de chave + os valores reais dos
  segredos locais, sem exibi-los). Rode antes de todo `push`. Se um segredo vazar: **revogue e gere outro primeiro**,
  depois limpe o histórico (apagar o commit não adianta).
- Exemplos e testes usam valores obviamente falsos; linhas de demonstração deliberadas levam o marcador `nosec-secret`.

## Modelo de proteção
| Superfície | Proteção |
|---|---|
| `POST /v1/run` | Token HMAC-SHA256 de ~5 min emitido pelo front (app + usuário no token, nunca no corpo); rotação de segredo sem parada; validação estrita por app (campo desconhecido é recusado); limites de corpo (inclusive em partes), de requisições e de simultâneos; idempotência por `request_id`. |
| Agente / Databricks | O LLM nunca escreve nem lê SQL: queries vêm das skills, só `SELECT`/`WITH`, parametrizadas (`:param`); erro de consulta nunca vaza SQL/valores; entrada do operador e dados consultados são tratados como dado (blocos delimitados). |
| Conversa | Chave HMAC amarrada a app + usuário: conhecer o id não abre a conversa de outro operador. Memória dentro do Databricks, não no gateway. |
| Painéis | `ADMIN_KEY` (técnico, vê e-mails) e `PANEL_KEY` (executivo, só agregados) distintas; comparação em tempo constante; bloqueio por tentativa e erro (429); `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, CSP e `X-Frame-Options` nas páginas; `/docs` e OpenAPI desligados. |
| Produção | Com `AUTH_MODE=browser` o serviço **recusa subir** com segredo curto/de exemplo. Login no Databricks só por navegador (OAuth); o cache de refresh token é credencial: ACL só da conta do serviço. |
| Dados | Eventos, logs e histórico do painel guardam só metadados (e-mail do operador em hash no histórico); CPF mascarado; nada de texto digitado ou resposta em disco. |

## Limites conhecidos (seja honesto ao avaliar)
- O token HMAC não impede *replay* dentro dos 5 minutos de vida (mitigação: vida curta + HTTPS).
- O limite de simultâneos, o bloqueio de tentativas e a idempotência vivem em memória de **um** processo.
- Cabeçalhos de CSP usam `'unsafe-inline'` (as páginas têm script/estilo embutidos).
- Sem HTTPS interno com certificado confiável o front (HTTPS) não consegue chamar o gateway: é requisito de implantação.
