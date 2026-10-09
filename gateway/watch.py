"""Vigia de taxa de erro: se os atendimentos estão falhando em massa, avisa no webhook (além de auth/circuito).
Só conta falhas do SERVIÇO (HTTP 5xx); erros de uso (422/429/401) não disparam alerta."""
import logging
import threading

log = logging.getLogger("gateway.watch")


def check_error_rate(events, alerter, window_s: int = 300, min_calls: int = 10, rate: float = 0.3) -> bool:
    s = events.summary(window_s)
    served = s["total"] - s.get("client_errors", 0)
    if served >= min_calls and s["failures"] / served >= rate:
        alerter.send("error-rate", f"[gateway] {s['failures']} de {served} atendimentos falharam nos últimos "
                                   f"{window_s // 60} min ({100 * s['failures'] // served}%)")
        return True
    return False


def start_error_watcher(events, alerter, window_s: int, min_calls: int, rate: float, every_s: float = 60.0):
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every_s):
            try:
                check_error_rate(events, alerter, window_s, min_calls, rate)
            except Exception:  # noqa: BLE001 - o vigia nunca pode morrer
                log.exception("erro no vigia de taxa de erro")

    threading.Thread(target=loop, name="error-watcher", daemon=True).start()
    return stop
