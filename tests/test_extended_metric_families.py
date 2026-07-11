import json
from pathlib import Path

from metricguard.clustering.grouper import cluster_candidates
from metricguard.comparison.diff import compare_signatures
from metricguard.models import MetricDefinition, Severity
from metricguard.signature.extractor import extract_signature

SEEDS = Path("seeds/metric_families")


def _load_family(family: str) -> list[MetricDefinition]:
    root = SEEDS / family
    manifest = json.loads((root / "manifest.json").read_text())
    definitions = []
    for item in manifest["definitions"]:
        sql = (root / item["file"]).read_text()
        definitions.append(MetricDefinition(
            name=item["name"],
            sql=sql,
            family_hint=family,
            signature=extract_signature(sql),
        ))
    return definitions


def test_order_volume_family_is_executable_and_conflicts_on_policy_filters():
    definitions = _load_family("weekly_order_volume")
    assert all(item.signature.aggregation.function == "COUNT" for item in definitions)
    assert all(item.signature.aggregation.argument == "order_id" for item in definitions)
    assert all(item.signature.grain == "week" for item in definitions)
    assert all(item.signature.source_population == ["metric.orders"] for item in definitions)

    fulfillment, sales, executive = definitions
    assert fulfillment.signature.filters != sales.signature.filters
    report = compare_signatures(fulfillment.signature, executive.signature)
    assert report.is_conflict
    assert report.worst_severity == Severity.HIGH
    assert {diff.field for diff in report.diffs} == {"filters"}


def test_refund_family_is_executable_and_conflicts_on_policy_filters():
    definitions = _load_family("weekly_refund_amount")
    assert all(item.signature.aggregation.function == "SUM" for item in definitions)
    assert all(item.signature.aggregation.argument == "refund_amount" for item in definitions)
    assert all(item.signature.grain == "week" for item in definitions)
    assert all(item.signature.source_population == ["metric.returns"] for item in definitions)

    finance, support, risk = definitions
    assert finance.signature.filters == []
    assert support.signature.filters != risk.signature.filters
    assert compare_signatures(finance.signature, support.signature).is_conflict


def test_full_seed_catalog_forms_four_separate_conflict_families():
    definitions = [
        item
        for family in (
            "weekly_active_users",
            "weekly_order_volume",
            "weekly_refund_amount",
            "weekly_revenue",
        )
        for item in _load_family(family)
    ]

    clusters = cluster_candidates(definitions)

    assert {cluster.metric_family for cluster in clusters} == {
        "weekly_active_users",
        "weekly_order_volume",
        "weekly_refund_amount",
        "weekly_revenue",
    }
    assert all(len(cluster.members) == 3 for cluster in clusters)


def test_governed_family_boundary_prevents_shared_source_cross_cluster():
    revenue = _load_family("weekly_revenue")[1]
    order_volume = _load_family("weekly_order_volume")[0]
    # These share metric.orders, weekly grain, and similar names. Without the
    # governed negative boundary they can incorrectly union transitively.
    assert cluster_candidates([revenue, order_volume]) == []
