"""Verifica a qualidade das fichas das skills ANTES de publicar. Sai com código 1 se houver problema grave.

Uso:  python -m scripts.lint_skills [pasta]        (padrão: databricks_agent/skills)
Checa: falta de perguntas de exemplo / not_when / domain; skills com fichas parecidas demais (risco de confusão no
roteador) e sem 'not_when' uma para a outra; queries sem 'answers' (invisíveis para a busca e o planejador).
"""
import sys
from itertools import combinations

from databricks_agent.retrieval import BM25, card_text, tokenize
from databricks_agent.skills import SkillStore

SIMILAR = 0.55  # Jaccard dos termos da ficha a partir do qual duas skills são "parecidas"


def lint(store: SkillStore) -> tuple[list[str], list[str]]:
    errors, warns = [], []
    toks = {n: set(tokenize(card_text(s))) for n, s in store.skills.items()}
    for n, s in store.skills.items():
        if n == "geral":
            continue
        if len(s.questions) < 3:
            warns.append(f"{n}: só {len(s.questions)} pergunta(s) de exemplo (recomendado 3-10; é o melhor sinal de busca)")
        if not s.domain:
            warns.append(f"{n}: sem 'domain' (o filtro por campo do formulário não funciona para ela)")
        for q in s.queries.values():
            if not q.answers:
                warns.append(f"{n}.{q.name}: query sem 'answers' (a busca e o planejador não a enxergam)")
    for a, b in combinations(store.skills, 2):
        if "geral" in (a, b):
            continue
        inter, union = toks[a] & toks[b], toks[a] | toks[b]
        sim = len(inter) / len(union) if union else 0.0
        if sim >= SIMILAR and not (store.skills[a].not_when and store.skills[b].not_when):
            errors.append(f"{a} <-> {b}: fichas {sim:.0%} parecidas e falta 'not_when' em uma delas "
                          f"(termos em comum: {', '.join(sorted(inter)[:6])})")
        elif sim >= SIMILAR:
            warns.append(f"{a} <-> {b}: fichas {sim:.0%} parecidas (têm 'not_when'; confirme na avaliação)")
    if "geral" not in store.skills:
        warns.append("sem skill 'geral' (último fallback do roteador)")
    return errors, warns


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "databricks_agent/skills"
    st = SkillStore.from_dir(path)
    errs, warns = lint(st)
    for w in warns:
        print("AVISO", w)
    for e in errs:
        print("ERRO ", e)
    print(f"{len(st.skills)} skills | {len(errs)} erro(s) | {len(warns)} aviso(s)")
    sys.exit(1 if errs else 0)
