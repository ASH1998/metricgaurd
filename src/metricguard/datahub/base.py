"""DataHub client abstraction — read + write-back.

The live implementation goes through the official DataHub MCP Server for graph
reads and verified stock mutations. Tests use the same interface and response
shapes through StubDataHubClient.

Write-back rules (context.md):
- Only entities that exist in stock DataHub Core today. Nothing depends on
  the unmerged metrics PR.
- The agent PROPOSES, a human APPROVES. `require_approval` is enforced here,
  at the boundary, so no caller can accidentally bypass it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from metricguard.config import settings


class ApprovalRequiredError(RuntimeError):
    """A mutation was attempted without human approval."""


@dataclass
class WriteAction:
    """A proposed (or executed) write-back, kept for audit/demo output."""
    kind: str                      # document | tag | description | structured_property | glossary_term
    target: str                    # URN or entity name
    payload: dict[str, Any]        # must match the mapped MCP tool's args
    executed: bool = False


class DataHubClient(ABC):
    # ---- read side ----
    @abstractmethod
    def search_queries(self, keyword: str) -> list[dict[str, Any]]:
        """Find queries/definitions mentioning a keyword (candidate discovery)."""

    @abstractmethod
    def get_dataset_queries(self, dataset_urn: str) -> list[dict[str, Any]]:
        """Queries observed against a dataset."""

    @abstractmethod
    def get_entities(self, urns: list[str] | str) -> list[dict[str, Any]] | dict[str, Any]:
        """Fetch rich graph context (ownership, domains, tags, properties) for entities."""

    @abstractmethod
    def get_lineage(
        self,
        urn: str,
        *,
        upstream: bool = True,
        max_hops: int = 1,
        max_results: int = 30,
    ) -> dict[str, Any]:
        """Upstream/downstream lineage for an entity."""

    # ---- write side (all gated by approval) ----
    @abstractmethod
    def _execute_write(self, action: WriteAction) -> WriteAction:
        """Perform the mutation. Implementations only — call `write()`."""

    def write(self, action: WriteAction, approved: bool = False) -> WriteAction:
        """Single gated entrypoint for every mutation."""
        if settings.require_approval and not approved:
            raise ApprovalRequiredError(
                f"Write-back '{action.kind}' to '{action.target}' requires human approval. "
                "Pass approved=True only after an explicit user confirmation."
            )
        return self._execute_write(action)

    # Proposal builders live in datahub/writeback.py — they produce payloads
    # matching the real MCP mutation tools. Nothing constructs write-backs here.


class StubDataHubClient(DataHubClient):
    """In-memory stand-in until the MCP connection lands.

    Reads return seeded/canned data; writes are recorded (never sent anywhere)
    so the full discovery→resolution→write-back flow can be developed and
    demoed end to end.
    """

    def __init__(self, canned_queries: list[dict[str, Any]] | None = None):
        # canned_queries entries are MCP-shaped (same envelope the real server
        # returns) so discovery.candidates_from_graph has a single parsing path.
        # Simple form: {dataset_urn, query_urn, name, sql}. Build with `from_specs`.
        self.canned_queries = canned_queries or []
        self.write_log: list[WriteAction] = []

    @classmethod
    def from_specs(cls, specs: list[dict[str, Any]]) -> StubDataHubClient:
        """Build a stub from simple {dataset_urn, query_urn, name, sql} dicts."""
        return cls(canned_queries=list(specs))

    def search_queries(self, keyword: str) -> list[dict[str, Any]]:
        kw = keyword.lower()
        out, seen = [], set()
        for q in self.canned_queries:
            urn = q["dataset_urn"]
            if urn in seen:
                continue
            if kw in ("", "*") or kw in q.get("name", "").lower() or kw in q.get("sql", "").lower():
                seen.add(urn)
                out.append({"entity": {"urn": urn, "properties": {"name": q.get("name", "")}}})
        return out

    def get_dataset_queries(self, dataset_urn: str) -> list[dict[str, Any]]:
        return [
            {
                "urn": q["query_urn"],
                "properties": {
                    "name": q.get("name", ""),
                    "description": q.get("source", ""),
                    "statement": {"value": q["sql"], "language": "SQL"},
                },
                "subjects": [dataset_urn],
            }
            for q in self.canned_queries if q["dataset_urn"] == dataset_urn
        ]

    def get_entities(self, urns: list[str] | str) -> list[dict[str, Any]] | dict[str, Any]:
        return_single = isinstance(urns, str)
        requested = [urns] if return_single else urns
        entities = []
        for urn in requested:
            spec = next((q for q in self.canned_queries if q["dataset_urn"] == urn), {})
            owner = spec.get("owner") or (
                spec.get("name", "").split(":", 1)[0]
                if ":" in spec.get("name", "") else ""
            )
            entities.append({
                "urn": urn,
                "type": "DATASET",
                "properties": {
                    "name": urn.split(",")[-2].split(".")[-1] if "," in urn else urn,
                    "description": spec.get("source", ""),
                    "customProperties": {
                        **({"owner_team": owner} if owner else {}),
                        **({"metric_family": spec["metric_family"]}
                           if spec.get("metric_family") else {}),
                    },
                },
                "ownership": {
                    "owners": [{"owner": {"urn": f"urn:li:corpGroup:{owner}"}}]
                    if owner else []
                },
                "subTypes": {"typeNames": [spec.get("subtype", "Dataset")]},
            })
        return entities[0] if return_single else entities

    def get_lineage(
        self,
        urn: str,
        *,
        upstream: bool = True,
        max_hops: int = 1,
        max_results: int = 30,
    ) -> dict[str, Any]:
        direction = "upstreams" if upstream else "downstreams"
        results = []
        for q in self.canned_queries:
            if q["dataset_urn"] != urn:
                continue
            key = "upstream" if upstream else "downstream"
            for related in q.get(key, []):
                results.append({"entity": related, "degree": 1})
        return {direction: {"searchResults": results[:max_results], "returned": len(results)}}

    def _execute_write(self, action: WriteAction) -> WriteAction:
        action.executed = True
        self.write_log.append(action)
        return action


def get_datahub_client() -> DataHubClient:
    """Factory. MCP-backed client when DATAHUB_MCP_TRANSPORT is set; stub otherwise."""
    if settings.datahub_mcp_transport:
        from metricguard.datahub.mcp_client import get_mcp_client
        return get_mcp_client()
    return StubDataHubClient()
