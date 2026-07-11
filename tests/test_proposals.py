"""The agent's write power is staging only; execution requires human approval."""

import pytest

from metricguard.datahub.base import ApprovalRequiredError, StubDataHubClient
from metricguard.datahub.proposals import Proposal, ProposalStatus, ProposalStore


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
