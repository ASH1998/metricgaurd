"""Signature + conflict correctness on the weekly_revenue seed family.

These definitions execute against the fiction-retail warehouse (metric schema),
so unlike the WAU family they also back the divergence demo. The extractor
must recover the rigged dimensions: status filtering, source population
(orders vs order_items join), and the expression measure.
"""

from pathlib import Path

import pytest

from metricguard.comparison.diff import compare_signatures
from metricguard.models import Severity
from metricguard.signature.extractor import extract_signature

SEEDS = Path(__file__).parent.parent / "seeds" / "metric_families" / "weekly_revenue"


@pytest.fixture()
def exec_sql() -> str:
    return (SEEDS / "exec_dashboard_weekly_revenue.sql").read_text()


@pytest.fixture()
def finance_sql() -> str:
    return (SEEDS / "finance_weekly_revenue.sql").read_text()


@pytest.fixture()
def sales_ops_sql() -> str:
    return (SEEDS / "sales_ops_weekly_revenue.sql").read_text()


def test_exec_signature(exec_sql):
    sig = extract_signature(exec_sql)
    assert sig.aggregation.function == "SUM"
    assert sig.aggregation.argument == "total_amount"
    assert sig.grain == "week"
    assert sig.filters == []                       # the rigged everything-counts definition
    assert sig.source_population == ["metric.orders"]


def test_finance_signature(finance_sql):
    sig = extract_signature(finance_sql)
    assert len(sig.filters) == 1
    for status in ("canceled", "returned", "disputed"):
        assert status in sig.filters[0]


def test_sales_ops_signature(sales_ops_sql):
    """Join + expression measure — the hardest of the seeded definitions."""
    sig = extract_signature(sales_ops_sql)
    assert sig.aggregation.function == "SUM"
    # the full formula must survive; collapsing to one column would make
    # different measures look identical
    for token in ("quantity", "unit_price", "discount_pct"):
        assert token in sig.aggregation.argument
    assert sig.source_population == ["metric.order_items", "metric.orders"]
    # JOIN ... ON is population definition, not a filter
    assert sig.filters == ["orders.order_status <> 'canceled'"]


def test_exec_vs_finance_is_filter_conflict(exec_sql, finance_sql):
    report = compare_signatures(extract_signature(exec_sql), extract_signature(finance_sql))
    assert report.is_conflict
    assert {d.field for d in report.diffs} == {"filters"}
    assert report.worst_severity == Severity.HIGH


def test_exec_vs_sales_ops_is_critical(exec_sql, sales_ops_sql):
    report = compare_signatures(extract_signature(exec_sql), extract_signature(sales_ops_sql))
    fields = {d.field for d in report.diffs}
    assert "aggregation" in fields
    assert "source_population" in fields
    assert report.worst_severity == Severity.CRITICAL
