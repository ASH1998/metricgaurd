import asyncio

from metricguard.agent import coordinator
from metricguard.agent.coordinator import InvestigationPlan, PlannedInvestigation


def _report():
    return {
        "clusters": [
            {
                "metric_family": "weekly_revenue",
                "members": ["finance", "executive"],
                "conflicts": [{
                    "worst_severity": "critical",
                    "diffs": [{"field": "filters"}],
                }],
            },
            {
                "metric_family": "weekly_refund_amount",
                "members": ["finance_refunds", "support_refunds"],
                "conflicts": [{
                    "worst_severity": "warning",
                    "diffs": [{"field": "null_handling"}],
                }],
            },
            {
                "metric_family": "monthly_signups",
                "members": ["signups"],
                "conflicts": [],
            },
        ],
    }


class _StructuredPlanner:
    async def ainvoke(self, messages):
        return InvestigationPlan(
            rationale="Both conflicting families are relevant to the broad request.",
            investigations=[
                PlannedInvestigation(
                    metric_family="weekly_revenue", reason="Critical conflict", priority=5,
                ),
                PlannedInvestigation(
                    metric_family="weekly_refund_amount", reason="Material conflict", priority=4,
                ),
                PlannedInvestigation(
                    metric_family="invented_family", reason="Invalid", priority=5,
                ),
            ],
        )


class _Model:
    def with_structured_output(self, schema):
        assert schema is InvestigationPlan
        return _StructuredPlanner()


def test_agent_planner_decides_how_many_valid_family_runs_to_open(monkeypatch):
    monkeypatch.setattr(coordinator, "get_llm", lambda: _Model())

    plan, source = asyncio.run(coordinator._choose_investigations(
        "find conflicting reports", _report(),
    ))

    assert source == "llm"
    assert [item.metric_family for item in plan.investigations] == [
        "weekly_revenue", "weekly_refund_amount",
    ]


def test_planner_falls_back_to_one_bounded_run_per_conflicting_family(monkeypatch):
    class BrokenModel:
        def with_structured_output(self, schema):
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr(coordinator, "get_llm", lambda: BrokenModel())

    plan, source = asyncio.run(coordinator._choose_investigations(
        "scan the organization", _report(),
    ))

    assert source == "deterministic_fallback"
    assert [item.metric_family for item in plan.investigations] == [
        "weekly_revenue", "weekly_refund_amount",
    ]
