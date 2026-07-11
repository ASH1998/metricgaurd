"""Postgres executor — wired when the demo warehouse lands.

Install with: uv sync --extra warehouse
"""

from __future__ import annotations

from metricguard.execution.base import NotConfiguredError, Row, WarehouseExecutor


class PostgresExecutor(WarehouseExecutor):
    def __init__(self, dsn: str):
        if not dsn:
            raise NotConfiguredError("Empty POSTGRES_DSN")
        try:
            import psycopg  # noqa: F401  (deferred import — optional extra)
        except ImportError as e:
            raise NotConfiguredError(
                "psycopg is not installed. Run: uv sync --extra warehouse"
            ) from e
        self._dsn = dsn

    def query(self, sql: str) -> list[Row]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return list(cur.fetchall())

    def ping(self) -> bool:
        try:
            self.query("SELECT 1 AS ok")
            return True
        except Exception:
            return False
