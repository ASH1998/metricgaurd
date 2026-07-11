import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

from metricguard.agent.runs import AgentRun, AgentRunStore, RunStatus, ToolTrace
from metricguard.ui.contracts import build_mission_control_run
from metricguard.ui.server import create_app, export_run


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


def test_api_lists_and_returns_frozen_contract(tmp_path: Path):
    store = AgentRunStore(tmp_path / "runs")
    store.save(_run())
    client = TestClient(create_app(store, preferred_run_id="golden-ui"))

    index = client.get("/api/runs")
    detail = client.get("/api/runs/golden-ui")

    assert index.status_code == 200
    assert index.json()["preferred_run_id"] == "golden-ui"
    assert index.json()["runs"][0]["has_divergence"] is True
    assert detail.status_code == 200
    assert detail.json()["schema_version"] == "1.0"
    assert client.get("/api/runs/missing").status_code == 404


def test_export_is_a_zero_backend_snapshot(tmp_path: Path):
    index_path = export_run(_run(), tmp_path / "site")

    assert index_path.exists()
    assert "MetricGuard Mission Control" in index_path.read_text()
    exported = json.loads((tmp_path / "site/data/golden-ui.json").read_text())
    run_index = json.loads((tmp_path / "site/data/index.json").read_text())
    assert exported["divergence"]["mean_pct_divergence"] == 15.0
    assert run_index["preferred_run_id"] == "golden-ui"
