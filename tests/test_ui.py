import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

from metricguard.agent.runs import (
    AgentRun,
    AgentRunStore,
    AutonomousOutcome,
    RunOrigin,
    RunStatus,
    ToolTrace,
)
from metricguard.ui.contracts import build_mission_control_run
from metricguard.ui import server
from metricguard.ui.server import create_app, dashboard_html, export_run


def _run() -> AgentRun:
    started = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    divergence = {
        "left_name": "finance_revenue",
        "right_name": "executive_revenue",
        "points": [
            {
                "key": "2026-07-06",
                "left_value": 100.0,
                "right_value": 115.0,
                "abs_divergence": 15.0,
                "pct_divergence": 15.0,
            }
        ],
        "mean_pct_divergence": 15.0,
        "max_pct_divergence": 15.0,
        "first_divergence_key": "2026-07-06",
    }
    return AgentRun(
        id="golden-ui",
        goal="Investigate the weekly revenue conflict",
        model="test:model",
        status=RunStatus.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(seconds=12),
        tool_traces=[ToolTrace(
            name="tool_prove_graph_divergence",
            arguments={"left": "finance", "right": "executive"},
            result=json.dumps(divergence),
            recorded_at=started + timedelta(seconds=8),
        )],
        final_answer="Finance is defensible as canonical.",
    )


def test_contract_projects_timeline_and_real_divergence():
    contract = build_mission_control_run(_run())

    assert contract.schema_version == "1.0"
    assert [event.offset_ms for event in contract.timeline] == [0, 8000, 12000]
    assert contract.timeline[1].title == "Divergence proven"
    assert contract.divergence is not None
    assert len(contract.divergences) == 1
    assert contract.divergence.points[0].right_value == 115.0
    assert contract.run.has_divergence is True
    assert contract.run.title == "Investigate the weekly revenue conflict"
    assert contract.run.headline == "Revenue conflict: Finance vs Executive"
    assert contract.focus is not None
    assert "1 comparison period" in contract.focus.executed_proof


def test_contract_explains_the_discovery_signal_before_the_proof():
    run = _run()
    run.tool_traces.insert(0, ToolTrace(
        name="tool_investigate_datahub_conflicts",
        result=json.dumps({
            "summary": {
                "candidate_count": 3,
                "metric_family_count": 1,
                "conflicting_pairs": 2,
            },
            "clusters": [{"conflicts": [{"diffs": [
                {"field": "filters"}, {"field": "deduplication"},
            ]}]}],
        }),
        recorded_at=run.started_at + timedelta(seconds=2),
    ))

    contract = build_mission_control_run(run)

    assert contract.focus is not None
    assert contract.focus.trigger == (
        "3 candidate definitions formed 1 conflict family with 2 conflicting pairs."
    )
    assert contract.focus.semantic_break == "Definitions disagree on filters, deduplication."


def test_contract_exposes_grounding_intervention():
    run = _run()
    run.tool_traces.insert(0, ToolTrace(
        name="grounding_check_intervention",
        arguments={"attempt": 1},
        result=json.dumps({"status": "rewrite_requested", "issue": "invented proposal ID"}),
        recorded_at=run.started_at + timedelta(seconds=2),
    ))

    contract = build_mission_control_run(run)

    event = contract.timeline[1]
    assert event.kind == "grounding"
    assert event.title == "Grounding intervention"
    assert "invented proposal ID" in event.detail


def test_contract_makes_sentinel_trigger_and_outcome_visible():
    run = _run()
    run.origin = RunOrigin.SENTINEL
    run.autonomous_outcome = AutonomousOutcome.NEEDS_HUMAN_DECISION
    run.tool_traces.insert(0, ToolTrace(
        name="sentinel_change_detection",
        arguments={"keyword": "*"},
        result=json.dumps({
            "new_definitions": [{"name": "rogue_revenue"}],
            "semantic_changes": [],
            "cosmetic_changes": [],
            "unchanged_count": 18,
            "decision": "investigate",
        }),
        recorded_at=run.started_at + timedelta(seconds=1),
    ))

    contract = build_mission_control_run(run)

    assert contract.timeline[0].title == "Sentinel investigation started"
    assert contract.timeline[1].title == "DataHub change evaluated"
    assert "18 unchanged skipped" in contract.timeline[1].detail
    assert "needs human decision" in contract.timeline[-1].detail
    assert contract.run.title == "Change detected · rogue revenue"
    assert contract.decision.state == "human"
    assert contract.decision.title == "Human decision needed"


