"""Corpus SINTÉTICO de ~22 skills parecidas + casos de avaliação, para testar o roteamento em escala.
Não são regras reais: servem para medir a mecânica (busca, escolha, desambiguação). A avaliação que vale é a do
conjunto de perguntas REAIS da empresa (ver docs/ARQUITETURA_AGENTE.md)."""
import json
from pathlib import Path

# name, domain, topic, descrição, perguntas, tags, not_when, exclusive_with
SKILLS = [
    ("consorcio-lances", "consorcio", "lances", "Regras e andamento de lances: tipos, como ofertar, prazo, valor mínimo, resultado.",
     ["Como dou um lance livre?", "Qual o valor mínimo do lance?", "Meu lance venceu a assembleia?"],
     ["lance livre", "lance fixo", "lance embutido", "ofertar lance"],
     "Se envolver usar FGTS no lance, use consorcio-lance-fgts.", []),
    ("consorcio-lance-fgts", "consorcio", "fgts", "Uso do FGTS no consórcio imobiliário: pagar lance, parcelas e amortizar com FGTS.",
     ["Posso usar o FGTS para dar um lance?", "Quais documentos para usar o FGTS?"],
     ["fgts", "saldo do fgts", "lance com fgts"], "Lance sem FGTS é consorcio-lances.", []),
    ("consorcio-vts", "consorcio", "vts", "Produto de consórcio VTS: condições e grupos específicos da linha VTS.",
     ["Quais as condições do grupo VTS?", "Meu consórcio é da linha VTS?"], ["vts", "linha vts"],
     "Dúvida geral de lance/boleto sem depender do produto não é VTS.", []),
    ("consorcio-contemplacao", "consorcio", "contemplacao", "Contemplação: sorteio, status de contemplação e o que fazer ao ser contemplado.",
     ["Fui sorteado?", "Qual o status da contemplação?", "Como funciona o sorteio?"], ["contemplado", "sorteio", "contemplacao"],
     "Liberar o crédito e documentos pós-contemplação é consorcio-credito-liberacao.", []),
    ("consorcio-assembleia", "consorcio", "assembleia", "Assembleias: datas, convocação e resultado das assembleias do grupo.",
     ["Quando é a próxima assembleia?", "Onde vejo o resultado da assembleia?"], ["assembleia", "data da assembleia", "resultado"],
     "Resultado do lance específico do cliente é consorcio-lances.", []),
    ("consorcio-boletos", "consorcio", "boletos", "Boletos do consórcio: segunda via, vencimento, boleto em atraso e formas de pagamento.",
     ["Preciso da segunda via do boleto", "Qual o vencimento da parcela?", "Meu boleto está atrasado"],
     ["boleto", "segunda via", "vencimento", "pagamento"], "Multa e juros por atraso de financiamento é financas-juros-multa.", []),
    ("consorcio-cancelamento", "consorcio", "cancelamento", "Desistência e cancelamento da cota: devolução de valores, prazos e multa rescisória.",
     ["Quero desistir do consórcio", "Recebo o dinheiro de volta se cancelar?"], ["desistir", "cancelar", "devolucao", "rescisao"],
     "Transferir a cota para outra pessoa é consorcio-transferencia.", []),
    ("consorcio-transferencia", "consorcio", "transferencia", "Transferência de cota e cessão de titularidade para outra pessoa.",
     ["Quero passar minha cota para outra pessoa", "Como transfiro a titularidade?"], ["transferir", "cessao", "titularidade", "ceder cota"],
     "Cancelar/desistir é consorcio-cancelamento.", []),
    ("consorcio-seguro", "consorcio", "seguro", "Seguro prestamista e seguro de vida do consórcio: cobertura, valor e cancelamento do seguro.",
     ["O seguro prestamista é obrigatório?", "O que o seguro cobre?"], ["seguro", "prestamista", "cobertura"], "", []),
    ("consorcio-taxas", "consorcio", "taxas", "Taxa de administração, fundo de reserva e demais custos do consórcio.",
     ["O que é taxa de administração?", "Para que serve o fundo de reserva?"], ["taxa de administracao", "fundo de reserva", "custos"],
     "Juros e multa por atraso são outro assunto (financas-juros-multa).", []),
    ("consorcio-credito-liberacao", "consorcio", "credito", "Liberação do crédito após contemplação: documentos, avaliação do bem e prazos.",
     ["Fui contemplado, como libero o crédito?", "Quais documentos para liberar a carta de crédito?"],
     ["liberar credito", "carta de credito", "avaliacao do bem", "documentos"], "Saber se foi contemplado é consorcio-contemplacao.", []),
    ("consorcio-tickets", "consorcio", "tickets", "Tickets e protocolos do cliente: andamento e previsão de retorno.",
     ["Qual o andamento do protocolo?", "Quais tickets o cliente tem?"], ["ticket", "protocolo", "chamado", "andamento"],
     "Reclamação formal é ouvidoria.", []),
    ("consorcio-reajuste", "consorcio", "reajuste", "Reajuste do crédito e das parcelas por índice (INCC/IPCA): por que mudou e quanto.",
     ["Por que minha parcela aumentou?", "O crédito foi reajustado?"], ["reajuste", "incc", "ipca", "parcela aumentou"], "", []),
    ("consorcio-quitacao", "consorcio", "quitacao", "Quitação antecipada do consórcio: saldo, desconto e como quitar.",
     ["Quero quitar meu consórcio antecipadamente", "Qual o saldo para quitar o consórcio?"], ["quitar consorcio", "quitacao antecipada", "saldo para quitar"],
     "Quitar financiamento fora do consórcio é financas-quitacao-financiamento.", ["financas-quitacao-financiamento"]),
    ("financas-parcelas", "financas", "parcelas", "Parcelas em aberto, valores, vencimentos e situação financeira do cliente.",
     ["Quantas parcelas o cliente tem em aberto?", "Qual o saldo devedor?"], ["parcelas em aberto", "saldo devedor", "situacao financeira"], "", []),
    ("financas-quitacao-financiamento", "financas", "quitacao", "Quitação de financiamento e empréstimos fora do consórcio.",
     ["Quero quitar meu financiamento", "Qual o saldo para quitar o empréstimo?"], ["quitar financiamento", "emprestimo", "financiamento"],
     "Quitar o consórcio é consorcio-quitacao.", ["consorcio-quitacao"]),
    ("financas-juros-multa", "financas", "juros", "Juros, multa e encargos por atraso de pagamento.",
     ["Quanto fica a multa por atraso?", "Como é calculado o juro?"], ["juros", "multa", "encargos", "atraso"], "", []),
    ("cadastro-dados", "cadastro", "dados", "Atualização de dados cadastrais: endereço, telefone, e-mail e dados bancários.",
     ["Atualizar telefone e endereço do cliente", "Como troco o e-mail cadastrado?"], ["telefone", "endereco", "email", "cadastro"], "", []),
    ("ouvidoria", "ouvidoria", "reclamacao", "Reclamações formais e ouvidoria: registrar, prazo de resposta e escalonamento.",
     ["Cliente quer fazer uma reclamação formal", "Como acionar a ouvidoria?"], ["reclamacao", "ouvidoria", "insatisfeito", "escalar"], "", []),
    ("cobranca-acordo", "cobranca", "acordo", "Negociação de dívida em atraso: acordo, parcelamento e desconto para regularizar.",
     ["Cliente quer negociar dívida em atraso", "Tem desconto para pagar à vista o atraso?"], ["negociar", "acordo", "regularizar", "divida em atraso"], "", []),
    ("geral", "geral", "geral", "Skill padrão para perguntas gerais que não se encaixam em nenhuma outra.", [], [], "", []),
]

