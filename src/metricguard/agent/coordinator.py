"""Agent-directed fan-out for broad semantic-conflict investigations."""

from __future__ import annotations

import json
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from metricguard.agent.runs import AgentRun, AgentRunStore, RunStatus
from metricguard.agent.tools import build_all_tools
from metricguard.llm.client import get_llm

MAX_CHILD_INVESTIGATIONS = 6


class PlannedInvestigation(BaseModel):
    metric_family: str
    reason: str
    priority: int = Field(default=3, ge=1, le=5)


class InvestigationPlan(BaseModel):
    rationale: str = ""
    investigations: list[PlannedInvestigation] = Field(default_factory=list)


async def coordinate_conflict_investigations(
    goal: str,
    store: AgentRunStore,
    run: AgentRun,
    spawn_child: Callable[[PlannedInvestigation, AgentRun], str],
) -> None:
    """Discover once, let the LLM choose focused families, then launch child runs."""
    try:
        tools = await build_all_tools()
        discovery = next(
            tool for tool in tools if tool.name == "tool_investigate_datahub_conflicts"
        )
        result = await discovery.ainvoke({"keyword": "*"})
        store.record_tool(
            run,
            "tool_investigate_datahub_conflicts",
            {"keyword": "*"},
            result,
        )
        report = _json_object(result)
        plan, source = await _choose_investigations(goal, report)
        store.record_tool(
            run,
            "agent_plan_conflict_investigations",
            {"goal": goal, "max_children": MAX_CHILD_INVESTIGATIONS},
            json.dumps({
                **plan.model_dump(mode="json"),
                "decision_source": source,
            }),
        )

        child_ids = [spawn_child(item, run) for item in plan.investigations]
        run.child_run_ids = child_ids
        store.save(run)
        count = len(child_ids)
        if count:
            families = ", ".join(item.metric_family for item in plan.investigations)
            answer = (
                f"The discovery agent split this broad request into {count} focused "
                f"investigation{'s' if count != 1 else ''}: {families}. Each child has "
                "its own evidence budget and human approval gate."
            )
        else:
            answer = (
                "The discovery agent found no conflict family that warranted a focused "
                "investigation for this request."
            )
        store.complete(run, answer)
    except Exception as exc:
        store.complete(run, "", status=RunStatus.FAILED, error=str(exc))
        raise


async def _choose_investigations(
    goal: str, report: dict,
) -> tuple[InvestigationPlan, str]:
    eligible = _eligible_clusters(report)
    if not eligible:
        return InvestigationPlan(rationale="No conflicting clusters were discovered."), "deterministic"

    prompt_payload = [{
        "metric_family": cluster["metric_family"],
        "members": cluster["members"],
        "conflicting_pairs": len(cluster["conflicts"]),
        "worst_severities": sorted({
            conflict.get("worst_severity", "") for conflict in cluster["conflicts"]
        }),
        "differing_dimensions": sorted({
            diff.get("field", "")
            for conflict in cluster["conflicts"]
            for diff in conflict.get("diffs", [])
            if diff.get("field")
        }),
    } for cluster in eligible]

    try:
        planner = get_llm().with_structured_output(InvestigationPlan)
        proposed = await planner.ainvoke([
            SystemMessage(content=(
                "You are MetricGuard's investigation coordinator. Select how many focused "
                "metric-family investigations to open. For broad organization-wide requests, "
                "select every materially conflicting family. For scoped requests, select only "
                "relevant families. Use metric_family values exactly as supplied. Do not select "
                f"more than {MAX_CHILD_INVESTIGATIONS}. Deterministic tools will prove every fact."
            )),
            HumanMessage(content=json.dumps({
                "user_goal": goal,
                "eligible_conflict_families": prompt_payload,
            })),
        ])
        normalized = _normalize_plan(proposed, eligible)
        return normalized, "llm"
    except Exception:  # noqa: BLE001 - deterministic fallback keeps discovery operable
        fallback = InvestigationPlan(
            rationale="Planner unavailable; opened one bounded run per conflicting family.",
            investigations=[
                PlannedInvestigation(
                    metric_family=cluster["metric_family"],
                    reason="Deterministic discovery recorded one or more semantic conflicts.",
                    priority=3,
                )
                for cluster in eligible[:MAX_CHILD_INVESTIGATIONS]
            ],
        )
        return fallback, "deterministic_fallback"


def _eligible_clusters(report: dict) -> list[dict]:
    return [
        cluster for cluster in report.get("clusters", [])
        if isinstance(cluster, dict)
        and cluster.get("metric_family")
        and cluster.get("conflicts")
    ]


def _normalize_plan(proposed: InvestigationPlan, eligible: list[dict]) -> InvestigationPlan:
    allowed = {cluster["metric_family"] for cluster in eligible}
    selected: list[PlannedInvestigation] = []
    seen: set[str] = set()
    for item in sorted(proposed.investigations, key=lambda value: value.priority, reverse=True):
        if item.metric_family not in allowed or item.metric_family in seen:
            continue
        selected.append(item)
        seen.add(item.metric_family)
        if len(selected) == MAX_CHILD_INVESTIGATIONS:
            break
    return InvestigationPlan(rationale=proposed.rationale, investigations=selected)


def _json_object(value: object) -> dict:
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
