"""Write-back builders produce payloads that match the real MCP mutation tools,
and the full stage -> approve path routes through the approval gate."""

import pytest

from metricguard.agent.tools import _is_mutation
from metricguard.datahub.base import StubDataHubClient
from metricguard.datahub.proposals import ProposalStatus, ProposalStore
from metricguard.datahub.writeback import (
    CANONICAL_TAG,
    DIVERGENT_TAG,
    SIGNATURE_PROP_PREFIX,
    build_canonical_writeback,
    structured_properties_proposal,
)
from metricguard.models import Aggregation, MetricDefinition, SemanticSignature


def _defs():
    canonical = MetricDefinition(
        name="weekly_revenue", sql="SELECT 1", owner="finance-data",
        dataset_urn="urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)",
        query_urn="urn:li:query:mg_finance",
    )
    divergent = [MetricDefinition(
        name="revenue_tile", sql="SELECT 2", owner="bi-team",
        dataset_urn="urn:li:dataset:(urn:li:dataPlatform:superset,executive_kpis.revenue_tile,PROD)",
        query_urn="urn:li:query:mg_exec",
    )]
    return canonical, divergent


def test_builder_payloads_match_tool_args():
    canonical, divergent = _defs()
    proposals = build_canonical_writeback("weekly_revenue", canonical, divergent)
    kinds = [p.kind for p in proposals]
    # 1 document + 1 canonical tag + (1 divergent tag + 1 redirect) per divergent
    assert kinds == ["document", "tag", "tag", "description"]

    doc = proposals[0]
    assert set(doc.payload) == {"document_type", "title", "content", "related_assets"}
    assert doc.payload["document_type"] == "Decision"
    assert canonical.dataset_urn in doc.payload["related_assets"]
    assert divergent[0].dataset_urn in doc.payload["related_assets"]

    canonical_tag = proposals[1]
    assert canonical_tag.payload == {
        "tag_urns": [CANONICAL_TAG], "entity_urns": [canonical.dataset_urn]}

    divergent_tag = proposals[2]
    assert divergent_tag.payload["tag_urns"] == [DIVERGENT_TAG]

    redirect = proposals[3]
    assert set(redirect.payload) == {"entity_urn", "operation", "description"}
    assert redirect.payload["operation"] == "append"


def test_structured_properties_payload_from_signature():
    canonical, _ = _defs()
    canonical.signature = SemanticSignature(
        aggregation=Aggregation(function="SUM", argument="total_amount"),
        entity="total_amount", grain="week",
        filters=["orders.order_status <> 'canceled'"],
        source_population=["metric.orders"],
    )
    p = structured_properties_proposal("weekly_revenue", canonical)
    assert p.kind == "structured_property"
    assert set(p.payload) == {"property_values", "entity_urns"}
    pv = p.payload["property_values"]
    assert pv[f"{SIGNATURE_PROP_PREFIX}aggregation"] == ["SUM(total_amount)"]
    assert pv[f"{SIGNATURE_PROP_PREFIX}grain"] == ["week"]
    assert pv[f"{SIGNATURE_PROP_PREFIX}filters"] == ["orders.order_status <> 'canceled'"]
    # empty fields (timezone, null_handling, deduplication) are omitted
    assert f"{SIGNATURE_PROP_PREFIX}timezone" not in pv
    assert p.payload["entity_urns"] == [canonical.dataset_urn]


def test_full_set_includes_structured_property_when_signature_present():
    canonical, divergent = _defs()
    canonical.signature = SemanticSignature(entity="total_amount", grain="week")
    kinds = [p.kind for p in build_canonical_writeback("weekly_revenue", canonical, divergent)]
    assert kinds == ["document", "structured_property", "tag", "tag", "description"]


def test_evidence_snapshot_stamped_on_all_proposals():
    """Every proposal in a resolution carries the canonical's staging-time
    signature so `proposals approve` can re-prove it against DataHub."""
    canonical, divergent = _defs()
    canonical.signature = SemanticSignature(
        aggregation=Aggregation(function="SUM", argument="total_amount"),
        entity="total_amount", grain="week",
        source_population=["metric.orders"],
    )
    for p in build_canonical_writeback("weekly_revenue", canonical, divergent):
        assert p.evidence["query_urn"] == canonical.query_urn
        assert p.evidence["dataset_urn"] == canonical.dataset_urn
        assert p.evidence["signature"]["grain"] == "week"


def test_no_evidence_snapshot_without_signature():
    """No signature at staging time -> nothing to re-verify later (honest gap)."""
    canonical, divergent = _defs()  # signature is None
    for p in build_canonical_writeback("weekly_revenue", canonical, divergent):
        assert p.evidence == {}


def test_decision_document_preserves_agent_evidence_for_datahub():
    canonical, divergent = _defs()
    (document, *_) = build_canonical_writeback(
        "weekly_revenue", canonical, divergent,
        evidence_summary="Warehouse proof: executive tile overstates revenue by 13.07% on average.",
    )
    assert "## Evidence behind this decision" in document.payload["content"]
    assert "13.07%" in document.payload["content"]


def test_writeback_requires_graph_provenance():
    canonical, divergent = _defs()
    canonical.dataset_urn = ""  # e.g. a seed candidate, no URN
    with pytest.raises(ValueError, match="provenance"):
        build_canonical_writeback("weekly_revenue", canonical, divergent)


def test_stage_then_approve_routes_through_gate(tmp_path):
    canonical, divergent = _defs()
    store = ProposalStore(directory=tmp_path)
    for p in build_canonical_writeback("weekly_revenue", canonical, divergent):
        store.stage(p)

    client = StubDataHubClient()
    for p in store.list(status=ProposalStatus.PENDING):
        store.approve(p.id, client)

    assert len(client.write_log) == 4
    assert [a.kind for a in client.write_log] == ["document", "tag", "tag", "description"]
    assert all(a.executed for a in client.write_log)


def test_mcp_call_raises_on_tool_error(monkeypatch):
    """A failed MCP write returns an 'Error calling tool' string, not an exception.
    _call must turn that into a raise so the approval gate never marks it executed."""
    from metricguard.datahub.mcp_client import MCPDataHubClient

    class _FakeTool:
        name = "add_tags"
        async def ainvoke(self, kwargs):
            return [{"type": "text", "text": "Error calling tool 'add_tags': Urn does not exist."}]

    client = MCPDataHubClient()
    client._tools = {"add_tags": _FakeTool()}
    with pytest.raises(RuntimeError, match="add_tags"):
        client._call("add_tags", tag_urns=["urn:li:tag:x"], entity_urns=["urn:li:dataset:x"])


def test_mutation_filter_catches_save_and_remove():
    # regression: save_document / remove_* must not leak to the agent's tool belt
    for name in ("save_document", "remove_tags", "remove_terms", "add_tags", "update_description"):
        assert _is_mutation(name), name
    for name in ("search", "get_dataset_queries", "get_lineage", "get_entities"):
        assert not _is_mutation(name), name
