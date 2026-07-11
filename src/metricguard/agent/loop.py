"""Agent orchestration — a tool-calling decision loop.

The agent investigates (pull definitions from DataHub via MCP when configured,
extract signatures, compare, cluster, run divergence, check drift) and DOES
real work by STAGING write-back proposals. Direct mutations are excluded from
its tool belt by design — humans approve staged proposals via
`metricguard proposals approve`.

Provider-agnostic: works with whatever LLM_MODEL points at, via LangChain
bind_tools. Async because MCP tools are async; `run_agent` is the sync facade.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from metricguard.agent.tools import build_all_tools
from metricguard.agent.runs import AgentRun, AgentRunStore, RunStatus
from metricguard.config import settings
from metricguard.llm.client import get_llm

AGENT_SYSTEM_PROMPT = """\
You are MetricGuard, a semantic conflict intelligence agent for data teams.

You investigate whether an organization computes the same business metric \
with conflicting logic. You are built with the DataHub Agent Context Kit and \
its official MCP server. DataHub provides graph context; deterministic tools \
parse SQL, extract signatures, compare definitions, cluster candidates, \
execute divergence proofs, and check drift against approved contracts.

Operating rules:
- Always use tools for facts. Never guess what SQL means — extract and \
compare signatures instead. Never estimate divergence — run the tool or say \
the warehouse is not connected.
- When tool_investigate_datahub_conflicts is available, call it FIRST. Do not \
use local seeds. Use raw DataHub search/entities/lineage tools only for focused \
follow-up questions after the composed investigation.
- For a proposed SQL change against an already resolved canonical asset, use \
tool_check_datahub_drift so the contract comes from governed DataHub properties.
- Investigate before concluding: inspect candidates and conflict severities, \
use ownership/domain/lineage to assess impact, then use \
tool_prove_graph_divergence for the most decision-relevant pair when the \
warehouse is available.
- Finish real work by calling tool_stage_canonical_resolution only when the \
evidence supports a defensible recommendation. It stages a decision document, \
governed signature properties, canonical/divergent tags, and redirects. Never \
claim these are written until a human approves them via `metricguard proposals`.
- Copy `metric_family` exactly from the investigation cluster. Never derive or \
rename it from an individual candidate name; the staging tool rejects mismatches.
- Report action state EXACTLY from the tool result. If `staged_proposal_ids` is \
empty, say no new proposals were staged and the equivalent resolution already \
exists. Never invent, shorten, or combine proposal IDs or entity URNs.
- Separate graph facts from recommendations. Do not claim an asset is reviewed, \
certified, tested, popular, or organizationally authoritative unless a tool \
returned that evidence. You may label a reasoned inference explicitly as such.
- If evidence is insufficient to recommend a canonical, say exactly what human \
decision is needed; do not stage a made-up choice merely to appear autonomous.
- Final answers are for data leaders and must be concise (at most 500 words): \
lead with the conflict and numeric impact, explain the recommendation, then \
state the exact action status and next human step. Do not reproduce full SQL.
"""

MAX_ITERATIONS = 20
MAX_FINAL_RETRIES = 2

_UNSUPPORTED_FINAL_CLAIMS = (
    "peer-reviewed",
    "version-controlled",
    "accounting practices",
    "reporting compliance",
    "certified source",
)


@dataclass(frozen=True)
class AgentExecution:
    answer: str
    run_id: str
    trace_path: Path


async def arun_agent_result(
    goal: str,
    verbose: bool = True,
    *,
    store: AgentRunStore | None = None,
    run: AgentRun | None = None,
) -> AgentExecution:
    """Run the decision loop and persist a complete tool/action audit trail."""
    store = store or AgentRunStore()
    run = run or store.start(goal, settings.llm_model)

    try:
        tools = await build_all_tools()
        tools_by_name = {t.name: t for t in tools}
        llm = get_llm().bind_tools(tools)

        messages: list[BaseMessage] = [
            SystemMessage(content=AGENT_SYSTEM_PROMPT),
            HumanMessage(content=goal),
        ]
        final_retries = 0
        revision_requested = False

        for _ in range(MAX_ITERATIONS):
            response: AIMessage = await llm.ainvoke(messages)
            messages.append(response)

            if revision_requested and response.tool_calls:
                answer = _grounded_fallback(run)
                store.complete(run, answer)
                return AgentExecution(answer, run.id, store.path_for(run.id))

            if not response.tool_calls:
                answer = _text(response)
                issue = _final_answer_issue(answer, run)
                if issue and final_retries < MAX_FINAL_RETRIES:
                    final_retries += 1
                    revision_requested = True
                    store.record_tool(
                        run,
                        "grounding_check_intervention",
                        {"attempt": final_retries},
                        json.dumps({"status": "rewrite_requested", "issue": issue}),
                    )
                    messages.append(HumanMessage(content=(
                        f"Your final response failed the grounding check: {issue}. "
                        "Rewrite the concise final report using only recorded tool facts. "
                        "Copy action status and proposal IDs exactly. Do not call more tools."
                    )))
                    continue
                if issue:
                    answer = _grounded_fallback(run)
                store.complete(run, answer)
                return AgentExecution(answer, run.id, store.path_for(run.id))

            for call in response.tool_calls:
                tool = tools_by_name.get(call["name"])
                if verbose:
                    print(f"  → {call['name']}({_short(call['args'])})", flush=True)
                error = ""
                if tool is None:
                    error = f"Unknown tool: {call['name']}"
                    result = error
                else:
                    try:
                        result = await tool.ainvoke(call["args"])
                    except Exception as exc:  # surface tool errors to the model, don't crash
                        error = str(exc)
                        result = f"Tool error: {exc}"
                store.record_tool(
                    run, call["name"], call["args"], result, error=error,
                )
                messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

        answer = "Agent stopped: iteration limit reached without a final answer."
        store.complete(run, answer, status=RunStatus.ITERATION_LIMIT)
        return AgentExecution(answer, run.id, store.path_for(run.id))
    except Exception as exc:
        store.complete(run, "", status=RunStatus.FAILED, error=str(exc))
        raise


async def arun_agent(goal: str, verbose: bool = True) -> str:
    """Compatibility facade returning only the final answer."""
    return (await arun_agent_result(goal, verbose=verbose)).answer


def run_agent(goal: str, verbose: bool = True) -> str:
    """Sync facade over the async loop (MCP tools are async)."""
    return asyncio.run(arun_agent(goal, verbose=verbose))


def run_agent_result(
    goal: str,
    verbose: bool = True,
    *,
    store: AgentRunStore | None = None,
    run: AgentRun | None = None,
) -> AgentExecution:
    """Sync facade that also returns the durable trace location."""
    return asyncio.run(arun_agent_result(goal, verbose=verbose, store=store, run=run))


def _text(message: AIMessage) -> str:
    """Provider-neutral text extraction for string and content-block messages."""
    return str(message.text)


def _short(args: dict, limit: int = 80) -> str:
    s = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in args.items())
    return s if len(s) <= limit else s[:limit] + "…"


def _final_answer_issue(answer: str, run: AgentRun) -> str:
    """Return why an LLM narrative is not auditable, or an empty string."""
    if not answer.strip():
        return "the response contained no renderable text"
    lowered = answer.lower()
    unsupported = [claim for claim in _UNSUPPORTED_FINAL_CLAIMS if claim in lowered]
    if unsupported:
        return f"unsupported claims appeared: {unsupported}"

    action = _last_tool_json(run, "tool_stage_canonical_resolution")
    if not action:
        return ""
    if action.get("error"):
        if "staged_for_human_approval" in answer or "successfully staged" in lowered:
            return "the staging tool returned an error but the narrative claimed success"
        return ""
    staged = action.get("staged_proposal_ids", [])
    allowed = set(staged) | set(action.get("already_staged_or_executed_ids", []))
    mentioned = set(re.findall(r"`([0-9a-f]{8})`", answer.lower()))
    invented = mentioned - allowed
    if invented:
        return f"proposal IDs were not returned by the action tool: {sorted(invented)}"
    missing = set(staged) - mentioned
    if missing:
        return f"staged proposal IDs were omitted: {sorted(missing)}"
    if not staged and "no new proposals" not in lowered:
        return "the action tool staged nothing but the narrative did not say 'no new proposals'"
    existing = action.get("existing_resolution_proposals", [])
    if not staged and existing and all(item.get("status") == "executed" for item in existing):
        if "already executed" not in lowered and "no approval required" not in lowered:
            return "all equivalent proposals are executed but the narrative did not say so"
        if "approve <id>" in lowered or "approval is required" in lowered:
            return "the narrative requested approval for an already executed resolution"
    return ""


def _last_tool_json(run: AgentRun, name: str) -> dict:
    for trace in reversed(run.tool_traces):
        if trace.name != name:
            continue
        try:
            value = json.loads(trace.result)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _grounded_fallback(run: AgentRun) -> str:
    """Deterministic final report when a provider cannot produce grounded prose."""
    investigation = _last_tool_json(run, "tool_investigate_datahub_conflicts")
    divergence = _last_tool_json(run, "tool_prove_graph_divergence")
    action = _last_tool_json(run, "tool_stage_canonical_resolution")
    summary = investigation.get("summary", {})
    lines = [
        "### MetricGuard evidence report",
        "",
        (
            f"DataHub scan: {summary.get('candidate_count', 0)} candidates, "
            f"{summary.get('metric_family_count', 0)} families, "
            f"{summary.get('conflicting_pairs', 0)} conflicting pairs "
            f"({summary.get('critical_pairs', 0)} critical)."
        ),
    ]
    if divergence:
        lines += [
            "",
            (
                f"Warehouse proof: `{divergence.get('left_name', 'left')}` vs "
                f"`{divergence.get('right_name', 'right')}` differs by "
                f"{divergence.get('mean_pct_divergence', 0)}% on average and "
                f"{divergence.get('max_pct_divergence', 0)}% at maximum; first "
                f"divergence: {divergence.get('first_divergence_key') or 'unknown'}."
            ),
        ]
    if action:
        lines += ["", f"Action status: `{action.get('status', 'error')}`."]
        staged = action.get("staged_proposal_ids", [])
        if staged:
            lines.append("Staged proposal IDs: " + ", ".join(f"`{item}`" for item in staged) + ".")
        else:
            lines.append("No new proposals were staged.")
        if action.get("error"):
            lines.append(f"Action error: {action['error']}")
        lines.append(f"Next human step: {action.get('next_command', 'inspect the audit trace')}.")
    lines += ["", f"Audit trace: `{run.id}`."]
    return "\n".join(lines)
