"""Evidence bundle for the graph-native MetricGuard investigation agent.

DataHub supplies organizational context; deterministic MetricGuard engines turn
the SQL into signatures, clusters, and conflict reports.  The LLM decides what
to investigate and what resolution to propose, but never invents these facts.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from metricguard.clustering.grouper import cluster_candidates
from metricguard.comparison.diff import compare_signatures
from metricguard.datahub.base import DataHubClient
from metricguard.datahub.discovery import candidates_from_graph, candidates_from_results
from metricguard.models import MetricDefinition

_INVESTIGATED_CANDIDATES: dict[tuple[int, str], list[MetricDefinition]] = {}


def investigate_graph(client: DataHubClient, keyword: str = "*") -> dict[str, Any]:
    """Discover conflicts and enrich them with DataHub ownership and impact context."""
    if hasattr(client, "bulk_investigation"):
        search, queries, entities, lineage = client.bulk_investigation(keyword)
        candidates = candidates_from_results(search, queries)
    else:
        candidates = candidates_from_graph(client, keyword=keyword)
        urns = [candidate.dataset_urn for candidate in candidates]
        raw_entities = client.get_entities(urns) if urns else []
        entities = raw_entities if isinstance(raw_entities, list) else [raw_entities]
        lineage = {
            urn: {
                "upstream": client.get_lineage(
                    urn, upstream=True, max_hops=3, max_results=30,
                ),
                "downstream": client.get_lineage(
                    urn, upstream=False, max_hops=3, max_results=30,
                ),
            }
            for urn in urns
        }

    entity_by_urn = {
        entity.get("urn", ""): entity for entity in entities if isinstance(entity, dict)
    }
    for candidate in candidates:
        entity = entity_by_urn.get(candidate.dataset_urn, {})
        owner = _owners(entity)
        if owner:
            candidate.owner = ", ".join(owner)
        candidate.family_hint = _custom_properties(entity).get("metric_family", "")
    # Agent runs are short-lived. Reuse the exact SQL evidence for follow-up
    # divergence and staging tools instead of respawning MCP for every action.
    _INVESTIGATED_CANDIDATES[(id(client), keyword)] = candidates

    clusters = cluster_candidates(candidates)
    candidates_by_name: dict[str, list[MetricDefinition]] = {}
    for candidate in candidates:
        candidates_by_name.setdefault(candidate.name, []).append(candidate)

    cluster_payloads = []
    conflict_count = 0
    critical_count = 0
    for cluster in clusters:
        members = [
            candidate
            for name in cluster.members
            for candidate in candidates_by_name.get(name, [])
        ]
        conflicts = []
        for left, right in combinations(members, 2):
            report = compare_signatures(
                left.signature, right.signature,
                left_name=left.name, right_name=right.name,
            )
            if not report.is_conflict:
                continue
            conflict_count += 1
            if report.worst_severity.value == "critical":
                critical_count += 1
            conflicts.append({
                "left": _identity(left),
                "right": _identity(right),
                "worst_severity": report.worst_severity.value,
                "diffs": [diff.model_dump(mode="json") for diff in report.diffs],
            })
        cluster_payloads.append({
            **cluster.model_dump(mode="json"),
            "member_dataset_urns": [member.dataset_urn for member in members],
            "conflicts": conflicts,
        })

    candidate_payloads = []
    for candidate in candidates:
        entity = entity_by_urn.get(candidate.dataset_urn, {})
        candidate_payloads.append({
            **_identity(candidate),
            "source": candidate.source,
            "signature": candidate.signature.model_dump(mode="json"),
            "datahub_context": _entity_context(entity),
            "upstream_provenance": _lineage_context(
                lineage.get(candidate.dataset_urn, {}).get("upstream", {}), "upstream",
            ),
            "downstream_impact": _lineage_context(
                lineage.get(candidate.dataset_urn, {}).get("downstream", {}), "downstream",
            ),
        })

    return {
        "source": "DataHub Agent Context Kit (official MCP server)",
        "search_query": keyword,
        "summary": {
            "candidate_count": len(candidates),
            "metric_family_count": len(clusters),
            "conflicting_pairs": conflict_count,
            "critical_pairs": critical_count,
        },
        "candidates": candidate_payloads,
        "clusters": cluster_payloads,
    }


def investigated_candidates(client: DataHubClient, keyword: str) -> list[MetricDefinition]:
    """Return candidates from this process's investigation, or fetch them once."""
    key = (id(client), keyword)
    if key not in _INVESTIGATED_CANDIDATES:
        investigate_graph(client, keyword=keyword)
    return _INVESTIGATED_CANDIDATES[key]


def _identity(candidate: MetricDefinition) -> dict[str, str]:
    return {
        "name": candidate.name,
        "metric_family": candidate.family_hint,
        "owner": candidate.owner,
        "dataset_urn": candidate.dataset_urn,
        "query_urn": candidate.query_urn,
    }


def _owners(entity: dict[str, Any]) -> list[str]:
    owners = []
    for item in (entity.get("ownership") or {}).get("owners", []):
        owner = item.get("owner") or {}
        properties = owner.get("properties") or owner.get("info") or {}
        label = (
            properties.get("displayName") or properties.get("name")
            or owner.get("name") or owner.get("urn", "").rsplit(":", 1)[-1]
        )
        if label and label not in owners:
            owners.append(label)
    if owners:
        return owners
    custom = _custom_properties(entity)
    return [custom["owner_team"]] if custom.get("owner_team") else []


def _custom_properties(entity: dict[str, Any]) -> dict[str, str]:
    custom = (entity.get("properties") or {}).get("customProperties") or {}
    if isinstance(custom, list):
        return {
            item.get("key", ""): item.get("value", "")
            for item in custom if item.get("key")
        }
    return custom


def _entity_context(entity: dict[str, Any]) -> dict[str, Any]:
    props = entity.get("properties") or {}
    platform = entity.get("platform") or {}
    domain = entity.get("domain") or {}
    domain_entity = domain.get("domain") or {}
    return {
        "name": props.get("name") or entity.get("name", ""),
        "description": props.get("description", ""),
        "owners": _owners(entity),
        "platform": (platform.get("properties") or {}).get("displayName")
        or platform.get("name", ""),
        "subtypes": (entity.get("subTypes") or {}).get("typeNames", []),
        "domain": (domain_entity.get("properties") or {}).get("name")
        or domain_entity.get("name", ""),
        "tags": [
            ((item.get("tag") or {}).get("properties") or {}).get("name", "")
            for item in (entity.get("tags") or {}).get("tags", [])
        ],
    }


def _lineage_context(lineage: dict[str, Any], direction: str) -> dict[str, Any]:
    plural = "upstreams" if direction == "upstream" else "downstreams"
    block = lineage.get(plural) or lineage.get(direction) or {}
    results = block.get("searchResults", []) if isinstance(block, dict) else []
    assets = []
    for result in results:
        entity = result.get("entity") or {}
        props = entity.get("properties") or {}
        assets.append({
            "urn": entity.get("urn", ""),
            "name": props.get("name") or entity.get("name", ""),
            "type": entity.get("type", ""),
            "degree": result.get("degree"),
        })
    return {"count": len(assets), "assets": assets}
