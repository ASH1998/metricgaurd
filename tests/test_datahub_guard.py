from pathlib import Path

from metricguard.datahub.base import StubDataHubClient
from metricguard.datahub.writeback import CANONICAL_TAG, SIGNATURE_PROP_PREFIX
from metricguard.guard.datahub_contracts import check_datahub_drift, signature_from_datahub_entity
from metricguard.models import DriftVerdict

_SEEDS = Path("seeds/metric_families/weekly_revenue")
_URN = "urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)"


def _entity(canonical: bool = True):
    values = {
        "aggregation": ["SUM(total_amount)"],
        "entity": ["total_amount"],
        "grain": ["week"],
        "filters": ["NOT orders.order_status IN ('canceled', 'returned', 'disputed')"],
        "source_population": ["metric.orders"],
    }
    return {
        "urn": _URN,
        "properties": {
            "name": "weekly_revenue",
            "customProperties": [{"key": "metric_family", "value": "weekly_revenue"}],
        },
        "tags": {"tags": [{"tag": {"urn": CANONICAL_TAG}}]} if canonical else {"tags": []},
        "structuredProperties": {"properties": [
            {
                "structuredProperty": {
                    "urn": f"{SIGNATURE_PROP_PREFIX}{field}",
                    "definition": {"qualifiedName": f"metricguard_{field}"},
                },
                "values": [{"stringValue": value} for value in field_values],
            }
            for field, field_values in values.items()
        ]},
    }


class _EntityClient(StubDataHubClient):
    def __init__(self, entity):
        super().__init__()
        self.entity = entity

    def get_entities(self, urns):
        return self.entity if isinstance(urns, str) else [self.entity]


def test_rehydrates_signature_from_datahub_structured_properties():
    signature = signature_from_datahub_entity(_entity())
    assert signature.aggregation.render() == "SUM(total_amount)"
    assert signature.grain == "week"
    assert signature.filters == ["NOT orders.order_status IN ('canceled', 'returned', 'disputed')"]


def test_graph_contract_accepts_canonical_and_rejects_divergent_sql():
    client = _EntityClient(_entity())
    canonical_sql = (_SEEDS / "finance_weekly_revenue.sql").read_text()
    divergent_sql = (_SEEDS / "exec_dashboard_weekly_revenue.sql").read_text()
    assert check_datahub_drift(client, _URN, canonical_sql).verdict == DriftVerdict.OK
    report = check_datahub_drift(client, _URN, divergent_sql)
    assert report.verdict == DriftVerdict.DRIFT
    assert [diff.field for diff in report.diffs] == ["filters"]


def test_graph_contract_requires_canonical_tag():
    report = check_datahub_drift(
        _EntityClient(_entity(canonical=False)), _URN,
        (_SEEDS / "finance_weekly_revenue.sql").read_text(),
    )
    assert report.verdict == DriftVerdict.NO_CONTRACT
