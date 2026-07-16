"""Frozen JSON contract consumed by Mission Control.

The browser never reads internal AgentRun models directly.  This adapter is the
only seam between durable agent artifacts and the static dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from metricguard.agent.runs import AgentRun, RunOrigin, RunStatus, ToolTrace
from metricguard.models import DivergencePoint, DivergenceReport

SCHEMA_VERSION = "1.0"


class RunSummary(BaseModel):
    id: str
    title: str
    headline: str = ""
    goal: str
    model: str
    status: str
    origin: str = "human"
    outcome: str = ""
    parent_run_id: str = ""
    child_run_ids: list[str] = Field(default_factory=list)
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


class InvestigationFocus(BaseModel):
    """The evidence that makes an investigation worth a human's attention."""

    trigger: str
    semantic_break: str
    executed_proof: str
    governance_boundary: str


class MissionControlRun(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run: RunSummary
    timeline: list[TimelineEvent] = Field(default_factory=list)
    divergence: DivergenceReport | None = None
    divergences: list[DivergenceReport] = Field(default_factory=list)
    decision: DecisionSummary | None = None
    focus: InvestigationFocus | None = None
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
            title=_start_title(run),
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
            title=_completion_title(run),
            detail=_final_summary(
                run.final_answer,
                run.error,
                run.autonomous_outcome.value if run.autonomous_outcome else "",
            ),
            status=(
                "error" if run.error else "warning"
                if run.status in {RunStatus.CANCELED, RunStatus.ITERATION_LIMIT}
                else "success"
            ),
            recorded_at=run.completed_at,
            offset_ms=_offset_ms(run.completed_at, run.started_at),
        ))

    return MissionControlRun(
        run=RunSummary(
            id=run.id,
            title=_run_title(run, changed_assets),
            headline=_headline(divergence),
            goal=run.goal,
            model=run.model,
            status=run.status.value,
            origin=run.origin.value,
            outcome=run.autonomous_outcome.value if run.autonomous_outcome else "",
            parent_run_id=run.parent_run_id,
            child_run_ids=run.child_run_ids,
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
        focus=_investigation_focus(run, divergences),
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
    if name == "agent_plan_conflict_investigations" and result:
        investigations = result.get("investigations", [])
        families = [
            str(item.get("metric_family", "")).replace("_", " ")
            for item in investigations if isinstance(item, dict)
        ]
        return (
            "Investigation plan created",
            f"Agent chose {len(families)} focused run{'s' if len(families) != 1 else ''}: "
            f"{', '.join(families) or 'none'} · {result.get('decision_source', 'recorded')}",
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
    if run.origin == RunOrigin.AUTOMATIC:
        return "Organization-wide metric conflict scan"
    if run.origin == RunOrigin.DELEGATED:
        family = str(run.trigger.get("metric_family", "")).replace("_", " ").strip()
        return f"Focused investigation · {family}" if family else "Focused investigation"
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


def _headline(divergence: DivergenceReport | None) -> str:
    if divergence is None:
        return ""
    metric = _shared_metric_name(divergence.left_name, divergence.right_name)
    left = _display_name(divergence.left_name, metric)
    right = _display_name(divergence.right_name, metric)
    return f"{metric.title()} conflict: {left} vs {right}"


def _investigation_focus(
    run: AgentRun, divergences: list[DivergenceReport],
) -> InvestigationFocus | None:
    discovery = _discovery_summary(run.tool_traces)
    summary = discovery.get("summary", {})
    first = divergences[0] if divergences else None
    if not summary and first is None:
        return None

    candidate_count = summary.get("candidate_count", 0)
    family_count = summary.get("metric_family_count", 0)
    conflict_count = summary.get("conflicting_pairs", 0)
    trigger = (
        f"{candidate_count} candidate definitions formed {family_count} conflict "
        f"famil{'y' if family_count == 1 else 'ies'} with {conflict_count} conflicting pairs."
        if summary else "MetricGuard recorded a semantic conflict worth investigating."
    )
    semantic_break = _semantic_break(discovery)
    if first is None:
        executed_proof = "Numeric proof is unavailable until a warehouse-backed comparison can run."
    else:
        metric = _shared_metric_name(first.left_name, first.right_name)
        executed_proof = (
            f"{metric.title()} was executed across {len(first.points)} comparison period"
            f"{'s' if len(first.points) != 1 else ''}; mean gap {first.mean_pct_divergence:.2f}%."
        )
    return InvestigationFocus(
        trigger=trigger,
        semantic_break=semantic_break,
        executed_proof=executed_proof,
        governance_boundary="A human must approve before MetricGuard writes the decision back to DataHub.",
    )


def _discovery_summary(traces: list[ToolTrace]) -> dict[str, Any]:
    for trace in traces:
        result = _json_object(trace.result)
        if result and isinstance(result.get("summary"), dict):
            return result
    return {}


def _semantic_break(discovery: dict[str, Any]) -> str:
    for cluster in discovery.get("clusters", []):
        for conflict in cluster.get("conflicts", []):
            fields = [str(diff.get("field", "")) for diff in conflict.get("diffs", []) if diff.get("field")]
            if fields:
                labels = ", ".join(field.replace("_", " ") for field in fields[:3])
                return f"Definitions disagree on {labels}."
    return "MetricGuard compared SQL semantics before recommending any canonical definition."


def _shared_metric_name(left: str, right: str) -> str:
    left_words = left.replace("_", " ").split()
    right_words = right.replace("_", " ").split()
    shared = [word for word in left_words if word in right_words]
    return " ".join(shared) or "Metric"


def _display_name(name: str, metric: str) -> str:
    metric_words = set(metric.lower().split())
    words = [word for word in name.replace("_", " ").split() if word.lower() not in metric_words]
    labels = {"exec": "Executive", "ops": "Operations"}
    return " ".join(labels.get(word.lower(), word.capitalize()) for word in words) or name.replace("_", " ").title()


def _start_detail(run: AgentRun, changed_assets: list[str]) -> str:
    if run.origin == RunOrigin.AUTOMATIC:
        return (
            "MetricGuard started an organization-wide discovery scan because no metric "
            "conflict was specified."
        )
    if run.origin == RunOrigin.DELEGATED:
        family = str(run.trigger.get("metric_family", "metric family")).replace("_", " ")
        return (
            f"The discovery agent delegated this focused {family} investigation. "
            f"{run.trigger.get('coordinator_reason', '')}"
        ).strip()
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
    if run.status == RunStatus.CANCELED:
        return DecisionSummary(
            state="dismissed", title="Investigation stopped",
            detail="A user stopped this run. Recorded evidence was kept; no write was executed.",
            next_action="Delete the run if its audit trail is no longer needed.",
        )
    if run.status == RunStatus.ITERATION_LIMIT:
        return DecisionSummary(
            state="human", title="Investigation needs continuation",
            detail=(
                "The agent used its reasoning budget before producing a grounded conclusion. "
                "Broad requests are now split into focused child investigations to avoid this."
            ),
            next_action="Start a focused follow-up for the unresolved metric family.",
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
    if run.child_run_ids:
        count = len(run.child_run_ids)
        return DecisionSummary(
            state="complete", title="Focused investigations launched",
            detail=(
                f"The discovery agent chose {count} metric famil"
                f"{'y' if count == 1 else 'ies'} and opened an independent run for each."
            ),
            next_action="Review the focused child investigations in the sidebar.",
        )
    if run.completed_at:
        return DecisionSummary(
            state="complete", title="Investigation complete",
            detail="MetricGuard completed its evidence checks. Review the final timeline event.",
        )
    return None


def _start_title(run: AgentRun) -> str:
    if run.origin == RunOrigin.SENTINEL:
        return "Sentinel investigation started"
    if run.origin == RunOrigin.AUTOMATIC:
        return "Organization-wide scan started"
    if run.origin == RunOrigin.DELEGATED:
        return "Focused investigation delegated"
    return "Investigation started"


def _completion_title(run: AgentRun) -> str:
    if run.error:
        return "Investigation failed"
    if run.status == RunStatus.CANCELED:
        return "Investigation stopped"
    if run.status == RunStatus.ITERATION_LIMIT:
        return "Investigation needs continuation"
    if run.child_run_ids:
        return "Focused investigations launched"
    return "Investigation complete"


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
