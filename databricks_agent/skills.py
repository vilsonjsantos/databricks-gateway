"""Skills = arquivos markdown com frontmatter YAML (a "ficha" da skill + o corpo).

    ---
    name: consorcio-lances
    description: QUANDO usar (uma linha; é o que o roteador e a busca leem)
    domain: consorcio                 # grande área; filtra candidatos quando o formulário informa o assunto
    topic: lances                     # sub-assunto
    tags: [lance livre, lance fixo]   # sinônimos e termos do dia a dia
    questions:                        # 3-10 perguntas REAIS que esta skill responde (melhor sinal de busca que existe)
      - Como dar um lance?
    not_when: Para uso do FGTS no lance use consorcio-lance-fgts   # desambiguação (NÃO é indexado)
    exclusive_with: [consorcio-vts]   # alternativas mutuamente exclusivas: nunca as duas juntas
    queries:                          # as ÚNICAS consultas que esta skill pode rodar
      lances_do_cliente:
        answers: Lances ofertados pelo cliente e resultado    # o que a query TRAZ (é isto que a busca indexa, não o SQL)
        tables: [catalogo.schema.lances]                      # documentação/auditoria
        always: false                                         # true = roda sempre que houver os parâmetros
        sql: SELECT ... WHERE cpf = :cpf
        params: [cpf]
        max_rows: 20
    ---
    # Corpo: pode ter 10 páginas. Divida em seções "## Título". Seções marcadas "## Título {core}" e o texto antes
    # da primeira seção entram SEMPRE; as demais só quando relevantes ao pedido (ver retrieval.pick_sections).

O LLM nunca escreve SQL e nunca lê o SQL: só a ficha (`answers`) das queries. As queries são validadas aqui
(somente SELECT/WITH, sem `;`, parâmetros nomeados `:param`).
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|merge|grant|revoke|truncate|call|copy)\b", re.I)
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


@dataclass
class Query:
    name: str
    description: str
    sql: str
    params: list[str]
    max_rows: int = 20
    answers: str = ""  # o que a query traz, em linguagem de negócio (indexado e mostrado ao planejador)
    tables: list[str] = field(default_factory=list)
    always: bool = False
    timeout_s: float = 30.0


@dataclass
class Section:
    title: str
    text: str
    core: bool = False


@dataclass
class Skill:
    name: str
    description: str
    body: str
    triggers: list[str] = field(default_factory=list)
    queries: dict[str, Query] = field(default_factory=dict)
    title: str = ""  # nome amigável para o painel executivo (ex.: "Lance com FGTS"); vazio = derivado do name
    domain: str = ""
    topic: str = ""
    tags: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    not_when: str = ""
    exclusive_with: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)


def _validate_query(skill: str, q: Query) -> None:
    where = f"skill '{skill}', query '{q.name}'"
    sql = q.sql.strip()
    if ";" in sql:
        raise ValueError(f"{where}: ';' não é permitido")
    if not re.match(r"^(select|with)\b", sql, re.I) or _FORBIDDEN.search(sql):
        raise ValueError(f"{where}: só consultas SELECT/WITH são permitidas")
    used = set(_PARAM.findall(sql))
    if used != set(q.params):
        raise ValueError(f"{where}: params {sorted(q.params)} não batem com os usados no SQL {sorted(used)}")


def split_sections(body: str) -> list[Section]:
    """Divide em seções por '## '. O texto antes da primeira seção e títulos com '{core}' são sempre incluídos."""
    parts = re.split(r"(?m)^##\s+", body)
    sections = [Section("(introdução)", parts[0].strip(), True)] if parts[0].strip() else []
    for p in parts[1:]:
        title, _, text = p.partition("\n")
        core = "{core}" in title
        title = title.replace("{core}", "").strip()
        sections.append(Section(title, f"## {title}\n{text}".strip(), core))
    return sections


def parse_skill(text: str, source: str = "?") -> Skill:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise ValueError(f"{source}: faltou o frontmatter '---'")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:  # erro clássico: ':' dentro de description/questions sem aspas
        raise ValueError(f"{source}: frontmatter YAML inválido (valores com ':' precisam de aspas): "
                         f"{str(e).splitlines()[0]}") from e
    for key in ("name", "description"):
        if not meta.get(key):
            raise ValueError(f"{source}: frontmatter sem '{key}'")
    queries = {}
    for qname, q in (meta.get("queries") or {}).items():
        query = Query(qname, q.get("description", ""), q["sql"], list(q.get("params", [])),
                      int(q.get("max_rows", 20)), str(q.get("answers", "")).strip(), list(q.get("tables", [])),
                      bool(q.get("always", False)), float(q.get("timeout_s", 30)))
        _validate_query(meta["name"], query)
        queries[qname] = query
    body = m.group(2).strip()
    return Skill(
        meta["name"], str(meta["description"]).strip(), body, [str(t) for t in meta.get("triggers", [])], queries,
        str(meta.get("title") or str(meta["name"]).replace("-", " ").replace("_", " ").capitalize()),
        str(meta.get("domain", "")), str(meta.get("topic", "")), [str(t) for t in meta.get("tags", [])],
        [str(q) for q in meta.get("questions", [])], str(meta.get("not_when", "")).strip(),
        [str(x) for x in meta.get("exclusive_with", [])], split_sections(body))


class SkillStore:
    def __init__(self, skills: dict[str, Skill], routing: dict | None = None):
        self.skills = skills
        # _routing.yaml: field_domains (campo do formulário -> valor -> domain), candidates_k, max_skills
        self.routing = routing or {}
        for s in skills.values():
            for other in s.exclusive_with:
                if other not in skills:
                    raise ValueError(f"skill '{s.name}': exclusive_with aponta para skill inexistente '{other}'")

    @classmethod
    def from_dir(cls, path: str | Path) -> "SkillStore":
        skills: dict[str, Skill] = {}
        for f in sorted(Path(path).glob("*.md")):
            if f.name.startswith("_") or f.name.lower() == "readme.md":
                continue
            s = parse_skill(f.read_text(encoding="utf-8"), f.name)
            if s.name in skills:
                raise ValueError(f"skill duplicada: {s.name}")
            skills[s.name] = s
        if not skills:
            raise ValueError(f"nenhuma skill em {path}")
        routing_file = Path(path) / "_routing.yaml"
        routing = yaml.safe_load(routing_file.read_text(encoding="utf-8")) if routing_file.exists() else {}
        return cls(skills, routing or {})

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def catalog(self) -> list[tuple[str, str]]:
        return [(s.name, s.description) for s in self.skills.values()]

    def domains_for(self, fields: dict) -> set[str]:
        """Domínios sugeridos pelos campos estruturados do formulário (sinal determinístico, mais forte que texto)."""
        out: set[str] = set()
        for fname, mapping in (self.routing.get("field_domains") or {}).items():
            dom = (mapping or {}).get(str(fields.get(fname, "")))
            if dom:
                out.add(dom)
        return out
