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
    assert contract.divergence.points[0].right_value == 115.0
    assert contract.run.has_divergence is True


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


def test_investigation_api_requires_json_content_type(tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")
    client = TestClient(create_app(store))

    response = client.post(
        "/api/investigations",
        content='{"goal":"Investigate conflicting weekly revenue definitions"}',
    )

    assert response.status_code == 415
    assert store.list() == []


def test_export_is_a_zero_backend_snapshot(tmp_path: Path):
    index_path = export_run(_run(), tmp_path / "site")

    assert index_path.exists()
    assert "New investigation" in index_path.read_text()
    exported = json.loads((tmp_path / "site/data/golden-ui.json").read_text())
    run_index = json.loads((tmp_path / "site/data/index.json").read_text())
    assert exported["divergence"]["mean_pct_divergence"] == 15.0
    assert run_index["preferred_run_id"] == "golden-ui"


def test_frontend_keeps_sse_reconnect_and_handles_units_and_flat_series():
    html = dashboard_html().read_text()

    assert "S.stream.onerror=()=>text('mode','Reconnecting…')" in html
    assert "S.stream.onerror=()=>S.stream.close()" not in html
    assert "const formatterFor=" in html
    assert "rawHi===rawLo" in html
    assert "const money=" not in html
