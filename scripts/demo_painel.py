"""Mostra o painel executivo com dados FICTÍCIOS (marcados como demonstração na tela). Não toca no histórico real.

Uso:  python -m scripts.demo_painel [--port 8767] [--no-browser]
Para parar: Ctrl+C (ou feche a janela do terminal)."""
import argparse
import os
import random
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path


def seed(path: str) -> None:
    from gateway.observability.store import HistoryStore

    random.seed(7)
    s = HistoryStore(path, salt="demo")
    now = time.time()
    today0 = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    users = [f"operador{i}@empresa.com" for i in range(1, 19)]
    apps = [("assistente-operadores", 0.62), ("assistente-cobranca", 0.28), ("assistente-ouvidoria", 0.10)]
    topics = ["Lance com FGTS", "Lances", "Boletos", "Contemplação", "Tickets e protocolos", "Cancelamento", "Parcelas",
              "Assembleias", "Transferência de cota", "Seguro prestamista", "Taxas e fundo de reserva", "Reajuste",
              "Quitação antecipada", "Liberação do crédito"]  # lista longa de propósito: mostra a rolagem interna
    weights = {8: 3, 9: 8, 10: 11, 11: 12, 12: 6, 13: 8, 14: 12, 15: 11, 16: 9, 17: 5, 18: 2}
    for back in range(6, -1, -1):
        base = today0 - back * 86400
        for h, w in weights.items():
            for _ in range(int(w * (1.0 if back else 0.9) * random.uniform(2.6, 3.4))):
                ts = base + h * 3600 + random.uniform(0, 3599)
                if ts > now:
                    continue
                app = random.choices([a for a, _ in apps], [p for _, p in apps])[0]
                fail = random.random() < 0.025
                s.record_call(app, random.choice(users), 502 if fail else 200,
                              800 if fail else int(random.gauss(3800, 900)),
                              [] if fail else random.sample(topics, random.choice([1, 1, 2])), random.random() < 0.06, ts=ts)
    s.record_incident("conexao", "Instabilidade", ts=today0 + 11 * 3600 + 120)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    db = str(Path(tempfile.mkdtemp()) / "demo.sqlite")
    # variáveis ANTES de importar o gateway (a configuração é lida na importação)
    os.environ.update(HISTORY_DB=db, TOKEN_SECRET="demo-secret-demo-secret-demo-secret", ADMIN_KEY="demo",  # nosec-secret (demo local)
                      PANEL_DEMO="true", PANEL_TITLE="Assistente de Atendimento", LLM_API_KEY="x")
    seed(db)
    url = f"http://localhost:{a.port}/painel?k=demo"
    print(f"Painel de demonstração: {url}   (Ctrl+C para encerrar)")
    if not a.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    import uvicorn

    uvicorn.run("gateway.main:app", host="127.0.0.1", port=a.port, log_level="warning")
