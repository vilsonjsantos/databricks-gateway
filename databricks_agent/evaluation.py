"""Avaliação do roteamento: mede acerto e mostra QUAIS skills se confundem. Roda a cada mudança de skill.

Caso (uma linha por caso em JSONL):
  {"id": "c1", "prompt": "...", "input": "...", "fields": {...}, "expect": ["consorcio-lances"],
   "expect_clarify": false}
Métricas: recall@k da busca (esperadas estavam entre os finalistas?), acerto exato (conjunto igual), acerto parcial
(esperada ⊆ escolhida), taxa de over-selection (escolheu skill a mais), esclarecimentos, e o ranking de confusões.
"""
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from databricks_agent.retrieval import CardIndex
from databricks_agent.router import LLM, select_skills
from databricks_agent.skills import SkillStore


@dataclass
class Report:
    n: int = 0
    recall_at_k: float = 0.0
    exact: float = 0.0
    contains: float = 0.0
    over_selected: float = 0.0
    clarified: float = 0.0
    confusions: list[tuple[str, int]] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        pct = lambda x: f"{100 * x:.0f}%"  # noqa: E731
        conf = "; ".join(f"{p} x{c}" for p, c in self.confusions[:5]) or "nenhuma"
        return (f"{self.n} casos | recall@k da busca {pct(self.recall_at_k)} | acerto exato {pct(self.exact)} | "
                f"contém a esperada {pct(self.contains)} | escolheu a mais {pct(self.over_selected)} | "
                f"pediu esclarecimento {pct(self.clarified)}\nconfusões (esperada -> escolhida): {conf}")


def load_cases(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_routing(llm: LLM, store: SkillStore, cases: list[dict], index: CardIndex | None = None,
                     max_skills: int = 2, direct_max: int | None = None) -> Report:
    index = index or CardIndex(store)
    rep, conf = Report(n=len(cases)), Counter()
    rec = exact = cont = over = clar = 0
    for c in cases:
        payload = {"prompt": c.get("prompt", ""), "input": c.get("input", ""), "fields": c.get("fields", {})}
        route = select_skills(llm, store, payload, max_skills, "geral", index, direct_max=direct_max)
        expect, got = set(c.get("expect", [])), set(route.names)
        if expect <= set(route.candidates):
            rec += 1
        want_clarify = bool(c.get("expect_clarify"))
        ok_exact = (route.clarify is not None) if want_clarify else (got == expect)
        exact += ok_exact
        cont += (expect <= got) if expect else not got
        over += bool(got - expect) and not want_clarify
        clar += route.clarify is not None
        if not ok_exact:
            for e in expect - got:
                for g in got - expect:
                    conf[f"{e} -> {g}"] += 1
            rep.failures.append({"id": c.get("id"), "expect": sorted(expect), "got": route.names, "via": route.method,
                                 "clarify": route.clarify, "candidates": route.candidates})
    n = max(1, len(cases))
    rep.recall_at_k, rep.exact, rep.contains, rep.over_selected, rep.clarified = rec / n, exact / n, cont / n, over / n, clar / n
    rep.confusions = conf.most_common()
    return rep
