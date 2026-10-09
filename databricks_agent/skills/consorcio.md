---
name: consorcio
title: Tickets e protocolos
description: Atendimento sobre consórcio - tickets e protocolos do cliente, andamento de solicitações e prazos de retorno.
domain: consorcio
topic: tickets
tags: [ticket, protocolo, solicitacao, chamado, andamento]
questions:
  - Quais tickets esse cliente tem?
  - Qual o andamento do protocolo?
  - Quando será o retorno do meu chamado?
not_when: O pedido é sobre regras de lance, FGTS, boleto ou contemplação (use a skill do assunto específico).
triggers: [consorcio, cota, ticket, protocolo, assembleia, contemplacao, lance, boleto]
queries:
  tickets_do_cliente:
    description: Últimos tickets de atendimento do cliente (EXEMPLO - ajustar tabela/colunas ao ambiente real)
    answers: Tickets e protocolos do cliente com assunto, status, data de abertura e previsão de retorno.
    tables: [catalogo_exemplo.atendimento.tickets]
    sql: >-
      SELECT protocolo, assunto, status, aberto_em, previsao_retorno
      FROM catalogo_exemplo.atendimento.tickets
      WHERE cpf = :cpf
      ORDER BY aberto_em DESC
      LIMIT 20
    params: [cpf]
    max_rows: 20
---
# Skill: consórcio (tickets e protocolos)

## Como responder {core}
1. Comece pela resposta direta à pergunta do operador (uma ou duas frases).
2. Se houver tickets em `<dados_consultados>`, liste os mais recentes em tópicos: protocolo, assunto, status e
   previsão de retorno. Destaque os que estão abertos.
3. Se o operador perguntou por um protocolo específico (campo `protocolo`), responda só sobre ele.
4. Termine com o próximo passo sugerido ao operador (ex.: "informe ao cliente que o retorno é até <data>").

## Regras de negócio (exemplo)
- Ticket "Aberto" ou "Em análise" ainda não tem solução: nunca diga ao cliente que está resolvido.
- Nunca prometa contemplação nem prazo que não esteja nos dados.
- Se não houver nenhum ticket, diga isso e sugira abrir um novo protocolo.

## Tom
Profissional e curto. Fale com o operador, não com o cliente final.
