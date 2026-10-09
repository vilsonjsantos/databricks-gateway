import os

# Antes de importar o gateway: config lê o ambiente na importação.
os.environ["TOKEN_SECRET"] = "segredo-de-teste"
os.environ["ADMIN_KEY"] = "admin-teste"
os.environ["LLM_API_KEY"] = "chave-falsa"
os.environ["APPS_DIR"] = "apps"
os.environ["HISTORY_DB"] = ":memory:"  # os testes não gravam histórico em disco

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_throttle():
    """Cada teste começa sem falhas de chave acumuladas (o bloqueio por tentativa e erro é por cliente)."""
    from gateway import main

    main.throttle._fails.clear()
    yield
