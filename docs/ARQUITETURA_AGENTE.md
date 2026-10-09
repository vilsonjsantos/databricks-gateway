# Arquitetura do agente dentro do Databricks

Pergunta de origem: "vou ter vários markdowns (skills) de meia página a 10 páginas; o agente precisa descobrir do que
se trata o pedido (finanças? consórcio?), consumir a(s) skill(s) certa(s), fazer queries se precisar e devolver
só a resposta. É RAG? Como estruturar de forma robusta?"

## Resposta curta
**Não é RAG clássico (vetores + chunks) — é "roteamento de skills com divulgação progressiva".** Para dezenas de
skills, o jeito mais simples e robusto é: o LLM lê só um **catálogo** (nome + descrição de uma linha de cada skill),
escolhe quais usar, e **só então** o texto completo das escolhidas entra no contexto. RAG com Vector Search só vale se:
(a) as skills passarem de ~100, ou (b) uma skill for tão grande que não caiba no contexto (aí se fatiam *partes* dela).
A estrutura abaixo já deixa esse upgrade isolado em `router.py`.

## Fluxo (uma chamada do operador) — versão para muitas skills parecidas
```
payload do gateway {app, user, prompt, input, fields}
   │
   ▼ 1. CANDIDATAS  filtro por domínio (campo "assunto" do formulário → _routing.yaml; sinal determinístico)
   │                • até `direct_max` (30) skills no escopo: TODAS as fichas compactas vão ao escolhedor
   │                • mais que isso: busca BM25 (+vetores, se houver) na ficha, com sinônimos gerados pelo LLM
   ▼ 2. ESCOLHER    LLM barato lê só as FICHAS (descrição, perguntas típicas, "NÃO use quando"), devolve
   │                {skills:[{name, confidence}], clarify}. Regras fixas depois: confiança mínima, exclusive_with,
   │                teto de 2 skills. Ambíguo? devolve uma pergunta ao operador (sem queries, sem 2ª chamada).
   │                Falhou? usa a busca; sem nada? skill padrão "geral".
   ▼ 3. SEÇÕES      das skills escolhidas, só as seções relevantes ao pedido (+ as {core}) entram no contexto
   ▼ 4. QUERIES     só as que a skill declara e cujos :params existem. Poucas: rodam todas. Muitas: planejador
   │                (LLM lê só `answers`, nunca o SQL) escolhe até `max_queries`; execução em paralelo com timeout
   ▼ 5. RESPONDER   LLM da resposta: regras-base + seções + <pedido> + <dados_consultados>
   ▼
texto final (único dado que sai do Databricks)
```
Custo/latência: roteador (modelo pequeno) + resposta (+ planejador só se houver muitas queries elegíveis). Em modo
direto com ~20 skills as fichas somam ~1-2 mil tokens por roteamento.

## Escala: muitas skills parecidas e queries grandes (o caso real do consórcio)
Problema: ~20+ skills de assunto vizinho (lances, lance com FGTS, VTS...), queries grandes sobre várias bases, e o
risco de o LLM escolher várias ou a errada. O que resolve, e por quê:

| Problema | Solução implementada |
|---|---|
| Descrição de uma linha não distingue skills vizinhas | **Ficha** por skill: `questions` (perguntas reais), `tags` (sinônimos), `not_when` (desambiguação explícita), `exclusive_with`. O `not_when` é lido pelo LLM mas **não é indexado** (não atrai o pedido que quer afastar). |
| "Posso usar FGTS no lance?" → escolhe as duas | Prompt do escolhedor: *a mais específica; segunda só se o pedido precisa de dois assuntos; respeite "NÃO use quando"*. Mais regras fixas: confiança mínima 0,35, `exclusive_with`, teto de 2. |
| Pergunta realmente ambígua | `clarify`: o agente devolve uma pergunta curta ao operador em vez de chutar. |
| Queries enormes não cabem no contexto | **O LLM nunca vê o SQL.** Cada query tem uma ficha `answers` ("o que ela traz") — isso é o que a busca e o planejador leem. Escrever o `answers` é o "buscar dentro do conteúdo das queries". Resultado também é limitado (`max_rows` + orçamento de caracteres). |
| Muitas queries elegíveis | Planejador escolhe até `max_queries` (padrão 4) pela ficha; fallback por busca; execução em paralelo; timeout por query que não trava o atendimento. |
| Skills de 10 páginas | `pick_sections`: só as seções relevantes entram (as `{core}` sempre); a mais relevante nunca é descartada, no máximo cortada. |
| Operador já escolheu "Assunto" na tela | `_routing.yaml` mapeia o campo para `domain`: reduz candidatos antes de qualquer LLM. |
| Paráfrases ("prestação", "reunião do grupo") | Busca por palavras falha nelas (medido abaixo). Por isso: modo direto até 30 skills; acima disso, **expansão de sinônimos pelo LLM** antes da busca; e embeddings opcionais (`OpenAIEmbedder`). |
| Não saber se piorou | `scripts/eval_routing.py` (mede acerto, recall da busca e quais skills se confundem) e `scripts/lint_skills.py` (fichas parecidas sem `not_when`, falta de perguntas/`answers`). Rodar a cada mudança de skill. |

