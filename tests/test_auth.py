import base64
import json

import pytest

from gateway.auth.databricks_oauth import jwt_exp
from gateway.auth.token_manager import DEGRADED, OK, AuthUnavailable, Token, TokenManager


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(silent, interactive, clock, **kw):
    states = []
    tm = TokenManager(silent, interactive, margin_s=300, backoff_s=30, backoff_max_s=120, clock=clock,
                      on_state_change=lambda s, m: states.append(s), **kw)
    return tm, states


def test_usa_silent_e_cacheia():
    c, calls = Clock(), []

    def silent():
        calls.append("s")
        return Token("A", c.t + 3600)

    tm, states = make(silent, None, c)
    assert tm.get_token() == "A" and tm.get_token() == "A"
    assert calls == ["s"] and states == [OK]


def test_renova_quando_expira():
    c, n = Clock(), []

    def silent():
        n.append(1)
        return Token(f"T{len(n)}", c.t + 100)

    tm, _ = make(silent, None, c)
    assert tm.get_token() == "T1"
    c.t += 90  # faltam 10s (<30s): considerado expirado
    assert tm.get_token() == "T2"


def test_silent_falha_cai_para_interativo():
    c = Clock()

    def silent():
        raise RuntimeError("precisa navegador")

    tm, states = make(silent, lambda: Token("B", c.t + 3600), c)
    assert tm.get_token() == "B" and states == [OK]


def test_falha_total_degrada_com_backoff_e_recupera():
    c, ok = Clock(), {"v": False}

    def silent():
        if not ok["v"]:
            raise RuntimeError("x")
        return Token("C", c.t + 3600)

    tm, states = make(silent, None, c)
    with pytest.raises(AuthUnavailable) as e:
        tm.get_token()
    assert e.value.retry_after == 30 and tm.state == DEGRADED
    with pytest.raises(AuthUnavailable):  # dentro do backoff não tenta de novo
        tm.get_token()
    assert tm.status()["fails"] == 1
    ok["v"] = True
    c.t += 31
    assert tm.get_token() == "C" and tm.state == OK
    assert states == [DEGRADED, OK]


def test_backoff_cresce_ate_o_teto():
    c = Clock()
    tm, _ = make(lambda: (_ for _ in ()).throw(RuntimeError("x")), None, c)
    waits = []
    for _ in range(5):
        with pytest.raises(AuthUnavailable) as e:
            tm.get_token()
        waits.append(e.value.retry_after)
        c.t += e.value.retry_after + 1
    assert waits == [30, 60, 120, 120, 120]


def test_invalidate_forca_renovacao():
    c, n = Clock(), []

    def silent():
        n.append(1)
        return Token(f"T{len(n)}", c.t + 3600)

    tm, _ = make(silent, None, c)
    tm.get_token()
    tm.invalidate()
    assert tm.get_token() == "T2"


def test_jwt_exp():
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    assert jwt_exp(f"{b({'alg': 'x'})}.{b({'exp': 12345})}.sig") == 12345.0
    assert jwt_exp("nao-e-jwt") is None
