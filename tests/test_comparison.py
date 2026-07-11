"""Conflict classification on the seeded family + guard drift behavior."""

from metricguard.comparison.diff import compare_signatures
from metricguard.guard.contracts import ContractStore
from metricguard.models import DriftVerdict, Severity
from metricguard.signature.extractor import extract_signature


def test_marketing_vs_product_conflicts(marketing_sql, product_sql):
    report = compare_signatures(
        extract_signature(marketing_sql), extract_signature(product_sql),
        left_name="marketing", right_name="product",
    )
    assert report.is_conflict
    fields = {d.field for d in report.diffs}
    assert "timezone" in fields          # UTC vs America/New_York
    assert "filters" in fields           # anonymous inclusion + event filtering


def test_marketing_vs_finance_is_critical(marketing_sql, finance_sql):
    report = compare_signatures(
        extract_signature(marketing_sql), extract_signature(finance_sql),
    )
    assert report.worst_severity == Severity.CRITICAL
    fields = {d.field for d in report.diffs}
    assert "aggregation" in fields       # COUNT DISTINCT vs COUNT
    assert "source_population" in fields # events vs billable_events


def test_identical_definitions_no_conflict(marketing_sql):
    report = compare_signatures(
        extract_signature(marketing_sql), extract_signature(marketing_sql),
    )
    assert not report.is_conflict
    assert report.diffs == []


class TestGuardDrift:
    def test_cosmetic_change_is_ok(self, tmp_path, marketing_sql):
        store = ContractStore(directory=tmp_path)
        store.approve("wau", marketing_sql, approved_by="test")

        cosmetic = marketing_sql.replace("e.user_id", "e .user_id").replace("    ", "  ")
        report = store.check_drift("wau", cosmetic)
        assert report.verdict == DriftVerdict.OK

    def test_semantic_change_is_drift(self, tmp_path, marketing_sql):
        store = ContractStore(directory=tmp_path)
        store.approve("wau", marketing_sql, approved_by="test")

        # someone "just" drops the session_start events — a semantic break
        drifted = marketing_sql.replace(
            "('page_view', 'click', 'session_start')", "('page_view', 'click')"
        )
        report = store.check_drift("wau", drifted)
        assert report.verdict == DriftVerdict.DRIFT
        assert any(d.field == "filters" for d in report.diffs)

    def test_no_contract(self, tmp_path, marketing_sql):
        store = ContractStore(directory=tmp_path)
        report = store.check_drift("unknown_metric", marketing_sql)
        assert report.verdict == DriftVerdict.NO_CONTRACT
