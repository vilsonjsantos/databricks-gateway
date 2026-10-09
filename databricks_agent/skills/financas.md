---
name: financas
title: Parcelas e finanças
description: Dúvidas financeiras do cliente - parcelas em aberto, juros, saldo devedor, quitação antecipada, taxas e multas.
domain: financas
topic: parcelas
tags: [parcela, juros, saldo devedor, quitacao antecipada, taxa, multa, atraso, divida]
questions:
  - Quantas parcelas o cliente tem em aberto?
  - Qual o saldo devedor?
  - Quanto fica a multa por atraso?
not_when: O pedido é sobre lance ou FGTS no consórcio (use as skills de consórcio).
triggers: [financas, parcela, juros, saldo, quitacao, taxa, multa, financeiro, divida]
queries:
  parcelas_do_cliente:
    description: Parcelas em aberto do cliente (EXEMPLO - ajustar tabela/colunas ao ambiente real)
    answers: Parcelas em aberto do cliente com número, vencimento, valor e situação.
    tables: [catalogo_exemplo.financeiro.parcelas]
    sql: >-
      SELECT numero_parcela, vencimento, valor, status
      FROM catalogo_exemplo.financeiro.parcelas
      WHERE cpf = :cpf AND status <> 'PAGA'
      ORDER BY vencimento
      LIMIT 24
    params: [cpf]
    max_rows: 24
---
# Skill: finanças

## Como responder {core}
1. Dê o número que o operador pediu primeiro (ex.: "3 parcelas em aberto, a próxima vence em 10/11").
2. Mostre valores sempre em reais, com duas casas decimais e a data de vencimento no formato dd/mm/aaaa.
3. Se o cliente perguntou sobre quitação antecipada e não há cálculo nos dados, **não calcule de cabeça**:
   oriente o operador a usar a simulação oficial.

## Regras de negócio (exemplo)
- Parcela vencida pode ter juros e multa; só cite o valor atualizado se ele estiver nos dados.
- Nunca arredonde valores em favor de ninguém.
- Não dê aconselhamento de investimento.
