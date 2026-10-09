import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from databricks_agent.agent import AgentCore
from databricks_agent.conversation import (DeltaConversationStore, InMemoryConversationStore, Turn,
                                           render_history)
from databricks_agent.skills import SkillStore
from databricks_agent.sql_runner import FakeSqlRunner

SKILLS = Path(__file__).resolve().parent.parent / "databricks_agent" / "skills"


class ConvLLM:
    """Roteador devolve a skill fixa; condensador devolve a pergunta fixa; resposta ecoa algo previsível."""

    def __init__(self, skill="consorcio-lances", clarify=None, standalone="Como funciona o lance livre?"):
        self.skill, self.clarify, self.standalone, self.calls = skill, clarify, standalone, []

    def __call__(self, messages, role, max_tokens):
        sys_ = messages[0]["content"]
        self.calls.append((role, sys_[:25], messages))
        if "Reescreva a ÚLTIMA" in sys_:
            return json.dumps({"pergunta": self.standalone})
        if role == "router":
            if self.clarify:
                return json.dumps({"skills": [], "clarify": self.clarify})
            return json.dumps({"skills": [{"name": self.skill, "confidence": 0.9}]})
        return "resposta final"


@pytest.fixture
def store():
    return SkillStore.from_dir(SKILLS)


def payload(key="k1", prompt="e se for livre?", fields=None):
    return {"app": "a", "user": "u", "prompt": prompt, "input": "", "fields": fields or {},
            "conversation_key": key, "conversation_max_turns": 6}


def test_primeiro_turno_nao_condensa_e_grava(store):
    conv = InMemoryConversationStore()
    llm = ConvLLM()
    core = AgentCore(llm, store, FakeSqlRunner(), conversations=conv)
    r = core.handle(payload(prompt="Como dou um lance?"))
    assert r.text == "resposta final" and not any("Reescreva" in c[1] for c in llm.calls)
    saved = conv.load("k1", 6)
    assert len(saved) == 1 and saved[0].assistant == "resposta final" and saved[0].skills == ["consorcio-lances"]


def test_segundo_turno_usa_historico_pergunta_independente_e_skill_anterior(store):
    conv = InMemoryConversationStore()
    llm = ConvLLM()
    core = AgentCore(llm, store, FakeSqlRunner(), conversations=conv)
    core.handle(payload(prompt="Como dou um lance?"))
    llm.calls.clear()
    core.handle(payload(prompt="e se for livre?"))
    kinds = [c[1] for c in llm.calls]
    assert any("Reescreva a ÚLTIMA" in k for k in kinds)  # condensou
    router_msgs = next(c[2] for c in llm.calls if c[0] == "router" and "Reescreva" not in c[1])
    assert "lance livre" in router_msgs[1]["content"] and "TURNO ANTERIOR" in router_msgs[1]["content"]
    answer_msgs = llm.calls[-1][2]
    assert "<historico>" in answer_msgs[1]["content"] and "Como dou um lance?" in answer_msgs[1]["content"]
    assert len(conv.load("k1", 6)) == 2


def test_campos_do_turno_anterior_continuam_valendo(store):
    conv = InMemoryConversationStore()
    sql = FakeSqlRunner({"tickets": [{"protocolo": "P-1"}]})
    llm = ConvLLM(skill="consorcio")
    core = AgentCore(llm, store, sql, conversations=conv)
    core.handle(payload(prompt="tickets do cliente", fields={"cpf": "12345678909"}))
    core.handle(payload(prompt="e o mais recente?", fields={}))  # sem CPF no 2º turno
    assert len(sql.calls) == 2 and sql.calls[1][1] == {"cpf": "12345678909"}


def test_pergunta_de_esclarecimento_fica_na_memoria(store):
    conv = InMemoryConversationStore()
    llm = ConvLLM(clarify="Você quer lance livre ou fixo?")
    r = AgentCore(llm, store, FakeSqlRunner(), conversations=conv).handle(payload(prompt="quero dar um lance"))
    assert r.clarify
    t = conv.load("k1", 6)[0]
    assert t.assistant == "Você quer lance livre ou fixo?" and t.skills == []


