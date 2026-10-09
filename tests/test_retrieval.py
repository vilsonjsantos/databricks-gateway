import json
import time

import pytest

from databricks_agent.agent import AgentCore
from databricks_agent.evaluation import evaluate_routing
from databricks_agent.retrieval import CardIndex, pick_sections, tokenize
from databricks_agent.router import select_skills
from databricks_agent.skills import SkillStore, parse_skill
from databricks_agent.sql_runner import FakeSqlRunner
from tests.corpus import CASES, write_skills


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    d = tmp_path_factory.mktemp("corpus")
    write_skills(d)
    return SkillStore.from_dir(d)


def payload(prompt, fields=None):
    return {"prompt": prompt, "input": "", "fields": fields or {}}


class Scripted:
    def __init__(self, router="{}", answer="ok"):
        self.router, self.answer, self.calls = router, answer, []

    def __call__(self, messages, role, max_tokens):
        self.calls.append((role, messages))
        return self.router if role == "router" else self.answer


def test_tokenize_ignora_acentos_plural_e_stopwords():
    assert tokenize("Os LANCES e a Contemplação") == ["lance", "contemplacao"]
    assert tokenize("contemplações") == ["contemplacao"]


def test_corpus_tem_21_skills_parecidas(store):
    assert len(store.skills) == 21


def test_busca_traz_a_esperada_entre_os_finalistas(store):
    idx = CardIndex(store)
    misses = []
    for prompt, fields, expect in CASES:
        names = [h.name for h in idx.search(prompt, 6, store.domains_for(fields) or None)]
        if not set(expect) <= set(names):
            misses.append((prompt, expect, names))
    assert not misses, misses


def test_busca_acerta_o_top1_na_maioria(store):
    idx = CardIndex(store)
    top1 = sum(1 for p, f, e in CASES
               if (hits := idx.search(p, 6, store.domains_for(f) or None)) and hits[0].name == e[0])
    assert top1 / len(CASES) >= 0.8, top1


def test_not_when_nao_e_indexado(store):
    # a frase "usar FGTS" aparece em not_when de consorcio-lances; não pode atrair o pedido para lá
    hits = CardIndex(store).search("quero informacao sobre fgts", 3)
    assert hits[0].name == "consorcio-lance-fgts"


def test_filtro_de_dominio_pelo_campo_do_formulario(store):
    hits = CardIndex(store).search("quitar", 6, store.domains_for({"assunto": "financas"}))
    assert hits and all(store.skills[h.name].domain == "financas" for h in hits)


def test_embeddings_sao_fundidos_sem_quebrar(store):
    def embed(texts):  # embedder falso determinístico: sacola de caracteres
        out = []
        for t in texts:
            v = [0.0] * 32
            for ch in t.lower():
                v[ord(ch) % 32] += 1
            out.append(v)
        return out

    idx = CardIndex(store, embedder=embed)
    assert idx.search("segunda via do boleto", 6)[0].name == "consorcio-boletos"


def test_llm_escolhe_so_uma_e_regras_valem(store):
    llm = Scripted('{"skills": [{"name": "consorcio-lance-fgts", "confidence": 0.9}, '
                   '{"name": "consorcio-lances", "confidence": 0.2}], "clarify": null}')
    r = select_skills(llm, store, payload("Posso usar o FGTS para dar um lance?", {"assunto": "consorcio"}))
    assert r.names == ["consorcio-lance-fgts"] and r.method == "llm"  # confiança baixa foi descartada


def test_exclusive_with_impede_as_duas(store):
    llm = Scripted('{"skills": [{"name": "consorcio-quitacao", "confidence": 0.8}, '
                   '{"name": "financas-quitacao-financiamento", "confidence": 0.7}]}')
    r = select_skills(llm, store, payload("quero quitar"))
    assert r.names == ["consorcio-quitacao"]


def test_ambiguo_devolve_pergunta_sem_chamar_resposta(store):
    llm = Scripted('{"skills": [], "clarify": "Você quer quitar o consórcio ou um financiamento?"}')
    res = AgentCore(llm, store, FakeSqlRunner()).handle(payload("quero quitar"))
    assert res.clarify and "consórcio ou um financiamento" in res.text
    assert [c[0] for c in llm.calls] == ["router"]  # nenhuma chamada de resposta nem de dados


def test_llm_falha_usa_a_busca(store):
    class Boom(Scripted):
        def __call__(self, *a):
            raise RuntimeError("fora do ar")

    r = select_skills(Boom(), store, payload("segunda via do boleto", {"assunto": "consorcio"}))
    assert r.names == ["consorcio-boletos"] and r.method == "retrieval"


def test_avaliacao_gera_relatorio_e_confusoes(store):
    class Top1(Scripted):  # simula um escolhedor que sempre confirma o 1º candidato listado
        def __call__(self, messages, role, max_tokens):
            first = messages[1]["content"].split("- ", 1)[1].split(" ", 1)[0]
            return json.dumps({"skills": [{"name": first, "confidence": 0.9}]})

    cases = [{"id": f"c{i}", "prompt": p, "fields": f, "expect": e} for i, (p, f, e) in enumerate(CASES)]
    rep = evaluate_routing(Top1(), store, cases)
    assert rep.n == len(CASES) and rep.recall_at_k >= 0.95 and rep.exact >= 0.8
    assert "recall@k" in rep.summary()


