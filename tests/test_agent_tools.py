import json

from metricguard.agent import tools
from metricguard.agent.runs import AgentRun, RunStatus, ToolTrace
from metricguard.execution.base import StaticExecutor
from metricguard.ui.contracts import build_mission_control_run


def test_local_divergence_tool_emits_full_points_for_ui(monkeypatch):
    executor = StaticExecutor(responses={
        "left sql": [
            {"week_start": "2026-07-06", "weekly_active_users": 100},
            {"week_start": "2026-07-13", "weekly_active_users": 105},
        ],
        "right sql": [
            {"week_start": "2026-07-06", "weekly_active_users": 90},
            {"week_start": "2026-07-13", "weekly_active_users": 95},
        ],
    })
    monkeypatch.setattr(tools, "get_executor", lambda: executor)

    result = tools.tool_run_divergence.invoke({
        "sql_a": "left sql",
        "sql_b": "right sql",
        "key_col": "week_start",
        "value_col": "weekly_active_users",
        "name_a": "finance_wau",
        "name_b": "product_wau",
    })
    payload = json.loads(result)

    assert len(payload["points"]) == 2
    assert payload["largest_divergence_points"]

    run = AgentRun(
        id="local-divergence",
        goal="Compare local WAU definitions",
        model="test:model",
        status=RunStatus.COMPLETED,
        tool_traces=[ToolTrace(name="tool_run_divergence", result=result)],
    )
    contract = build_mission_control_run(run)
    assert contract.divergence is not None
    assert len(contract.divergence.points) == 2
