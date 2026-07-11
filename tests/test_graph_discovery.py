"""Graph-native discovery: candidates come from DataHub, families are recovered
from *semantic signals* — not from names (the names deliberately disagree).

Exercises the same code path production uses, via the in-memory stub.
"""

from pathlib import Path

from metricguard.clustering.grouper import cluster_candidates
from metricguard.datahub.base import StubDataHubClient
from metricguard.datahub.discovery import candidates_from_graph

_SEEDS = Path("seeds/metric_families/weekly_revenue")

# Three teams, three DIFFERENT dataset names/owners, same underlying metric.
_SPECS = [
    {
        "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)",
        "query_urn": "urn:li:query:mg_finance",
        "name": "finance-data: weekly_revenue",
        "sql": (_SEEDS / "finance_weekly_revenue.sql").read_text(),
    },
    {
        "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:superset,executive_kpis.revenue_tile,PROD)",
        "query_urn": "urn:li:query:mg_exec",
        "name": "bi-team: weekly_revenue",
        "sql": (_SEEDS / "exec_dashboard_weekly_revenue.sql").read_text(),
    },
    {
        "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:superset,sales_ops.weekly_bookings,PROD)",
        "query_urn": "urn:li:query:mg_salesops",
        "name": "sales-operations: weekly_revenue",
        "sql": (_SEEDS / "sales_ops_weekly_revenue.sql").read_text(),
    },
]


def _client():
    return StubDataHubClient.from_specs(_SPECS)


def test_candidates_carry_signature_and_provenance():
    candidates = candidates_from_graph(_client(), keyword="*")
    assert len(candidates) == 3
    for c in candidates:
        assert c.signature is not None            # extractor ran
        assert c.dataset_urn and c.query_urn       # provenance for write-back
        assert c.owner                             # parsed from '<owner>: <metric>'
        assert c.family_hint == "weekly_revenue"  # explicit label, not inferred from asset name
    # names are the org's inconsistent labels, not a shared family name
    assert {c.name for c in candidates} == {"weekly_revenue", "revenue_tile", "weekly_bookings"}


def test_clustering_recovers_family_from_semantics_despite_different_names():
    candidates = candidates_from_graph(_client(), keyword="*")
    clusters = cluster_candidates(candidates)
    assert len(clusters) == 1
    (family,) = clusters
    assert set(family.members) == {"weekly_revenue", "revenue_tile", "weekly_bookings"}


def test_deduped_by_query_urn():
    # same query surfaced via two datasets must not double-count
    client = StubDataHubClient.from_specs(_SPECS + [{**_SPECS[0],
        "dataset_urn": "urn:li:dataset:(urn:li:dataPlatform:dbt,other.copy,PROD)"}])
    names = [c.query_urn for c in candidates_from_graph(client, keyword="*")]
    assert len(names) == len(set(names)) == 3
