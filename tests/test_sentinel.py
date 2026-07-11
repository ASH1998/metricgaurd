import json

from metricguard.agent.loop import AgentExecution
from metricguard.agent.runs import (
    AgentRunStore,
    AutonomousOutcome,
    RunOrigin,
)
from metricguard.datahub.base import StubDataHubClient
from metricguard.models import MetricDefinition
from metricguard.sentinel import SentinelStateStore, classify_outcome, scan_once
from metricguard.signature.extractor import extract_signature


def _candidate(sql: str, query_urn: str = "urn:li:query:revenue") -> MetricDefinition:
    return MetricDefinition(
        name="weekly_revenue",
        sql=sql,
        dataset_urn="urn:li:dataset:(urn:li:dataPlatform:dbt,marts.weekly_revenue,PROD)",
        query_urn=query_urn,
        signature=extract_signature(sql),
    )


def _patch_candidates(monkeypatch, candidates):
    monkeypatch.setattr(
        "metricguard.sentinel.candidates_from_graph",
        lambda client, keyword: candidates,
    )


def test_first_scan_creates_baseline_without_running_agent(monkeypatch, tmp_path):
    _patch_candidates(monkeypatch, [_candidate("SELECT SUM(amount) FROM orders")])
    called = False

    def runner(*args):
        nonlocal called
        called = True

    result = scan_once(
        client=StubDataHubClient(),
        state_store=SentinelStateStore(tmp_path / "state.json"),
        run_store=AgentRunStore(tmp_path / "runs"),
        runner=runner,
    )

    assert result.status == "baseline_created"
    assert result.observed == 1
    assert called is False


def test_changing_search_scope_rebaselines_instead_of_raising_false_changes(
    monkeypatch, tmp_path,
):
    state = SentinelStateStore(tmp_path / "state.json")
    runs = AgentRunStore(tmp_path / "runs")
    _patch_candidates(monkeypatch, [_candidate("SELECT SUM(amount) FROM orders")])
    scan_once(
        keyword="revenue", client=StubDataHubClient(), state_store=state, run_store=runs,
    )

    result = scan_once(
        keyword="wau", client=StubDataHubClient(), state_store=state, run_store=runs,
    )

    assert result.status == "baseline_created"
    assert runs.list() == []


def test_cosmetic_sql_edit_is_dismissed_with_deterministic_evidence(monkeypatch, tmp_path):
    state = SentinelStateStore(tmp_path / "state.json")
    runs = AgentRunStore(tmp_path / "runs")
    _patch_candidates(monkeypatch, [_candidate("SELECT SUM(amount) FROM orders")])
    scan_once(client=StubDataHubClient(), state_store=state, run_store=runs)

    _patch_candidates(monkeypatch, [_candidate("select sum(amount)\nfrom orders")])
    result = scan_once(client=StubDataHubClient(), state_store=state, run_store=runs)

    run = runs.get(result.run_id)
    assert result.status == "dismissed"
    assert result.outcome == AutonomousOutcome.DISMISSED_WITH_EVIDENCE
    assert run.origin == RunOrigin.SENTINEL
    assert run.tool_traces[0].name == "sentinel_change_detection"
    assert "dismissed_as_cosmetic" in run.tool_traces[0].result


def test_semantic_change_opens_agent_run_and_records_outcome(monkeypatch, tmp_path):
    state = SentinelStateStore(tmp_path / "state.json")
    runs = AgentRunStore(tmp_path / "runs")
    _patch_candidates(monkeypatch, [_candidate("SELECT SUM(amount) FROM orders")])
    scan_once(client=StubDataHubClient(), state_store=state, run_store=runs)

    changed = _candidate("SELECT SUM(amount) FROM orders WHERE status = 'completed'")
    _patch_candidates(monkeypatch, [changed])

    def runner(goal, verbose, store, run):
        store.record_tool(
            run,
            "tool_investigate_datahub_conflicts",
            {"keyword": "*"},
            json.dumps({"summary": {"conflicting_pairs": 2}}),
        )
        store.complete(run, "Evidence is ambiguous; Finance must choose the policy.")
        return AgentExecution("Evidence is ambiguous", run.id, store.path_for(run.id))

    result = scan_once(
        client=StubDataHubClient(), state_store=state, run_store=runs, runner=runner,
    )

    run = runs.get(result.run_id)
    assert result.semantic_changes == 1
    assert result.outcome == AutonomousOutcome.NEEDS_HUMAN_DECISION
    assert run.trigger["semantic_changes"][0]["query_urn"] == changed.query_urn
    assert run.autonomous_outcome == AutonomousOutcome.NEEDS_HUMAN_DECISION


def test_outcome_classifier_recognizes_staged_and_clean_investigations(tmp_path):
    store = AgentRunStore(tmp_path)
    staged = store.start("resolve", "test", origin=RunOrigin.SENTINEL)
    store.record_tool(
        staged,
        "tool_stage_canonical_resolution",
        {},
        json.dumps({"staged_proposal_ids": ["abcd1234"]}),
    )
    assert classify_outcome(staged) == AutonomousOutcome.STAGED_RESOLUTION

    clean = store.start("dismiss", "test", origin=RunOrigin.SENTINEL)
    store.record_tool(
        clean,
        "tool_investigate_datahub_conflicts",
        {},
        json.dumps({"summary": {"conflicting_pairs": 0}}),
    )
    assert classify_outcome(clean) == AutonomousOutcome.DISMISSED_WITH_EVIDENCE

    already_resolved = store.start("resolved", "test", origin=RunOrigin.SENTINEL)
    store.record_tool(
        already_resolved,
        "tool_stage_canonical_resolution",
        {},
        json.dumps({
            "staged_proposal_ids": [],
            "existing_resolution_proposals": [{"status": "executed"}],
            "human_approval_required": False,
        }),
    )
    assert classify_outcome(already_resolved) == AutonomousOutcome.DISMISSED_WITH_EVIDENCE