# (prompt, fields, esperado)
CASES = [
    ("Posso usar o FGTS para fazer um lance no consórcio?", {"assunto": "consorcio"}, ["consorcio-lance-fgts"]),
    ("Como dou um lance livre?", {"assunto": "consorcio"}, ["consorcio-lances"]),
    ("Meu lance venceu a assembleia de ontem?", {"assunto": "consorcio"}, ["consorcio-lances"]),
    ("Quando é a próxima assembleia?", {"assunto": "consorcio"}, ["consorcio-assembleia"]),
    ("Fui contemplado, como libero o crédito e quais documentos?", {"assunto": "consorcio"}, ["consorcio-credito-liberacao"]),
    ("Foi sorteado? Qual o status da contemplação?", {"assunto": "consorcio"}, ["consorcio-contemplacao"]),
    ("Preciso da segunda via do boleto", {"assunto": "consorcio"}, ["consorcio-boletos"]),
    ("Quero desistir do consórcio, recebo o dinheiro de volta?", {"assunto": "consorcio"}, ["consorcio-cancelamento"]),
    ("Quero passar minha cota para outra pessoa", {"assunto": "consorcio"}, ["consorcio-transferencia"]),
    ("O seguro prestamista é obrigatório?", {"assunto": "consorcio"}, ["consorcio-seguro"]),
    ("O que é fundo de reserva e taxa de administração?", {"assunto": "consorcio"}, ["consorcio-taxas"]),
    ("Minha parcela aumentou, o crédito foi reajustado?", {"assunto": "consorcio"}, ["consorcio-reajuste"]),
    ("Quero quitar meu consórcio antecipadamente", {"assunto": "consorcio"}, ["consorcio-quitacao"]),
    ("Quais as condições do grupo VTS?", {"assunto": "consorcio"}, ["consorcio-vts"]),
    ("Qual o andamento do protocolo?", {"assunto": "consorcio", "protocolo": "2024/123"}, ["consorcio-tickets"]),
    ("Quantas parcelas em aberto o cliente tem?", {"assunto": "financas"}, ["financas-parcelas"]),
    ("Quanto fica a multa por atraso?", {"assunto": "financas"}, ["financas-juros-multa"]),
    ("Quero quitar meu financiamento", {"assunto": "financas"}, ["financas-quitacao-financiamento"]),
    ("Atualizar telefone e endereço do cliente", {}, ["cadastro-dados"]),
    ("Cliente quer fazer uma reclamação formal", {}, ["ouvidoria"]),
    ("Cliente quer negociar dívida em atraso", {}, ["cobranca-acordo"]),
]


