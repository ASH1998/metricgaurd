"""Frozen JSON contract consumed by Mission Control.

The browser never reads internal AgentRun models directly.  This adapter is the
only seam between durable agent artifacts and the static dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from metricguard.agent.runs import AgentRun, ToolTrace
from metricguard.models import DivergencePoint, DivergenceReport

SCHEMA_VERSION = "1.0"


class RunSummary(BaseModel):
    id: str
    goal: str
    model: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    event_count: int = 0
    has_divergence: bool = False


class TimelineEvent(BaseModel):
    id: str
    kind: Literal["run", "tool", "grounding", "decision", "complete"]
    title: str
    detail: str = ""
    status: Literal["running", "success", "warning", "error", "waiting"] = "success"
    recorded_at: datetime
    offset_ms: int = 0
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)


class MissionControlRun(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run: RunSummary
    timeline: list[TimelineEvent] = Field(default_factory=list)
    divergence: DivergenceReport | None = None


def build_mission_control_run(run: AgentRun) -> MissionControlRun:
    """Project a durable agent run into the stable, presentation-safe contract."""
    divergence = _find_divergence(run.tool_traces)
    timeline = [
        TimelineEvent(
            id="run-start",
            kind="run",
            title="Investigation started",
            detail=run.goal,
            status="running" if run.status.value == "running" else "success",
            recorded_at=run.started_at,
        )
    ]
    for index, trace in enumerate(run.tool_traces, start=1):
        timeline.append(_trace_event(trace, index, run.started_at))
    if run.completed_at:
        timeline.append(TimelineEvent(
            id="run-complete",
            kind="complete",
            title="Investigation complete" if not run.error else "Investigation failed",
            detail=_final_summary(run.final_answer, run.error),
            status="error" if run.error else "success",
            recorded_at=run.completed_at,
            offset_ms=_offset_ms(run.completed_at, run.started_at),
        ))

    return MissionControlRun(
        run=RunSummary(
            id=run.id,
            goal=run.goal,
            model=run.model,
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            event_count=len(timeline),
            has_divergence=divergence is not None,
        ),
        timeline=timeline,
        divergence=divergence,
    )


def _trace_event(trace: ToolTrace, index: int, started_at: datetime) -> TimelineEvent:
    parsed = _json_object(trace.result)
    title, detail, kind = _describe_tool(trace.name, parsed)
    return TimelineEvent(
        id=f"tool-{index}",
        kind=kind,
        title=title,
        detail=trace.error or detail,
        status="error" if trace.error else "success",
        recorded_at=trace.recorded_at,
        offset_ms=_offset_ms(trace.recorded_at, started_at),
        tool_name=trace.name,
        arguments=trace.arguments,
    )


def _describe_tool(
    name: str, result: dict[str, Any] | None,
) -> tuple[str, str, Literal["tool", "grounding", "decision"]]:
    if result and "points" in result and "mean_pct_divergence" in result:
        return (
            "Divergence proven",
            f"{result.get('left_name', 'Left')} vs {result.get('right_name', 'right')} · "
            f"{result.get('mean_pct_divergence', 0):.2f}% mean gap · "
            f"{len(result.get('points', []))} periods fact-checked",
            "tool",
        )
    if result and isinstance(result.get("summary"), dict):
        summary = result["summary"]
        return (
            "Organization scanned",
            f"{summary.get('candidate_count', 0)} candidates · "
            f"{summary.get('metric_family_count', 0)} family · "
            f"{summary.get('conflicting_pairs', 0)} conflicting pairs",
            "tool",
        )
    if "resolve" in name or "proposal" in name:
        status = result.get("status", "proposal state recorded") if result else "proposal state recorded"
        return "Human gate checked", str(status).replace("_", " ").capitalize(), "decision"
    if "ground" in name:
        issue = result.get("issue", "Agent output did not match recorded evidence.") if result else (
            "Agent output did not match recorded evidence."
        )
        return "Grounding intervention", f"{issue} Rewrite demanded.", "grounding"
    return _humanize(name), _compact_result(result), "tool"


def _find_divergence(traces: list[ToolTrace]) -> DivergenceReport | None:
    for trace in traces:
        parsed = _json_object(trace.result)
        if not parsed or "points" not in parsed or "mean_pct_divergence" not in parsed:
            continue
        try:
            return DivergenceReport(
                left_name=str(parsed["left_name"]),
                right_name=str(parsed["right_name"]),
                points=[DivergencePoint.model_validate(point) for point in parsed["points"]],
                mean_pct_divergence=float(parsed.get("mean_pct_divergence", 0)),
                max_pct_divergence=float(parsed.get("max_pct_divergence", 0)),
                first_divergence_key=parsed.get("first_divergence_key"),
                segment_localization=parsed.get("segment_localization", {}),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _compact_result(result: dict[str, Any] | None) -> str:
    if not result:
        return "Deterministic tool completed and its evidence was recorded."
    for key in ("summary", "message", "rationale", "status"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value[:240]
    return f"Evidence recorded ({len(result)} fields)."


def _final_summary(answer: str, error: str) -> str:
    value = error or answer.strip().replace("\n", " ")
    if not value:
        return "Run closed after the recorded evidence and action state were checked."
    return value[:280] + ("…" if len(value) > 280 else "")


def _humanize(name: str) -> str:
    return name.removeprefix("tool_").replace("_", " ").strip().capitalize()


def _offset_ms(recorded_at: datetime, started_at: datetime) -> int:
    return max(0, int((recorded_at - started_at).total_seconds() * 1000))
