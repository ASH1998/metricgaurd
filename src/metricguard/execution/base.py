"""Warehouse execution abstraction.

The live Postgres and static test executors share this interface. Everything in
MetricGuard that runs SQL depends on it, so semantic analysis still degrades
cleanly when a warehouse is not configured.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

Row = dict[str, Any]


class NotConfiguredError(RuntimeError):
    """Raised when execution is requested before the warehouse is wired up."""


class WarehouseExecutor(ABC):
    @abstractmethod
    def query(self, sql: str) -> list[Row]:
        """Run SQL and return rows as dicts keyed by column name."""

    @abstractmethod
    def ping(self) -> bool:
        """Cheap connectivity check."""


class StaticExecutor(WarehouseExecutor):
    """Test/demo executor that returns pre-canned rows keyed by SQL.

    Keeps divergence and agent tests deterministic without a live warehouse.
    """

    def __init__(self, responses: dict[str, list[Row]] | None = None,
                 default: list[Row] | None = None):
        self._responses = responses or {}
        self._default = default

    def query(self, sql: str) -> list[Row]:
        key = " ".join(sql.split())  # whitespace-insensitive lookup
        for known, rows in self._responses.items():
            if " ".join(known.split()) == key:
                return rows
        if self._default is not None:
            return self._default
        raise KeyError(f"StaticExecutor has no canned response for: {sql[:80]}...")

    def ping(self) -> bool:
        return True


def get_executor() -> WarehouseExecutor:
    """Factory: returns the configured executor, or raises NotConfiguredError.

    Once POSTGRES_DSN is set (and the `warehouse` extra installed), this
    returns a live PostgresExecutor. Until then callers should catch
    NotConfiguredError and degrade gracefully (signature comparison still
    works without execution — only divergence proof needs the warehouse).
    """
    from metricguard.config import settings

    if not settings.postgres_dsn:
        raise NotConfiguredError(
            "POSTGRES_DSN is not set. Signature comparison works without it; "
            "divergence execution needs a configured Postgres warehouse."
        )
    from metricguard.execution.postgres import PostgresExecutor
    return PostgresExecutor(settings.postgres_dsn)
