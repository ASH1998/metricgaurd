"""Graph-native candidate discovery.

This is what makes MetricGuard *Semantic Conflict Intelligence* rather than a
catalog lookup: we do NOT tell it which datasets form a metric family. We pull
candidate SQL definitions out of DataHub (search -> get_dataset_queries),
extract each one's SemanticSignature with the *unchanged* deterministic
extractor, and hand the lot to the clustering layer — which discovers the
families (and thus the hidden conflicts) from signals, not from a declaration.

Reads go through the DataHubClient ABC, so the same code runs against the live
MCP server and the in-memory stub (tests). LLM judgment sits on top of the
returned candidates in the agent loop; nothing here calls an LLM.
"""

from __future__ import annotations

from metricguard.config import settings
from metricguard.datahub.base import DataHubClient
from metricguard.models import MetricDefinition
from metricguard.signature.extractor import extract_signature


def _ordered_dataset_urns(search_results: list[dict]) -> list[str]:
    """Unique dataset URNs from search results, order-preserving."""
    urns: list[str] = []
    seen: set[str] = set()
    for result in search_results:
        urn = (result.get("entity") or {}).get("urn", "")
        if urn.startswith("urn:li:dataset:") and urn not in seen:
            seen.add(urn)
            urns.append(urn)
    return urns


def _short_name(dataset_urn: str) -> str:
    """Last dotted segment of a dataset URN's name — the org's own (inconsistent) label.

    'urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)'
      -> 'weekly_revenue'
    """
    inner = dataset_urn.split(",")
    name = inner[1] if len(inner) >= 2 else dataset_urn
    return name.split(".")[-1]


def _owner_from(query_name: str) -> str:
    """Query names are seeded as '<owner>: <metric>' — pull the owner if present."""
    return query_name.split(":", 1)[0].strip() if ":" in query_name else ""


def candidates_from_graph(
    client: DataHubClient,
    keyword: str = "*",
    dialect: str | None = None,
) -> list[MetricDefinition]:
    """Discover candidate metric definitions from DataHub's Query entities.

    Each returned MetricDefinition carries its graph provenance (dataset_urn,
    query_urn) and a populated signature. Deduped by query_urn.
    """
    dialect = dialect or settings.dialect
    # Fast path: one MCP session for all reads (avoids per-call server respawn).
    # Fallback (stub, or any client without it): per-call reads.
    if hasattr(client, "bulk_discovery"):
        search_results, per_dataset = client.bulk_discovery(keyword)
    else:
        search_results = client.search_queries(keyword)
        per_dataset = {
            urn: client.get_dataset_queries(urn)
            for urn in _ordered_dataset_urns(search_results)
        }

    return candidates_from_results(search_results, per_dataset, dialect=dialect)


def candidates_from_results(
    search_results: list[dict],
    per_dataset: dict[str, list[dict]],
    *,
    dialect: str | None = None,
) -> list[MetricDefinition]:
    """Build candidates from a pre-fetched MCP evidence bundle."""
    dialect = dialect or settings.dialect
    candidates: list[MetricDefinition] = []
    seen_queries: set[str] = set()

    for dataset_urn in _ordered_dataset_urns(search_results):
        queries = per_dataset.get(dataset_urn, [])
        for query in queries:
            query_urn = query.get("urn", "")
            props = query.get("properties") or {}
            sql = (props.get("statement") or {}).get("value") or ""
            if not sql.strip() or query_urn in seen_queries:
                continue
            seen_queries.add(query_urn)
            candidates.append(MetricDefinition(
                name=_short_name(dataset_urn),
                sql=sql,
                dialect=dialect,
                source=props.get("description") or f"DataHub query {query_urn}",
                owner=_owner_from(props.get("name", "")),
                signature=extract_signature(sql, dialect=dialect),
                dataset_urn=dataset_urn,
                query_urn=query_urn,
            ))
    return candidates
