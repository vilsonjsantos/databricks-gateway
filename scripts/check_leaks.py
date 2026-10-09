"""Varredura anti-vazamento: rode ANTES de publicar (git push) ou de gerar o pacote. Sai com 1 se houver FALHA.

Uso:  python -m scripts.check_leaks [--git]
  --git  usa `git ls-files` (exatamente o que iria ao repositório); sem a flag usa as regras do pacote (make_package).

Verifica: (1) nomes de arquivo que nunca podem ir (.env, tokens, chaves, bancos, perfil do navegador);
(2) padrões de segredo (dapi, sk-, GitHub, AWS, Google, Slack, JWT, chave privada, atribuições longas);
(3) os VALORES REAIS dos seus segredos locais (.env, arquivos apontados por *_FILE, databricks-token.txt), lidos só em
memória e NUNCA impressos: o relatório diz apenas em qual arquivo apareceu; (4) e-mails fora de uma lista de domínios
fictícios e caminhos locais (AVISO).  Linha com `nosec-secret` é ignorada (valor de exemplo deliberado)."""
import re
import subprocess
import sys
from pathlib import Path

from scripts.make_package import ROOT, SECRET_PATTERNS, TEXT_SUFFIX, collect

FORBIDDEN_NAMES = re.compile(
    r"(^|/)(\.env(\..*)?|.*token.*\.txt|.*\.(pem|key|pfx|p12|sqlite|sqlite-wal|sqlite-shm|db))$", re.I)
FORBIDDEN_DIRS = ("browser-profile/", ".auth/", ".venv/", "data/", "logs/", "dist/", ".claude/", "acessos/")
ALLOWED_FILES = {".env.example"}
FAKE_EMAIL_DOMAINS = {"x.com", "empresa.com", "example.com", "teste", "local", "users.noreply.github.com"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/]+(?:projetos|Users)[\\/]|/Users/[A-Za-z]|/home/[a-z]+/", re.I)


def tracked_files(root: Path, use_git: bool) -> list[Path]:
    if use_git:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                             capture_output=True, check=True).stdout.decode("utf-8", "ignore")
        return [root / f for f in out.split("\0") if f and (root / f).is_file()]
    return collect(root)[0]


def local_secret_values(root: Path) -> list[str]:
    """Valores dos segredos desta máquina para procurar no que será publicado. Só em memória; nunca impressos."""
    vals: list[str] = []
    env = root / ".env"
    paths: list[Path] = [root / "databricks-token.txt"]
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            if k.strip().endswith("_FILE") and v:
                paths.append(Path(v))
            elif len(v) >= 8 and not v.startswith(("http", "<")) and k.strip() not in {"LLM_MODEL", "AUTH_MODE", "APPS_DIR"}:
                vals.append(v)
    for p in paths:
        try:
            if p.is_file():
                vals += [ln.strip() for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines() if len(ln.strip()) >= 8]
        except OSError:
            pass
    return sorted(set(vals), key=len, reverse=True)


def scan(files: list[Path], root: Path, secret_values: list[str]) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if rel not in ALLOWED_FILES and (FORBIDDEN_NAMES.search(rel) or any(rel.startswith(d) or f"/{d}" in rel for d in FORBIDDEN_DIRS)):
            fails.append(f"{rel}: arquivo/pasta que nunca pode ser publicado")
            continue
        if p.suffix not in TEXT_SUFFIX and p.name not in ALLOWED_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for v in secret_values:
            if v in text:  # o valor NUNCA entra na mensagem
                fails.append(f"{rel}: contém o valor de um segredo local ({len(v)} caracteres)")
                break
        clean = "\n".join(line for line in text.splitlines() if "nosec-secret" not in line)
        for label, rx in SECRET_PATTERNS.items():
            if rx.search(clean):
                fails.append(f"{rel}: parece conter {label}")
        for dom in {m.group(1).lower() for m in EMAIL.finditer(clean)}:
            if dom not in FAKE_EMAIL_DOMAINS and not dom.endswith((".example", ".invalid", ".test", "python.org")):
                warns.append(f"{rel}: e-mail com domínio real ({dom})")
        if LOCAL_PATH.search(clean):
            warns.append(f"{rel}: caminho absoluto local (revele só se for intencional)")
    return fails, warns


if __name__ == "__main__":
    use_git = "--git" in sys.argv
    files = tracked_files(ROOT, use_git)
    secrets = local_secret_values(ROOT)
    fails, warns = scan(files, ROOT, secrets)
    print(f"{len(files)} arquivos verificados ({'git' if use_git else 'regras do pacote'}), "
          f"{len(secrets)} valor(es) de segredo local comparado(s) (não exibidos)")
    for w in warns:
        print("AVISO", w)
    for f in fails:
        print("FALHA", f)
    print(f"\n{len(fails)} falha(s), {len(warns)} aviso(s)")
    sys.exit(1 if fails else 0)
