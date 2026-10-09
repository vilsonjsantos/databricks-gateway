---
name: consorcio-lances
title: Lances
description: Regras e andamento de LANCES em consórcio - tipos de lance, como ofertar, prazos, valor mínimo, lance embutido e resultado da oferta.
domain: consorcio
topic: lances
tags: [lance livre, lance fixo, lance embutido, ofertar lance, oferta de lance, lance vencedor]
questions:
  - Como dou um lance no meu consórcio?
  - Qual o valor mínimo para um lance?
  - O que é lance embutido?
  - Meu lance foi o vencedor da assembleia?
  - Até quando posso ofertar o lance?
not_when: O pedido é sobre USAR O FGTS para pagar o lance (use consorcio-lance-fgts); sobre sorteio ou status de contemplação sem falar de lance (use consorcio-contemplacao).
queries:
  lances_do_cliente:
    answers: Lances ofertados pelo cliente nas últimas assembleias, valor ofertado, tipo de lance e resultado.
    tables: [catalogo_exemplo.consorcio.lances]
    sql: >-
      SELECT assembleia, tipo_lance, valor_ofertado, percentual, resultado
      FROM catalogo_exemplo.consorcio.lances
      WHERE cpf = :cpf
      ORDER BY assembleia DESC
      LIMIT 10
    params: [cpf]
    max_rows: 10
---
Atendimento sobre lances de consórcio. (EXEMPLO - substituir pelas regras reais.)

## Tipos de lance {core}
- **Livre**: o consorciado escolhe o valor; vence o maior percentual.
- **Fixo**: percentual definido pelo grupo; em empate, sorteio.
- **Embutido**: parte do crédito é usada para o lance (reduz o crédito liberado).

## Como ofertar
Oriente o operador a conferir o prazo da assembleia antes de orientar o cliente. Nunca garanta que o lance vence.

## Resultado de lances
Use os lances de `<dados_consultados>` para dizer se o último lance venceu. Se não houver dados, diga que não encontrou.

## Valor mínimo e prazos
O valor mínimo depende do grupo. Se o dado do grupo não estiver disponível, não invente percentual.