def test_conversas_de_chaves_diferentes_sao_isoladas(store):
    conv = InMemoryConversationStore()
    core = AgentCore(ConvLLM(), store, FakeSqlRunner(), conversations=conv)
    core.handle(payload(key="ana", prompt="segredo da ana"))
    core.handle(payload(key="bia", prompt="algo da bia"))
    assert [t.user for t in conv.load("ana", 6)] == ["segredo da ana"]
    assert "segredo da ana" not in json.dumps([t.__dict__ for t in conv.load("bia", 6)])


def test_falha_na_memoria_nao_derruba_o_atendimento(store):
    class Broken:
        def load(self, *a):
            raise RuntimeError("warehouse parado")

        def append(self, *a):
            raise RuntimeError("warehouse parado")

    r = AgentCore(ConvLLM(), store, FakeSqlRunner(), conversations=Broken()).handle(payload())
    assert r.text == "resposta final"


def test_sem_loja_ou_sem_chave_funciona_como_antes(store):
    r = AgentCore(ConvLLM(), store, FakeSqlRunner()).handle(payload())
    assert r.text == "resposta final"
    r2 = AgentCore(ConvLLM(), store, FakeSqlRunner(), conversations=InMemoryConversationStore()).handle(
        {"prompt": "oi", "input": "", "fields": {}})
    assert r2.text == "resposta final"


def test_memoria_expira_e_limita_turnos():
    conv = InMemoryConversationStore(ttl_s=0.2)
    for i in range(5):
        conv.append("k", Turn(f"u{i}", f"a{i}"))
    assert [t.user for t in conv.load("k", 3)] == ["u2", "u3", "u4"]
    time.sleep(0.3)
    assert conv.load("k", 3) == []


def test_render_historico_respeita_orcamento_e_mantem_o_mais_recente():
    turns = [Turn(f"pergunta {i} " + "x" * 200, f"resposta {i}") for i in range(10)]
    out = render_history(turns, budget=600)
    assert "pergunta 9" in out and "pergunta 0" not in out and len(out) <= 700
    assert render_history([Turn("u" * 5000, "a")], budget=300)  # turno único gigante é cortado, não descartado


# ---------- loja Delta (com executor falso; Databricks real NÃO testado) ----------
class FakeExec:
    def __init__(self, rows=None):
        self.rows, self.stmts = rows or [], []

    def execute_statement(self, statement, warehouse_id, wait_timeout, parameters):
        self.stmts.append((statement, {p.name: p.value for p in parameters}))
        return SimpleNamespace(status=SimpleNamespace(state="StatementState.SUCCEEDED"),
                               result=SimpleNamespace(data_array=self.rows))


def test_delta_grava_com_parametros_e_le_em_ordem():
    ex = FakeExec()
    st = DeltaConversationStore("cat.sch.conversas", "wh1", executor=ex)
    st.append("chave", Turn("pergunta ' com aspas", "resposta", ["s"], {"cpf": "1"}, ts=1000.0))
    sql, params = ex.stmts[0]
    assert sql.startswith("INSERT INTO cat.sch.conversas") and "pergunta" not in sql  # valores só como parâmetro
    assert params["k"] == "chave" and json.loads(params["j"])["user"] == "pergunta ' com aspas"
    rows = [[json.dumps({"user": "u2", "assistant": "a2", "skills": [], "fields": {}, "ts": 2.0})],
            [json.dumps({"user": "u1", "assistant": "a1", "skills": [], "fields": {}, "ts": 1.0})]]  # DESC no banco
    st2 = DeltaConversationStore("cat.sch.conversas", "wh1", executor=FakeExec(rows))
    assert [t.user for t in st2.load("chave", 5)] == ["u1", "u2"]  # devolvidas em ordem cronológica


def test_delta_rejeita_nome_de_tabela_perigoso():
    with pytest.raises(ValueError):
        DeltaConversationStore("t; DROP TABLE x", "wh", executor=FakeExec())