def test_contract_exposes_multiple_proofs_and_staged_human_action():
    run = _run()
    second = json.loads(run.tool_traces[0].result)
    second["left_name"] = "finance_refunds"
    second["right_name"] = "support_refunds"
    run.tool_traces.append(ToolTrace(
        name="tool_prove_graph_divergence",
        result=json.dumps(second),
        recorded_at=run.started_at + timedelta(seconds=9),
    ))
    run.tool_traces.append(ToolTrace(
        name="tool_stage_canonical_resolution",
        result=json.dumps({
            "staged_proposal_ids": ["abcd1234", "efgh5678"],
            "next_command": "metricguard proposals list",
        }),
        recorded_at=run.started_at + timedelta(seconds=10),
    ))

    contract = build_mission_control_run(run)

    assert len(contract.divergences) == 2
    assert contract.divergences[1].left_name == "finance_refunds"
    assert contract.decision.state == "action"
    assert contract.decision.proposal_ids == ["abcd1234", "efgh5678"]


def test_contract_explains_why_numeric_proof_is_unavailable():
    run = _run()
    run.tool_traces = [ToolTrace(
        name="tool_prove_graph_divergence",
        result=json.dumps({"error": 'relation "metric.events" does not exist'}),
        recorded_at=run.started_at + timedelta(seconds=3),
    )]

    contract = build_mission_control_run(run)

    assert contract.divergence is None
    assert "metric.events" in contract.proof_unavailable_reason


