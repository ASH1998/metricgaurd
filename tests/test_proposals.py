"""The agent's write power is staging only; execution requires human approval."""

import pytest

from metricguard.datahub.base import ApprovalRequiredError, StubDataHubClient
from metricguard.datahub.proposals import (
    Proposal,
    ProposalStatus,
    ProposalStore,
    StaleEvidenceError,
)
from metricguard.signature.extractor import extract_signature


@pytest.fixture()
def store(tmp_path):
    return ProposalStore(directory=tmp_path)


def make_proposal() -> Proposal:
    return Proposal(
        metric="weekly_active_users",
        kind="tag",
        target="urn:li:dataset:(urn:li:dataPlatform:postgres,demo.marketing_wau,PROD)",
        payload={"tag": "divergent"},
        rationale="Disagrees with the approved canonical on timezone and filters.",
    )


def test_stage_and_list(store):
    p = store.stage(make_proposal())
    pending = store.list(status=ProposalStatus.PENDING)
    assert [x.id for x in pending] == [p.id]


def test_approve_executes_through_gated_write(store):
    client = StubDataHubClient()
    p = store.stage(make_proposal())

    executed = store.approve(p.id, client)
    assert executed.status == ProposalStatus.EXECUTED
    assert len(client.write_log) == 1
    assert client.write_log[0].kind == "tag"
    assert client.write_log[0].executed


def test_double_approve_rejected(store):
    client = StubDataHubClient()
    p = store.stage(make_proposal())
    store.approve(p.id, client)
    with pytest.raises(ValueError):
        store.approve(p.id, client)
    assert len(client.write_log) == 1


def test_direct_unapproved_write_still_raises():
    """The underlying gate holds even if someone bypasses the store."""
    client = StubDataHubClient()
    with pytest.raises(ApprovalRequiredError):
        client.write(make_proposal().to_action(), approved=False)


def test_reject_keeps_audit_trail(store):
    p = store.stage(make_proposal())
    store.reject(p.id)
    assert store.get(p.id).status == ProposalStatus.REJECTED
    assert store.list(status=ProposalStatus.PENDING) == []


# ---------------------------------------------------------------------------
# Approval-time evidence re-verification — the gate re-proves before it writes.
# ---------------------------------------------------------------------------

_DATASET = "urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)"
_QUERY = "urn:li:query:mg_finance_weekly_revenue"

_CANONICAL_SQL = """
SELECT DATE_TRUNC('week', created_at) AS week_start,
       SUM(total_amount) AS weekly_revenue
FROM orders
WHERE NOT order_status IN ('canceled', 'returned')
GROUP BY 1
"""

# Same semantics, different text: casing, alias, formatting.
_COSMETIC_SQL = """
select date_trunc('week', created_at) as wk,
       sum(total_amount) as rev
from orders
where not order_status in ('canceled', 'returned')
group by 1
"""

# Semantic change: the status filter is gone.
_CHANGED_SQL = """
SELECT DATE_TRUNC('week', created_at) AS week_start,
       SUM(total_amount) AS weekly_revenue
FROM orders
GROUP BY 1
"""


def evidence_proposal(staged_sql: str) -> Proposal:
    return Proposal(
        metric="weekly_revenue",
        kind="tag",
        target=_DATASET,
        payload={"tag_urns": ["urn:li:tag:metricguard_canonical"], "entity_urns": [_DATASET]},
        rationale="Warehouse-proven canonical choice.",
        evidence={
            "canonical_name": "weekly_revenue",
            "query_urn": _QUERY,
            "dataset_urn": _DATASET,
            "dialect": "postgres",
            "signature": extract_signature(staged_sql, dialect="postgres").model_dump(mode="json"),
        },
    )


def client_serving(current_sql: str) -> StubDataHubClient:
    return StubDataHubClient.from_specs([{
        "dataset_urn": _DATASET, "query_urn": _QUERY,
        "name": "finance:weekly_revenue", "sql": current_sql,
    }])


def test_approve_reverifies_unchanged_evidence(store):
    p = store.stage(evidence_proposal(_CANONICAL_SQL))
    client = client_serving(_CANONICAL_SQL)
    assert store.verify_evidence(p, client) == "verified"
    assert store.approve(p.id, client).status == ProposalStatus.EXECUTED


def test_cosmetic_edit_still_approves(store):
    """Signature equality is the check, not text equality."""
    p = store.stage(evidence_proposal(_CANONICAL_SQL))
    client = client_serving(_COSMETIC_SQL)
    assert store.verify_evidence(p, client) == "verified"
    assert store.approve(p.id, client).status == ProposalStatus.EXECUTED


def test_semantic_change_blocks_approval(store):
    p = store.stage(evidence_proposal(_CANONICAL_SQL))
    client = client_serving(_CHANGED_SQL)
    with pytest.raises(StaleEvidenceError, match="filters"):
        store.approve(p.id, client)
    # nothing was written; the proposal stays pending for re-investigation
    assert client.write_log == []
    assert store.get(p.id).status == ProposalStatus.PENDING


def test_deleted_definition_blocks_approval(store):
    p = store.stage(evidence_proposal(_CANONICAL_SQL))
    client = StubDataHubClient()  # the query no longer exists in DataHub
    with pytest.raises(StaleEvidenceError, match="re-read"):
        store.approve(p.id, client)
    assert store.get(p.id).status == ProposalStatus.PENDING


def test_legacy_proposal_without_evidence_skips_verification(store):
    p = store.stage(make_proposal())  # no evidence snapshot
    client = StubDataHubClient()
    assert store.verify_evidence(p, client) == "unverified"
    assert store.approve(p.id, client).status == ProposalStatus.EXECUTED


def test_skip_verification_flag_path(store):
    """approve(verify=False) is the explicit human override the CLI exposes."""
    p = store.stage(evidence_proposal(_CANONICAL_SQL))
    client = client_serving(_CHANGED_SQL)
    assert store.approve(p.id, client, verify=False).status == ProposalStatus.EXECUTED