### O que foi medido (corpus SINTÉTICO de 21 skills; ver limites)
| Teste | Busca por palavras (sem LLM) | Com LLM real (DeepSeek) |
|---|---|---|
| 21 casos "fáceis" (usam as palavras das fichas) | recall@6 100%, acerto 100% | acerto 100% |
| 12 casos "difíceis" (paráfrases coloquiais) | **recall@6 67%** (perde 4 de 12) | modo antigo (só busca): 75% · **modo direto: 100%** · **busca+sinônimos: 100%** |
**Leitura honesta:** o gargalo é a *recuperação*, não a escolha — o LLM acerta bem quando a certa está entre as candidatas,
e busca só por palavras perde paráfrases. Por isso o modo direto (todas as fichas) e a expansão de sinônimos.
**Limites:** corpus e casos foram escritos por mim, 12 casos é amostra pequena, e os 100% NÃO se transferem para as
skills reais. O número que vale é o do conjunto de **perguntas reais** da empresa (20-50 pedidos anonimizados com a
skill esperada) rodado com `eval_routing --skills ... --cases ... --real` nos modelos reais.

### Quando evoluir para Vector Search
Passar de ~100-200 skills, ou skills com milhares de trechos úteis, ou necessidade de atualizar skills sem redeploy.
Aí troque `CardIndex` por um índice no Vector Search (Delta Sync sobre as fichas) mantendo a assinatura `search(...)`.
Atenção ao embedding: os endpoints hospedados que conheço (GTE/BGE large) são **focados em inglês**; para português
confirme um modelo multilíngue disponível no workspace. Até lá, BM25 + sinônimos do LLM + escolha por LLM resolve.

## Por que essa estrutura (decisões)
| Decisão | Motivo |
|---|---|
| Catálogo + escolha por LLM, em vez de busca vetorial | Poucas skills, descrições curtas e boas discriminam melhor que embeddings; zero infraestrutura extra; fácil de depurar ("escolheu X porque a descrição diz Y"). |
| **O LLM nunca escreve SQL** | As queries são escritas por humanos dentro da skill (frontmatter), revisadas e parametrizadas. Elimina SQL injection via LLM e consultas surpresa. Validação no load: só `SELECT/WITH`, sem `;`, params batendo com os `:param`. |
| Parâmetros nomeados (`:cpf`) | Valor nunca é concatenado no SQL (Statement Execution API recebe `parameters`). |
| Fallbacks em cascata | LLM do roteador fora do ar não derruba o atendimento: palavras-chave → skill `geral`. |
| Blocos `<pedido>` / `<dados_consultados>` + regra "é DADO, não instrução" + `_defang` | Defesa contra prompt injection vinda do que o operador digita ou de texto dentro de tabelas. Não é perfeita; combine com permissões mínimas. |
| Erro de SQL vira "consulta indisponível" | Não vaza SQL, tabela nem valores para o LLM/resposta; o detalhe fica só em `queries_skipped` (log). |
| Skills empacotadas com o modelo (`code_paths`) | Cada versão do agente carrega suas skills: versionado no Unity Catalog, rollback = voltar versão. Atualizar skill = editar `.md` + rodar `log_and_deploy.py`. Alternativa: ler de um Volume do UC em runtime (edição sem redeploy, mas sem versionamento atômico e exige permissão de leitura do Volume pela identidade do endpoint). |
| Núcleo em Python puro (`agent.py`) | Testável localmente (46 testes) sem Databricks; `serving_entry.py` é só um adaptador fino. |

