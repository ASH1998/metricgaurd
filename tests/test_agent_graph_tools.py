"""The agent must investigate and act on DataHub graph evidence, not seed files."""

import json
from dataclasses import replace
from pathlib import Path

from metricguard.agent import tools as agent_tools
from metricguard.datahub import base as datahub_base
from metricguard.datahub.base import StubDataHubClient
from metricguard.datahub.investigation import investigate_graph
from metricguard.datahub.proposals import Proposal, ProposalStore
from metricguard.execution.base import StaticExecutor

_SEEDS = Path("seeds/metric_families/weekly_revenue")
_FINANCE_URN = "urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)"
_EXEC_URN = "urn:li:dataset:(urn:li:dataPlatform:superset,executive_kpis.revenue_tile,PROD)"
_SALES_URN = "urn:li:dataset:(urn:li:dataPlatform:superset,sales_ops.weekly_bookings,PROD)"


def _specs():
    downstream = [{
        "urn": "urn:li:dashboard:(superset,executive-kpis)",
        "type": "DASHBOARD",
        "properties": {"name": "Executive KPIs"},
    }]
    return [
        {
            "dataset_urn": _FINANCE_URN,
            "query_urn": "urn:li:query:mg_finance",
            "name": "finance-data: weekly_revenue",
            "owner": "finance-data",
            "metric_family": "weekly_revenue",
            "sql": (_SEEDS / "finance_weekly_revenue.sql").read_text(),
            "downstream": downstream,
            "upstream": [{
                "urn": "urn:li:dataset:(urn:li:dataPlatform:postgres,metric.orders,PROD)",
                "type": "DATASET",
                "properties": {"name": "orders"},
            }],
        },
        {
            "dataset_urn": _EXEC_URN,
            "query_urn": "urn:li:query:mg_exec",
            "name": "bi-team: revenue_tile",
            "owner": "bi-team",
            "metric_family": "weekly_revenue",
            "sql": (_SEEDS / "exec_dashboard_weekly_revenue.sql").read_text(),
        },
        {
            "dataset_urn": _SALES_URN,
            "query_urn": "urn:li:query:mg_salesops",
            "name": "sales-operations: weekly_bookings",
            "owner": "sales-operations",
            "metric_family": "weekly_revenue",
            "sql": (_SEEDS / "sales_ops_weekly_revenue.sql").read_text(),
        },
    ]


def _client():
    return StubDataHubClient.from_specs(_specs())


def test_investigation_combines_datahub_context_and_deterministic_conflicts():
    report = investigate_graph(_client(), keyword="*")
    assert report["source"].startswith("DataHub Agent Context Kit")
    assert report["summary"] == {
        "candidate_count": 3,
        "metric_family_count": 1,
        "conflicting_pairs": 3,
        "critical_pairs": 2,
    }
    assert len(report["clusters"][0]["conflicts"]) == 3
    finance = next(item for item in report["candidates"] if item["dataset_urn"] == _FINANCE_URN)
    assert finance["datahub_context"]["owners"] == ["finance-data"]
    assert finance["upstream_provenance"]["assets"][0]["name"] == "orders"
    assert finance["downstream_impact"]["assets"][0]["name"] == "Executive KPIs"


def test_agent_stages_full_resolution_idempotently(monkeypatch, tmp_path):
    client = _client()
    store = ProposalStore(directory=tmp_path)
    monkeypatch.setattr(datahub_base, "get_datahub_client", lambda: client)

    import metricguard.datahub.proposals as proposals_module

    monkeypatch.setattr(proposals_module, "ProposalStore", lambda: store)
    first = json.loads(agent_tools.stage_canonical_resolution(
        "weekly_revenue", _FINANCE_URN,
        "Finance excludes canceled orders and owns reporting.", "*",
    ))
    second = json.loads(agent_tools.stage_canonical_resolution(
        "weekly_revenue", _FINANCE_URN,
        "Finance excludes canceled orders and owns reporting.", "*",
    ))
    assert len(first["staged_proposal_ids"]) == 7
    assert first["status"] == "staged_for_human_approval"
    assert first["human_approval_required"] is True
    assert second["staged_proposal_ids"] == []
    assert second["status"] == "no_new_proposals"
    assert len(second["already_staged_or_executed_ids"]) == 7
    # Equivalent proposals are pending in this test store, so approval still remains.
    assert second["human_approval_required"] is True
    assert {item["status"] for item in second["existing_resolution_proposals"]} == {"pending"}

    for proposal in store.list():
        store.approve(proposal.id, client)
    third = json.loads(agent_tools.stage_canonical_resolution(
        "weekly_revenue", _FINANCE_URN,
        "Finance excludes canceled orders and owns reporting.", "*",
    ))
    assert third["human_approval_required"] is False
    assert third["next_command"] == "No approval required."
    assert {item["status"] for item in third["existing_resolution_proposals"]} == {"executed"}


