"""Busca para escolher skills e seções entre muitas skills parecidas.

Camadas (cada uma barata e determinística; o LLM só decide entre os finalistas):
  1. BM25 sobre a FICHA de cada skill (nome, tópico, tags, perguntas de exemplo, descrição e o que as queries
     trazem). O texto de `not_when` NÃO é indexado (não pode atrair o pedido que ele pretende afastar).
  2. Opcional: vetores (embeddings) por `embedder` injetável; fundidos ao BM25 por RRF (reciprocal rank fusion).
  3. Seções: dentro de uma skill grande, só as seções relevantes ao pedido entram no contexto.
Tudo roda em memória; para dezenas/centenas de skills não precisa de infraestrutura extra.
"""
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from databricks_agent.skills import Skill, SkillStore

_STOP = frozenset(
    "a o as os um uma uns umas de do da dos das em no na nos nas por para com sem e ou que se qual quais como "
    "posso pode podem ser ter ha tem eu ele ela cliente ao aos mais mas ja the".split())

Embedder = Callable[[list[str]], list[list[float]]]


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _stem(t: str) -> str:
    if t.endswith("coes"):
        return t[:-4] + "cao"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", _fold(text)) if len(t) > 1 and t not in _STOP]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.n = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.n) if self.n else 1.0
        self.tf = [Counter(d) for d in docs]
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, doc in zip(self.tf, self.docs):
            s, dl = 0.0, len(doc)
            for t in set(query):
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out


def rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for pos, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + pos + 1)
    return fused


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


@dataclass
class Hit:
    name: str
    bm25: float
    score: float  # após fusão; só para ordenar


def card_text(s: Skill) -> str:
    """Texto indexado da ficha. Perguntas/tags/tópico pesam mais (repetidos); `not_when` fica de fora."""
    strong = " ".join([s.name.replace("-", " "), s.topic, s.domain, *s.tags, *s.questions])
    queries = " ".join(f"{q.name.replace('_', ' ')} {q.answers}" for q in s.queries.values())
    return f"{strong} {strong} {s.description} {queries}"


class CardIndex:
    def __init__(self, store: SkillStore, embedder: Embedder | None = None):
        self.store, self.embedder = store, embedder
        self.skills = list(store.skills.values())
        self.texts = [card_text(s) for s in self.skills]
        self.bm25 = BM25([tokenize(t) for t in self.texts])
        self.vecs = embedder(self.texts) if embedder else None

    def search(self, text: str, k: int = 6, domains: set[str] | None = None,
               exclude: set[str] | None = None) -> list[Hit]:
        q = tokenize(text)
        allowed = [i for i, s in enumerate(self.skills)
                   if (not domains or s.domain in domains) and s.name not in (exclude or ())]
        bm = self.bm25.scores(q)
        rankings = [sorted((i for i in allowed if bm[i] > 0), key=lambda i: -bm[i])]
        if self.embedder and self.vecs:
            qv = self.embedder([text])[0]
            sims = {i: _cos(qv, self.vecs[i]) for i in allowed}
            rankings.append(sorted(allowed, key=lambda i: -sims[i]))
        fused = rrf([r for r in rankings if r])
        order = sorted(fused, key=lambda i: -fused[i])[:k]
        return [Hit(self.skills[i].name, bm[i], fused[i]) for i in order]


class OpenAIEmbedder:
    """Embeddings por qualquer cliente OpenAI-compatível (no Databricks: um endpoint de embeddings MULTILÍNGUE; os
    modelos 'gte/bge large en' que lembro são focados em inglês — conferir o que o workspace oferece)."""

    def __init__(self, client, model: str, batch: int = 64):
        self.client, self.model, self.batch = client, model, batch

    def __call__(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            r = self.client.embeddings.create(model=self.model, input=texts[i:i + self.batch])
            out += [d.embedding for d in r.data]
        return out


def pick_sections(skill: Skill, request_text: str, k: int = 3, budget: int = 6000) -> tuple[str, list[str]]:
    """(texto para o contexto, títulos das seções usadas). Skill curta entra inteira."""
    if len(skill.body) <= budget or len(skill.sections) <= 1:
        return skill.body, [s.title for s in skill.sections]
    core = [s for s in skill.sections if s.core]
    rest = [s for s in skill.sections if not s.core]
    scores = BM25([tokenize(f"{s.title} {s.title} {s.text}") for s in rest]).scores(tokenize(request_text)) if rest else []
    ranked = [s for _, s in sorted(zip(scores, rest), key=lambda x: -x[0]) if _ > 0][:k] or rest[:1]
    # Orçamento distribuído por relevância (core primeiro, depois as mais relevantes); a mais relevante nunca é
    # descartada: se não couber inteira, é cortada. A saída mantém a ordem original do documento.
    allot: dict[int, str] = {}
    left = budget
    for s in core + ranked:
        if s.core:
            allot[id(s)] = s.text
            left -= len(s.text)
        elif left >= 200:
            allot[id(s)] = s.text if len(s.text) <= left else s.text[:left].rstrip() + "\n[...seção cortada por tamanho]"
            left -= len(allot[id(s)])
    used = [s.title for s in skill.sections if id(s) in allot]
    kept = [allot[id(s)] for s in skill.sections if id(s) in allot]
    omitted = [s.title for s in skill.sections if s.title not in used]
    note = f"\n\n[Seções omitidas por não serem relevantes a este pedido: {', '.join(omitted)}]" if omitted else ""
    return "\n\n".join(kept) + note, used
