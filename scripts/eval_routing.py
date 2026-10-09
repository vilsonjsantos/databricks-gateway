"""Mede o roteamento de skills. Rode a cada mudança de skill e ANTES de publicar uma nova versão do agente.

Uso:
  python -m scripts.eval_routing --corpus                       # corpus sintético (tests/corpus.py), só busca (sem LLM)
  python -m scripts.eval_routing --corpus --real                # idem, com o LLM configurado no .env (DeepSeek em casa)
  python -m scripts.eval_routing --skills databricks_agent/skills --cases meus_casos.jsonl [--real]
Casos (JSONL): {"id","prompt","input","fields","expect":["skill"],"expect_clarify":false}
"""
import argparse
import json
import tempfile
from pathlib import Path

from databricks_agent.evaluation import evaluate_routing, load_cases
from databricks_agent.retrieval import CardIndex
from databricks_agent.skills import SkillStore


def _search_only_llm(messages, role, max_tokens):
    """Sem LLM: confirma o 1º finalista da busca. Mede o piso (busca pura)."""
    first = messages[1]["content"].split("- ", 1)[1].split(" ", 1)[0]
    return json.dumps({"skills": [{"name": first, "confidence": 0.9}]})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("--skills")
    ap.add_argument("--cases")
    ap.add_argument("--hard", action="store_true", help="com --corpus: usa os casos difíceis (paráfrases)")
    ap.add_argument("--direct-max", type=int, default=None,
                    help="até quantas skills vão direto ao escolhedor; use 5 para forçar o modo busca+sinônimos")
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    if a.corpus:
        from tests.corpus import CASES, HARD_CASES, write_skills

        tmp = Path(tempfile.mkdtemp())
        write_skills(tmp)
        store = SkillStore.from_dir(tmp)
        chosen = HARD_CASES if a.hard else CASES
        cases = [{"id": f"c{i}", "prompt": p, "fields": f, "expect": e} for i, (p, f, e) in enumerate(chosen)]
    else:
        store, cases = SkillStore.from_dir(a.skills), load_cases(a.cases)

    llm = _search_only_llm
    if a.real:
        from openai import OpenAI

        from databricks_agent.agent import OpenAILLM
        from gateway.config import get_settings

        s = get_settings()
        llm = OpenAILLM(OpenAI(base_url=s.llm_base_url, api_key=s.resolve_llm_key()), s.llm_model, s.llm_model)
    rep = evaluate_routing(llm, store, cases, CardIndex(store), direct_max=a.direct_max)
    print(rep.summary())
    for f in rep.failures[: (50 if a.verbose else 8)]:
        print("  FALHA", f)


if __name__ == "__main__":
    main()