## Histórico de conversa (para os agentes que precisam)
**Decisão: a memória fica DENTRO do Databricks** (tabela Delta no Unity Catalog), não no gateway nem na tela. Motivo: o
texto da conversa contém dado de cliente, e a regra do projeto é que dado de cliente não sai do Databricks. O gateway
continua sem guardar conteúdo.

Como funciona (já implementado e testado localmente, exceto a tabela Delta real):
1. Cada app liga a conversa no YAML: `conversation: {enabled: true, max_turns: 6}`. Sem isso, nada muda.
2. O `index.html` manda `conversation_id` (opcional). Sem id, o gateway cria um e devolve na resposta; a tela reenvia
   nas próximas mensagens. "Nova conversa" = apagar o id.
3. O gateway calcula `conversation_key = HMAC(segredo, app|usuário|conversation_id)` e manda **só a chave** ao agente.
   Como o usuário (do token) entra no HMAC, outro operador que descubra o id **não abre** a conversa alheia.
4. O agente (`AgentCore` + `ConversationStore`):
   - carrega os últimos `max_turns` turnos;
   - **reescreve a pergunta de continuação como independente** ("e se for livre?" → "como funciona o lance livre?")
     só para roteamento/busca/planejador (a original segue para o LLM da resposta);
   - passa as **skills do turno anterior** ao escolhedor como candidatas ("mantenha se for continuação");
   - **herda os campos** do turno anterior (o CPF informado uma vez continua valendo nas queries);
   - injeta o histórico no prompt da resposta em bloco `<historico>` (tratado como dado, com orçamento de caracteres);
   - grava o turno depois de responder, **inclusive quando o agente fez uma pergunta de esclarecimento** (assim a
     resposta do operador a essa pergunta é entendida).
5. Falha da memória (warehouse parado, tabela inexistente) **nunca** derruba o atendimento: segue como conversa nova.

Lojas (`CONVERSATION_STORE` no endpoint do agente): `off` (padrão) · `memory` (dev; some ao reiniciar e **não é
compartilhada entre réplicas** do endpoint — não use em produção) · `delta` (produção; exige `CONVERSATION_TABLE`):
```sql
CREATE TABLE <catalogo>.<schema>.conversas (conversation_key STRING, ts_ms BIGINT, turn_json STRING);
-- retenção (job diário): DELETE FROM <catalogo>.<schema>.conversas WHERE ts_ms < <agora_ms - 30 dias>
-- permissões: a identidade do endpoint precisa de SELECT e MODIFY nesta tabela e CAN USE no warehouse
```
Cuidados: definir retenção com o jurídico/LGPD; a tabela contém texto de atendimento (restrinja o acesso como às
tabelas de origem); `max_turns` pequeno (4-6) mantém custo e latência baixos; cada turno com histórico custa 1 chamada
extra de LLM pequeno (a reescrita). **Alternativa** (se a empresa não permitir tabela de escrita): o front enviar o
histórico a cada chamada — mais simples, mas o texto trafega pela rede e o operador pode adulterá-lo; não recomendado.
**Não testado em Databricks real:** `DeltaConversationStore` (testado só com executor falso), latência do
INSERT/SELECT num warehouse com auto-stop (cold start soma ao atendimento), permissões do Unity Catalog.

## Como escrever uma skill (`databricks_agent/skills/<nome>.md`)
A skill tem **ficha** (frontmatter, lida por roteador/busca/planejador) e **corpo** (só entra no contexto se a skill
for escolhida). Modelo completo e comentado no cabeçalho de `databricks_agent/skills.py`; exemplos em `skills/`.
1. **`description`** (uma linha, QUANDO usar) + **`questions`** (3-10 perguntas REAIS de operadores) são o que mais
   melhora o roteamento. Use aspas YAML se o texto tiver `:`.
