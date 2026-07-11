"""Frozen JSON contract consumed by Mission Control.

The browser never reads internal AgentRun models directly.  This adapter is the
only seam between durable agent artifacts and the static dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from metricguard.agent.runs import AgentRun, RunOrigin, ToolTrace
from metricguard.models import DivergencePoint, DivergenceReport

SCHEMA_VERSION = "1.0"


class RunSummary(BaseModel):
    id: str
    title: str
    goal: str
    model: str
    status: str
    origin: str = "human"
    outcome: str = ""
    changed_assets: list[str] = Field(default_factory=list)
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


class DecisionSummary(BaseModel):
    state: Literal["action", "human", "dismissed", "failed", "complete"]
    title: str
    detail: str
    next_action: str = ""
    proposal_ids: list[str] = Field(default_factory=list)


class MissionControlRun(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run: RunSummary
    timeline: list[TimelineEvent] = Field(default_factory=list)
    divergence: DivergenceReport | None = None
    divergences: list[DivergenceReport] = Field(default_factory=list)
    decision: DecisionSummary | None = None
    proof_unavailable_reason: str = ""


def build_mission_control_run(run: AgentRun) -> MissionControlRun:
    """Project a durable agent run into the stable, presentation-safe contract."""
    divergences = _find_divergences(run.tool_traces)
    divergence = divergences[0] if divergences else None
    changed_assets = _changed_assets(run)
    timeline = [
        TimelineEvent(
            id="run-start",
            kind="run",
            title=(
                "Sentinel investigation started"
                if run.origin == RunOrigin.SENTINEL else "Investigation started"
            ),
            detail=_start_detail(run, changed_assets),
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
            detail=_final_summary(
                run.final_answer,
                run.error,
                run.autonomous_outcome.value if run.autonomous_outcome else "",
            ),
            status="error" if run.error else "success",
            recorded_at=run.completed_at,
            offset_ms=_offset_ms(run.completed_at, run.started_at),
        ))

    return MissionControlRun(
        run=RunSummary(
            id=run.id,
            title=_run_title(run, changed_assets),
            goal=run.goal,
            model=run.model,
            status=run.status.value,
            origin=run.origin.value,
            outcome=run.autonomous_outcome.value if run.autonomous_outcome else "",
            changed_assets=changed_assets,
            started_at=run.started_at,
            completed_at=run.completed_at,
            event_count=len(timeline),
            has_divergence=divergence is not None,
        ),
        timeline=timeline,
        divergence=divergence,
        divergences=divergences,
        decision=_decision_summary(run),
        proof_unavailable_reason=_proof_unavailable_reason(run.tool_traces, divergences),
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
    if name == "sentinel_change_detection" and result:
        changed = len(result.get("new_definitions", [])) + len(result.get("semantic_changes", []))
        return (
            "DataHub change evaluated",
            f"{result.get('unchanged_count', 0)} unchanged skipped · "
            f"{changed} material · {len(result.get('cosmetic_changes', []))} cosmetic · "
            f"decision: {str(result.get('decision', 'recorded')).replace('_', ' ')}",
            "decision",
        )
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


def _find_divergences(traces: list[ToolTrace]) -> list[DivergenceReport]:
    reports: list[DivergenceReport] = []
    for trace in traces:
        parsed = _json_object(trace.result)
        if not parsed or "points" not in parsed or "mean_pct_divergence" not in parsed:
            continue
        try:
            reports.append(DivergenceReport(
                left_name=str(parsed["left_name"]),
                right_name=str(parsed["right_name"]),
                points=[DivergencePoint.model_validate(point) for point in parsed["points"]],
                mean_pct_divergence=float(parsed.get("mean_pct_divergence", 0)),
                max_pct_divergence=float(parsed.get("max_pct_divergence", 0)),
                total_abs_divergence=float(parsed.get("total_abs_divergence", 0)),
                first_divergence_key=parsed.get("first_divergence_key"),
                segment_localization=parsed.get("segment_localization", {}),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return reports


def _changed_assets(run: AgentRun) -> list[str]:
    names: list[str] = []
    trigger = run.trigger
    if not trigger:
        trigger = _last_trace_json(run.tool_traces, "sentinel_change_detection")
    for key in ("new_definitions", "semantic_changes", "cosmetic_changes"):
        for item in trigger.get(key, []):
            name = item.get("name", "") if isinstance(item, dict) else ""
            rendered = name.replace("_", " ").strip()
            if rendered and rendered not in names:
                names.append(rendered)
    return names


def _run_title(run: AgentRun, changed_assets: list[str]) -> str:
    if run.origin == RunOrigin.SENTINEL:
        if len(changed_assets) == 1:
            return f"Change detected · {changed_assets[0]}"
        if len(changed_assets) == 2:
            return f"{changed_assets[0]} + {changed_assets[1]}"
        if changed_assets:
            return f"{len(changed_assets)} changed metric definitions"
        return "Sentinel catalog investigation"
    goal = " ".join(run.goal.split())
    return goal[:72] + ("…" if len(goal) > 72 else "")


def _start_detail(run: AgentRun, changed_assets: list[str]) -> str:
    if run.origin == RunOrigin.SENTINEL and changed_assets:
        return (
            f"DataHub reported {len(changed_assets)} material change"
            f"{'s' if len(changed_assets) != 1 else ''}: {', '.join(changed_assets)}."
        )
    return run.goal


def _decision_summary(run: AgentRun) -> DecisionSummary | None:
    action = _last_trace_json(run.tool_traces, "tool_stage_canonical_resolution")
    proposal_ids = list(action.get("staged_proposal_ids", [])) if action else []
    if run.error:
        return DecisionSummary(
            state="failed", title="Investigation failed", detail=run.error,
            next_action="Fix the configuration error, then retry the investigation.",
        )
    outcome = run.autonomous_outcome.value if run.autonomous_outcome else ""
    if outcome == "staged_resolution" or proposal_ids:
        count = len(proposal_ids)
        detail = (
            f"MetricGuard staged {count} governed change{'s' if count != 1 else ''}; "
            "nothing has been written to DataHub yet."
        )
        return DecisionSummary(
            state="action", title="Resolution ready for human review", detail=detail,
            next_action=action.get("next_command", "metricguard proposals list") if action else (
                "metricguard proposals list"
            ),
            proposal_ids=proposal_ids,
        )
    if outcome == "needs_human_decision":
        return DecisionSummary(
            state="human", title="Human decision needed",
            detail="Evidence is real, but MetricGuard refused to force an unsupported canonical choice.",
            next_action="Review the competing business policies in the final evidence summary.",
        )
    if outcome == "dismissed_with_evidence":
        return DecisionSummary(
            state="dismissed", title="Dismissed with evidence",
            detail="The observed change did not require a governance resolution.",
            next_action="No action required; the dismissal remains in the audit trail.",
        )
    if run.completed_at:
        return DecisionSummary(
            state="complete", title="Investigation complete",
            detail="MetricGuard completed its evidence checks. Review the final timeline event.",
        )
    return None


def _proof_unavailable_reason(
    traces: list[ToolTrace], divergences: list[DivergenceReport],
) -> str:
    if divergences:
        return ""
    for trace in reversed(traces):
        if "divergence" not in trace.name:
            continue
        parsed = _json_object(trace.result) or {}
        reason = trace.error or str(parsed.get("error", ""))
        if reason:
            return reason[:280]
    return ""


def _last_trace_json(traces: list[ToolTrace], name: str) -> dict[str, Any]:
    for trace in reversed(traces):
        if trace.name == name:
            return _json_object(trace.result) or {}
    return {}


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


def _final_summary(answer: str, error: str, autonomous_outcome: str = "") -> str:
    value = error or answer.strip().replace("\n", " ")
    if autonomous_outcome and not error:
        value = f"Outcome: {autonomous_outcome.replace('_', ' ')}. {value}"
    if not value:
        return "Run closed after the recorded evidence and action state were checked."
    return value[:280] + ("…" if len(value) > 280 else "")


def _humanize(name: str) -> str:
    return name.removeprefix("tool_").replace("_", " ").strip().capitalize()


def _offset_ms(recorded_at: datetime, started_at: datetime) -> int:
    return max(0, int((recorded_at - started_at).total_seconds() * 1000))