def test_agent_refuses_family_name_not_governed_in_datahub(monkeypatch, tmp_path):
    client = _client()
    store = ProposalStore(directory=tmp_path)
    monkeypatch.setattr(datahub_base, "get_datahub_client", lambda: client)

    import metricguard.datahub.proposals as proposals_module

    monkeypatch.setattr(proposals_module, "ProposalStore", lambda: store)
    # Prime the graph investigation cache so DataHub family hints are attached.
    investigate_graph(client, keyword="*")
    result = json.loads(agent_tools.stage_canonical_resolution(
        "revenue_tile", _FINANCE_URN, "Incorrect family label", "*",
    ))
    assert result["no_proposals_staged"] is True
    assert result["datahub_metric_family"] == "weekly_revenue"
    assert store.list() == []


def test_decision_document_idempotency_ignores_display_wording_changes():
    common = {
        "metric": "weekly_revenue",
        "kind": "document",
        "target": "metric:weekly_revenue",
    }
    old = Proposal(**common, payload={
        "content": "owner: finance-data",
        "related_assets": [_EXEC_URN, _FINANCE_URN],
    })
    enriched = Proposal(**common, payload={
        "content": "owner: Finance Data",
        "related_assets": [_FINANCE_URN, _EXEC_URN],
    })
    assert agent_tools._proposal_identity(old) == agent_tools._proposal_identity(enriched)


def test_graph_divergence_uses_sql_fetched_from_datahub(monkeypatch):
    client = _client()
    monkeypatch.setattr(datahub_base, "get_datahub_client", lambda: client)
    specs = _specs()
    executor = StaticExecutor(responses={
        specs[0]["sql"]: [
            {"week_start": "2026-01-05", "weekly_revenue": 100.0},
            {"week_start": "2026-01-12", "weekly_revenue": 120.0},
        ],
        specs[1]["sql"]: [
            {"week_start": "2026-01-05", "weekly_revenue": 110.0},
            {"week_start": "2026-01-12", "weekly_revenue": 150.0},
        ],
    })
    monkeypatch.setattr(agent_tools, "get_executor", lambda: executor)
    report = json.loads(agent_tools.prove_graph_divergence(
        _FINANCE_URN, _EXEC_URN, "week_start", "weekly_revenue", "*",
    ))
    assert report["left_name"] == "weekly_revenue"
    assert report["right_name"] == "revenue_tile"
    assert report["max_pct_divergence"] == 25.0


def test_mcp_agent_tool_belt_excludes_seed_loader(monkeypatch):
    import metricguard.config as config_module

    graph_settings = replace(agent_tools.settings, datahub_mcp_transport="stdio")
    monkeypatch.setattr(agent_tools, "settings", graph_settings)
    monkeypatch.setattr(config_module, "settings", graph_settings)

    async def fake_mcp_tools():
        return []

    monkeypatch.setattr(
        "metricguard.datahub.mcp_client.load_datahub_mcp_tools", fake_mcp_tools,
    )
    import asyncio

    names = {tool.name for tool in asyncio.run(agent_tools.build_all_tools())}
    assert "tool_load_seed_definitions" not in names
    assert "tool_stage_writeback" not in names
    assert "tool_investigate_datahub_conflicts" in names
    assert "tool_prove_graph_divergence" in names
    assert "tool_stage_canonical_resolution" in names
    assert "tool_check_datahub_drift" in names