# Casos DIFÍCEIS: linguagem de balcão, sinônimos, sem repetir as palavras das fichas. Medem o que a busca por
# palavras NÃO resolve sozinha (aí entram sinônimos em `tags`, embeddings multilíngues e a escolha pelo LLM).
HARD_CASES = [
    ("O cliente quer usar o dinheiro do fundo de garantia pra cobrir uma oferta maior", {"assunto": "consorcio"}, ["consorcio-lance-fgts"]),
    ("Ele disse que não vai mais pagar e quer o dinheiro de volta", {"assunto": "consorcio"}, ["consorcio-cancelamento"]),
    ("A prestação veio mais cara este mês, por quê?", {"assunto": "consorcio"}, ["consorcio-reajuste"]),
    ("Qual o dia que rola a reunião do grupo?", {"assunto": "consorcio"}, ["consorcio-assembleia"]),
    ("Preciso reenviar o código de barras pro cliente", {"assunto": "consorcio"}, ["consorcio-boletos"]),
    ("Ele quer pagar tudo de uma vez e acabar com o consórcio", {"assunto": "consorcio"}, ["consorcio-quitacao"]),
    ("Cobraram um valor a mais por eu ter atrasado", {"assunto": "financas"}, ["financas-juros-multa"]),
    ("Não estou satisfeito com o atendimento, quero falar com alguém de cima", {}, ["ouvidoria"]),
    ("A minha esposa vai assumir o consórcio no lugar do cliente", {"assunto": "consorcio"}, ["consorcio-transferencia"]),
    ("Meu nome saiu na lista dos que ganharam a carta?", {"assunto": "consorcio"}, ["consorcio-contemplacao"]),
    ("Já pagamos metade, o que mais entra na conta além da parcela?", {"assunto": "consorcio"}, ["consorcio-taxas"]),
    ("Como cobrir a oferta com o saldo da conta do trabalhador?", {"assunto": "consorcio"}, ["consorcio-lance-fgts"]),
]


def write_skills(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    q = json.dumps  # JSON é YAML válido: aspas protegem ':' e acentos nos valores
    for name, domain, topic, desc, questions, tags, not_when, excl in SKILLS:
        qs = "".join(f"  - {q(x, ensure_ascii=False)}\n" for x in questions)
        fm = (f"---\nname: {name}\ndescription: {q(desc, ensure_ascii=False)}\ndomain: {domain}\ntopic: {topic}\n"
              f"tags: {q(tags, ensure_ascii=False)}\n" + (f"questions:\n{qs}" if qs else "") +
              (f"not_when: {q(not_when, ensure_ascii=False)}\n" if not_when else "") +
              (f"exclusive_with: {q(excl)}\n" if excl else "") + f"---\nCorpo da skill {name} (sintético).\n")
        (directory / f"{name}.md").write_text(fm, encoding="utf-8")
    (directory / "_routing.yaml").write_text(
        "field_domains:\n  assunto:\n    consorcio: consorcio\n    financas: financas\ncandidates_k: 6\n", encoding="utf-8")