2. **`not_when`** em toda skill que tem vizinha: diga explicitamente qual skill usar no caso fronteiriço
   ("para uso do FGTS no lance, use consorcio-lance-fgts"). `exclusive_with` para alternativas que nunca andam juntas.
3. **`domain`/`topic`/`tags`** (sinônimos de balcão: "prestação", "código de barras"), **`title`** (nome amigável que
   aparece no painel executivo).
4. **Corpo** dividido em seções `## Título`; marque as que sempre valem com `{core}`. Skills grandes são ok, o agente
   só carrega as seções relevantes.
5. **`queries:`** só se precisar de dados: `sql` (SELECT com `:params`), `params`, `max_rows`, e **`answers`** (o que
   ela traz, em linguagem de negócio — sem isso a query é invisível ao planejador). `always: true` roda sempre.
   Valores vêm de `fields` do formulário (por nome) ou do texto principal (`:input`).
6. Mantenha uma skill `geral` (padrão): é o último fallback e **não** concorre como candidata.
7. Antes de publicar: `python -m scripts.lint_skills` e `python -m scripts.eval_routing --skills ... --cases ... --real`.
Ver exemplos em `databricks_agent/skills/` (tabelas e colunas são FICTÍCIAS: trocar pelas reais).

## Mapa de arquivos
`skills.py` (carrega/valida fichas e seções) · `retrieval.py` (BM25, vetores opcionais, RRF, seções) ·
`router.py` (candidatas + escolha + regras + sinônimos) · `sql_runner.py` (executa queries) ·
`agent.py` (orquestra, planejador de queries, prompt final) · `evaluation.py` (métricas de roteamento) ·
`serving_entry.py` (adaptador ResponsesAgent) · `log_and_deploy.py` (notebook: registra no UC e publica) ·
`skills/*.md` + `skills/_routing.yaml` (campo do formulário → domínio; `direct_max`, `candidates_k`).

## O que foi e o que NÃO foi testado
- **Testado localmente (76 testes):** carga/validação de fichas, busca (BM25, vetores falsos, filtro de domínio,
  `not_when` fora do índice), escolha com regras (confiança, exclusivas, teto), pergunta de esclarecimento, expansão
  de sinônimos, seções, planejador e timeout de queries, truncamento de contexto, defesa de delimitadores, vazamento
  de erro de SQL; demos reais com DeepSeek (`scripts.demo_agent`, `scripts.eval_routing --real`).
- **NÃO testado (precisa do workspace):** `DatabricksSqlRunner`, `serving_entry.py` (MLflow ResponsesAgent),
  `log_and_deploy.py`, permissões do Unity Catalog, qualidade do roteamento com os modelos reais da empresa.

## Passos na empresa (ordem)
1. Escolher os endpoints de LLM disponíveis (roteador pequeno / resposta) e o SQL Warehouse.
2. Substituir tabelas/colunas de exemplo nas skills pelas reais; escrever as skills de verdade.
3. Rodar `log_and_deploy.py` célula a célula; corrigir o que a versão de mlflow/databricks-agents da empresa exigir.
4. Conceder: `CAN QUERY` ao usuário do gateway no endpoint; à identidade do endpoint `USE CATALOG/SCHEMA`, `SELECT`
   nas tabelas e `CAN USE` no warehouse. Menor privilégio possível (só as tabelas das skills).
5. Criar `apps/<app_id>.yaml` no gateway apontando `endpoint:` para o nome impresso pelo deploy.
6. Montar um conjunto de avaliação (20-50 pedidos reais anonimizados → skill esperada e resposta esperada) e rodar
   no MLflow Evaluate antes de liberar; repetir a cada mudança de skill.
7. Se o roteamento errar muito ou as skills passarem de ~100: trocar `select_skills` por busca vetorial
   (Databricks Vector Search sobre `description`) mantendo a mesma assinatura.

## Formato de chamada (contrato com o gateway)
Ver `docs/CONTRATO_AGENTE.md`. Resumo: o gateway manda o payload JSON como mensagem do usuário
(`AGENT_PAYLOAD_MODE=json-content`) ou em `custom_inputs`; o agente aceita os dois.