def test_catalogo_grande_usa_busca_com_sinonimos_do_llm(store):
    seen = []

    class Expander(Scripted):
        def __call__(self, messages, role, max_tokens):
            seen.append(messages[0]["content"][:30])
            if "termos e sinônimos" in messages[0]["content"]:
                return '{"terms": ["boleto", "segunda via", "pagamento"]}'
            return '{"skills": [{"name": "consorcio-boletos", "confidence": 0.9}]}'

    # direct_max=5 força o modo busca (21 skills > 5); "código de barras" só chega ao boleto pela expansão
    r = select_skills(Expander(), store, payload("Preciso reenviar o código de barras"), direct_max=5)
    assert r.names == ["consorcio-boletos"] and "consorcio-boletos" in r.candidates
    assert any("ajuda a encontrar" in s for s in seen)  # chamou o expansor


def test_skill_padrao_nao_e_candidata(store):
    r = select_skills(Scripted('{"skills": []}'), store, payload("bom dia"))
    assert "geral" not in r.candidates and r.names == ["geral"] and r.method == "default"


def test_titulo_amigavel_para_o_painel():
    s = parse_skill('---\nname: consorcio-lance-fgts\ndescription: d\n---\ncorpo')
    assert s.title == "Consorcio lance fgts"  # derivado do name quando não há title:
    assert parse_skill('---\nname: x\ntitle: Lance com FGTS\ndescription: d\n---\ncorpo').title == "Lance com FGTS"


# ---------- seções ----------
BIG = "---\nname: grande\ndescription: skill grande\n---\nIntro sempre presente.\n" + "".join(
    f"\n## Assunto {t}\n" + (f"texto sobre {t} " * 200) for t in ["alfa", "beta", "gama", "delta"]) + \
    "\n## Regras gerais {core}\nSempre valem estas regras.\n"


def test_secoes_so_as_relevantes_mais_core():
    skill = parse_skill(BIG)
    text, used = pick_sections(skill, "dúvida sobre gama", k=1, budget=3000)
    assert "Assunto gama" in used and "Regras gerais" in used and "(introdução)" in used
    assert "Assunto alfa" not in used and "Seções omitidas" in text and len(text) < len(skill.body)


def test_skill_curta_entra_inteira():
    skill = parse_skill("---\nname: c\ndescription: d\n---\n## A\nx\n## B\ny\n")
    assert pick_sections(skill, "qualquer", budget=6000)[0] == skill.body


# ---------- planejador e execução de queries ----------
def many_queries_skill():
    qs = "".join(
        f"  q{i}:\n    answers: dados do tema{i}\n    sql: SELECT {i} FROM t WHERE cpf = :cpf\n    params: [cpf]\n"
        for i in range(1, 7))
    return parse_skill(f"---\nname: multi\ndescription: muitas queries\nqueries:\n{qs}---\ncorpo\n")


def core_with(skill, llm, sql):
    return AgentCore(llm, SkillStore({"multi": skill, "geral": parse_skill("---\nname: geral\ndescription: g\n---\nx")}),
                     sql, max_queries=2)


def test_planejador_escolhe_so_as_queries_pedidas():
    sk = many_queries_skill()
    sql = FakeSqlRunner()
    llm = Scripted('{"queries": ["multi.q2", "multi.q5"]}')
    core = core_with(sk, llm, sql)
    eligible = core._eligible([sk], {"cpf": "1"}, [])
    plan = core._plan(eligible, "quero o tema2 e tema5")
    assert [q.name for _, q in plan] == ["q2", "q5"]


def test_planejador_com_lixo_cai_para_a_busca():
    sk = many_queries_skill()
    core = core_with(sk, Scripted("lixo"), FakeSqlRunner())
    plan = core._plan(core._eligible([sk], {"cpf": "1"}, []), "preciso do tema4")
    assert "q4" in [q.name for _, q in plan] and len(plan) == 2


def test_query_travada_estoura_timeout_sem_travar_o_atendimento():
    class Slow:
        def run(self, sql, params, max_rows):
            time.sleep(1.0)
            return []

    sk = parse_skill("---\nname: s\ndescription: d\nqueries:\n  q:\n    sql: SELECT 1 FROM t WHERE cpf = :cpf\n"
                     "    params: [cpf]\n    timeout_s: 0.1\n---\ncorpo\n")
    core = AgentCore(Scripted(), SkillStore({"s": sk}), Slow())
    ran, skipped = [], []
    t0 = time.perf_counter()
    data = core._execute([(sk, sk.queries["q"])], {"cpf": "1"}, ran, skipped)
    assert time.perf_counter() - t0 < 0.6 and skipped == ["s.q: timeout"] and data["s.q"]["erro"]
