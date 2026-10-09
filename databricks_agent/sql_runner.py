"""Execução das queries declaradas nas skills. Sempre com parâmetros nomeados (nunca concatena valor no SQL)."""
from typing import Protocol


class SqlRunner(Protocol):
    def run(self, sql: str, params: dict[str, str], max_rows: int) -> list[dict]: ...


class DatabricksSqlRunner:
    """SQL Warehouse via databricks-sdk (Statement Execution API). Dentro do Model Serving a autenticação é
    automática (identidade do endpoint); o endpoint precisa ter o warehouse como resource e permissão nas tabelas.
    NÃO testado contra um workspace real."""

    def __init__(self, warehouse_id: str, client=None, wait_timeout: str = "30s"):
        from databricks.sdk import WorkspaceClient

        self._w = client or WorkspaceClient()
        self._warehouse, self._wait = warehouse_id, wait_timeout

    def run(self, sql: str, params: dict[str, str], max_rows: int) -> list[dict]:
        from databricks.sdk.service.sql import StatementParameterListItem as P, StatementState

        r = self._w.statement_execution.execute_statement(
            statement=sql, warehouse_id=self._warehouse, wait_timeout=self._wait, row_limit=max_rows,
            parameters=[P(name=k, value=str(v)) for k, v in params.items()])
        if r.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"statement {r.status.state}")
        cols = [c.name for c in r.manifest.schema.columns]
        return [dict(zip(cols, row)) for row in (r.result.data_array or [])]


class FakeSqlRunner:
    """Para testes/demos locais: devolve linhas fixas por trecho do SQL."""

    def __init__(self, rows_by_marker: dict[str, list[dict]] | None = None, fail: bool = False):
        self.rows, self.fail, self.calls = rows_by_marker or {}, fail, []

    def run(self, sql: str, params: dict[str, str], max_rows: int) -> list[dict]:
        self.calls.append((sql, dict(params)))
        if self.fail:
            raise RuntimeError("falha simulada com SEGREDO-NO-SQL")
        for marker, rows in self.rows.items():
            if marker in sql:
                return rows[:max_rows]
        return []
