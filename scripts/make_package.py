"""Monta o pacote limpo (zip) para levar à máquina da empresa, SEM segredos, e varre o conteúdo atrás de chaves.

Uso:  python -m scripts.make_package [--out dist]
Sai com código 1 e NÃO gera o zip se achar algo suspeito. Lista o que ficou de fora.
Fora do pacote por regra: databricks-token.txt, .env, .venv, data/, logs/, browser-profile/, .auth/, dist/, caches,
apps/demo.yaml (app de teste local) e qualquer *.sqlite. A pasta de credenciais pessoais (`acessos/`) fica fora do projeto."""
import argparse
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".git", "data", "logs", "browser-profile", ".auth",
                "dist", "node_modules", ".mypy_cache", ".ruff_cache"}
EXCLUDE_FILES = {"databricks-token.txt", ".env", "apps/demo.yaml"}
EXCLUDE_SUFFIX = (".pyc", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".log", ".zip")
TEXT_SUFFIX = {".py", ".md", ".yaml", ".yml", ".txt", ".ps1", ".html", ".css", ".js", ".gs", ".json", ".example", ""}
# Linha com o marcador `nosec-secret` é ignorada (valores de EXEMPLO/demonstração deliberados, nunca segredos reais).
SECRET_PATTERNS = {
    "token Databricks (dapi...)": re.compile(r"\bdapi[0-9a-f]{20,}\b"),
    "chave sk-...": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "Bearer longo": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{40,}"),
    "chave privada": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{10,}"),
    "token GitHub": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "chave AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "chave Google": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "token Slack": re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    "atribuição de segredo": re.compile(
        r"""(?i)(?:api[_-]?key|secret|passw(?:or)?d|token)["']?\s*[:=]\s*["'][A-Za-z0-9/+_\-]{24,}["']"""),
}


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def collect(root: Path) -> tuple[list[Path], list[str]]:
    files, skipped = [], []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = _rel(p, root)
        if any(part in EXCLUDE_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if rel in EXCLUDE_FILES or p.name in EXCLUDE_FILES or p.suffix in EXCLUDE_SUFFIX or "token" in p.name.lower() and p.suffix == ".txt":
            skipped.append(rel)
            continue
        files.append(p)
    return files, skipped


def scan(files: list[Path], root: Path) -> list[str]:
    problems = []
    for p in files:
        if p.suffix not in TEXT_SUFFIX and p.name != ".env.example":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = "\n".join(line for line in text.splitlines() if "nosec-secret" not in line)
        for label, rx in SECRET_PATTERNS.items():
            if rx.search(text):
                problems.append(f"{_rel(p, root)}: parece conter {label}")
    return problems


def build(root: Path, out_dir: Path) -> tuple[Path | None, list[Path], list[str], list[str]]:
    files, skipped = collect(root)
    problems = scan(files, root)
    if problems:
        return None, files, skipped, problems
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"databricks-gateway-{date.today().isoformat()}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, f"databricks-project/{_rel(p, root)}")
    return zpath, files, skipped, []


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    a = ap.parse_args()
    z, files, skipped, problems = build(ROOT, ROOT / a.out)
    if problems:
        print("PACOTE NÃO GERADO: possíveis segredos encontrados:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print(f"pacote: {z}  ({len(files)} arquivos, {z.stat().st_size // 1024} KB)")
    print("deixados de fora por regra:", ", ".join(skipped) or "nada além de pastas ignoradas")
    print("Lembrete: NÃO leve databricks-token.txt, .env, a pasta acessos/ nem o .venv. Na empresa crie o .env novo.")
