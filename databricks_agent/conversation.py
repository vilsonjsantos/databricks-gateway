"""Memória de conversa do agente. Guardada DENTRO do Databricks (o texto da conversa contém dados de cliente).

O gateway manda `conversation_key` = HMAC(segredo, app|usuário|conversation_id): o agente só usa como chave de
armazenamento. Como o usuário entra no HMAC, outro operador não consegue abrir a conversa de alguém mesmo sabendo o id.

Lojas:
  InMemoryConversationStore  testes/demo e desenvolvimento. NÃO serve em produção com mais de uma réplica do endpoint
                             (cada réplica teria a sua memória) nem sobrevive a reinício.
  DeltaConversationStore     produção: tabela Delta no Unity Catalog via SQL Warehouse. NÃO testada em Databricks real.

    CREATE TABLE <catalogo>.<schema>.conversas (
      conversation_key STRING, ts_ms BIGINT, turn_json STRING)
    TBLPROPERTIES (delta.autoOptimize.optimizeWrite = true);
    -- retenção: job diário  DELETE FROM <catalogo>.<schema>.conversas WHERE ts_ms < <agora_ms - 30 dias>
"""
import json
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class Turn:
    user: str  # o que o operador pediu neste turno (prompt + texto), já sem dados desnecessários
    assistant: str
    skills: list[str] = field(default_factory=list)
    fields: dict = field(default_factory=dict)  # campos do formulário deste turno (ex.: cpf), para continuar a conversa
    ts: float = field(default_factory=time.time)


class ConversationStore(Protocol):
    def load(self, key: str, max_turns: int) -> list[Turn]: ...
    def append(self, key: str, turn: Turn) -> None: ...


class InMemoryConversationStore:
    def __init__(self, ttl_s: float = 3600.0, max_conversations: int = 5000, max_turns_kept: int = 20):
        self._d: OrderedDict[str, deque[Turn]] = OrderedDict()
        self._ttl, self._max, self._keep = ttl_s, max_conversations, max_turns_kept
        self._lock = threading.Lock()

    def load(self, key: str, max_turns: int) -> list[Turn]:
        with self._lock:
            turns = [t for t in self._d.get(key, ()) if time.time() - t.ts <= self._ttl]
        return turns[-max_turns:]

    def append(self, key: str, turn: Turn) -> None:
        with self._lock:
            self._d.setdefault(key, deque(maxlen=self._keep)).append(turn)
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)


class DeltaConversationStore:
    """Tabela Delta (ver DDL no topo). `executor` = SDK `WorkspaceClient().statement_execution`; passe um falso nos testes."""

    def __init__(self, table: str, warehouse_id: str, executor=None, ttl_s: float = 7 * 86400, wait_timeout: str = "20s"):
        if not all(c.isalnum() or c in "._`" for c in table):
            raise ValueError("nome de tabela inválido")
        if executor is None:
            from databricks.sdk import WorkspaceClient

            executor = WorkspaceClient().statement_execution
        self._x, self._table, self._wh, self._ttl, self._wait = executor, table, warehouse_id, ttl_s, wait_timeout

    def _run(self, sql: str, params: dict):
        from databricks.sdk.service.sql import StatementParameterListItem as P

        r = self._x.execute_statement(statement=sql, warehouse_id=self._wh, wait_timeout=self._wait,
                                      parameters=[P(name=k, value=str(v)) for k, v in params.items()])
        if str(r.status.state).endswith("FAILED") or str(r.status.state).endswith("CANCELED"):
            raise RuntimeError(f"statement {r.status.state}")
        return r

    def load(self, key: str, max_turns: int) -> list[Turn]:
        n = int(max_turns)  # inteiro validado: entra como literal (LIMIT)
        cutoff = int((time.time() - self._ttl) * 1000)
        r = self._run(f"SELECT turn_json FROM {self._table} WHERE conversation_key = :k AND ts_ms > :c "
                      f"ORDER BY ts_ms DESC LIMIT {n}", {"k": key, "c": cutoff})
        rows = (r.result.data_array or []) if r.result else []
        return [Turn(**json.loads(row[0])) for row in reversed(rows)]

    def append(self, key: str, turn: Turn) -> None:
        self._run(f"INSERT INTO {self._table} (conversation_key, ts_ms, turn_json) VALUES (:k, :t, :j)",
                  {"k": key, "t": int(turn.ts * 1000), "j": json.dumps(asdict(turn), ensure_ascii=False)})


def render_history(turns: list[Turn], budget: int = 4000) -> str:
    """Histórico para o prompt da resposta: do mais recente para trás até caber no orçamento; saída em ordem cronológica."""
    lines: list[str] = []
    used = 0
    for t in reversed(turns):
        block = f"Operador: {t.user}\nAssistente: {t.assistant}"
        if used + len(block) > budget:
            if not lines:  # o turno mais recente nunca é descartado: é cortado
                lines.append(block[-budget:])
            break
        lines.append(block)
        used += len(block)
    return "\n---\n".join(reversed(lines))