def test_api_lists_and_returns_frozen_contract(tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")
    store.save(_run())
    client = TestClient(create_app(store, preferred_run_id="golden-ui"))

    index = client.get("/api/runs")
    detail = client.get("/api/runs/golden-ui")

    assert index.status_code == 200
    assert index.json()["mode"] == "live"
    assert index.json()["preferred_run_id"] == "golden-ui"
    assert index.json()["runs"][0]["has_divergence"] is True
    assert detail.status_code == 200
    assert detail.json()["schema_version"] == "1.0"
    assert client.get("/api/runs/missing").status_code == 404


def test_api_starts_an_investigation_without_bypassing_run_store(monkeypatch, tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")

    async def finish(goal, run_store, run):
        run_store.complete(run, f"Investigated: {goal}")

    monkeypatch.setattr(server, "_run_investigation", finish)
    with TestClient(create_app(store)) as client:
        invalid = client.post("/api/investigations", json={"goal": "short"})
        started = client.post(
            "/api/investigations",
            json={"goal": "Investigate conflicting weekly revenue definitions"},
        )

    assert invalid.status_code == 422
    assert started.status_code == 202
    run_id = started.json()["run"]["id"]
    assert store.get(run_id) is not None
    assert store.get(run_id).goal.startswith("Investigate conflicting")


def test_investigation_api_is_disabled_in_replay_mode(tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")
    client = TestClient(create_app(store, replay_mode=True))

    response = client.post(
        "/api/investigations",
        json={"goal": "Investigate conflicting weekly revenue definitions"},
    )

    assert response.status_code == 403
    assert store.list() == []


def test_live_ui_starts_an_organization_wide_discovery_when_empty(monkeypatch, tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")
    observed: list[str] = []

    async def finish(goal, run_store, run, spawn_child):
        observed.append(goal)
        run_store.complete(run, "Organization-wide discovery completed.")

    from metricguard.agent import coordinator

    monkeypatch.setattr(coordinator, "coordinate_conflict_investigations", finish)
    with TestClient(create_app(store, auto_discover=True)) as client:
        response = client.get("/api/runs")

    assert response.status_code == 200
    assert len(response.json()["runs"]) == 1
    run = store.list()[0]
    assert run.origin == RunOrigin.AUTOMATIC
    assert run.goal == server.AUTOMATIC_DISCOVERY_GOAL
    assert observed == [server.AUTOMATIC_DISCOVERY_GOAL]


def test_contract_labels_automatic_discovery_without_a_user_metric():
    run = _run()
    run.origin = RunOrigin.AUTOMATIC

    contract = build_mission_control_run(run)

    assert contract.run.title == "Organization-wide metric conflict scan"
    assert "organization-wide discovery scan" in contract.timeline[0].detail


def test_investigation_api_requires_json_content_type(tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")
    client = TestClient(create_app(store))

    response = client.post(
        "/api/investigations",
        content='{"goal":"Investigate conflicting weekly revenue definitions"}',
    )

    assert response.status_code == 415
    assert store.list() == []


def test_run_can_be_stopped_and_deleted(tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")
    run = store.start("Investigate a running metric conflict", "test:model")

    with TestClient(create_app(store)) as client:
        stopped = client.post(f"/api/runs/{run.id}/stop")
        deleted = client.delete(f"/api/runs/{run.id}")

    assert stopped.status_code == 200
    assert stopped.json()["run"]["status"] == "canceled"
    assert stopped.json()["decision"]["title"] == "Investigation stopped"
    assert deleted.status_code == 200
    assert store.get(run.id) is None


def test_broad_request_fans_out_into_agent_selected_children(monkeypatch, tmp_path: Path):
    from metricguard.agent import coordinator
    from metricguard.agent.coordinator import PlannedInvestigation

    store = AgentRunStore(tmp_path / "runs")

    async def coordinate(goal, run_store, run, spawn_child):
        items = [
            PlannedInvestigation(
                metric_family="weekly_revenue", reason="Material revenue conflict", priority=5,
            ),
            PlannedInvestigation(
                metric_family="weekly_refund_amount", reason="Material refund conflict", priority=4,
            ),
        ]
        run.child_run_ids = [spawn_child(item, run) for item in items]
        run_store.complete(run, "Delegated two focused investigations.")

    async def finish_child(goal, run_store, run):
        run_store.complete(run, f"Completed: {goal}")

    monkeypatch.setattr(coordinator, "coordinate_conflict_investigations", coordinate)
    monkeypatch.setattr(server, "_run_investigation", finish_child)
    with TestClient(create_app(store)) as client:
        response = client.post(
            "/api/investigations",
            json={"goal": "find conflicting reports across the organization"},
        )
        client.get("/api/runs")

    assert response.status_code == 202
    runs = store.list()
    parent = next(run for run in runs if run.origin == RunOrigin.HUMAN)
    children = [run for run in runs if run.origin == RunOrigin.DELEGATED]
    assert len(children) == 2
    assert len(parent.child_run_ids) == 2
    assert {run.trigger["metric_family"] for run in children} == {
        "weekly_revenue", "weekly_refund_amount",
    }


def test_run_proposals_are_exposed_for_the_review_tab(monkeypatch, tmp_path: Path):
    from metricguard.datahub import proposals as proposals_module
    from metricguard.datahub.proposals import Proposal, ProposalStore

    store = AgentRunStore(tmp_path / "runs")
    proposal_store = ProposalStore(tmp_path / "proposals")
    proposal = proposal_store.stage(Proposal(
        metric="weekly_revenue",
        kind="tag",
        target="urn:li:dataset:finance",
        rationale="Mark the approved canonical definition.",
    ))
    run = _run()
    run.tool_traces.append(ToolTrace(
        name="tool_stage_canonical_resolution",
        result=json.dumps({"staged_proposal_ids": [proposal.id]}),
    ))
    store.save(run)
    monkeypatch.setattr(proposals_module, "ProposalStore", lambda: proposal_store)

    client = TestClient(create_app(store))
    response = client.get(f"/api/runs/{run.id}/proposals")

    assert response.status_code == 200
    assert response.json()["proposals"][0]["id"] == proposal.id
    assert response.json()["proposals"][0]["status"] == "pending"


def test_export_is_a_zero_backend_snapshot(tmp_path: Path):
    index_path = export_run(_run(), tmp_path / "site")

    assert index_path.exists()
    assert "New investigation" in index_path.read_text(encoding="utf-8")
    exported = json.loads((tmp_path / "site/data/golden-ui.json").read_text())
    run_index = json.loads((tmp_path / "site/data/index.json").read_text())
    assert exported["divergence"]["mean_pct_divergence"] == 15.0
    assert run_index["preferred_run_id"] == "golden-ui"


def test_frontend_keeps_sse_reconnect_and_handles_units_and_flat_series():
    html = dashboard_html().read_text(encoding="utf-8")

    assert "S.stream.onerror=()=>text('mode','Reconnecting…')" in html
    assert "S.stream.onerror=()=>S.stream.close()" not in html
    assert "const formatterFor=" in html
    assert "rawHi===rawLo" in html
    assert "const money=" not in html
    assert "Why MetricGuard investigated" in html
    assert "proofSelect" in html
    assert "Play from start" in html
    assert "stopRun" in html
    assert "deleteRun" in html
    assert "proposalsTab" in html
    assert '<span class="version">alpha</span>' not in html
