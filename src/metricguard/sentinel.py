"""Standing-agent mode: detect DataHub SQL changes and open investigations.

The polling loop is transport, not judgment. Change classification is deterministic;
material changes are handed to the existing agent and every autonomous run receives
one explicit terminal outcome.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

from metricguard.agent.loop import AgentExecution, run_agent_result
from metricguard.agent.runs import (
    AgentRun,
    AgentRunStore,
    AutonomousOutcome,
    RunOrigin,
)
from metricguard.config import settings
from metricguard.datahub.base import DataHubClient, get_datahub_client
from metricguard.datahub.discovery import candidates_from_graph
from metricguard.models import MetricDefinition


class ObservedDefinition(BaseModel):
    identity: str
    name: str
    dataset_urn: str
    query_urn: str
    sql_hash: str
    signature_hash: str


class SentinelState(BaseModel):
    schema_version: str = "1.0"
    keyword: str = "*"
    definitions: dict[str, ObservedDefinition] = Field(default_factory=dict)


class SentinelStateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.contracts_dir.parent / "sentinel" / "state.json"

    def load(self) -> SentinelState | None:
        if not self.path.exists():
            return None
        try:
            return SentinelState.model_validate(json.loads(self.path.read_text()))
        except (OSError, json.JSONDecodeError, ValidationError):
            return None

    def save(self, state: SentinelState) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w") as handle:
                handle.write(state.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.path


@dataclass(frozen=True)
class SentinelScanResult:
    status: str
    observed: int
    unchanged: int
    new: int
    semantic_changes: int
    cosmetic_changes: int
    removed: int
    run_id: str = ""
    outcome: AutonomousOutcome | None = None


Runner = Callable[[str, bool, AgentRunStore, AgentRun], AgentExecution]


def scan_once(
    *,
    keyword: str = "*",
    investigate_existing: bool = False,
    client: DataHubClient | None = None,
    state_store: SentinelStateStore | None = None,
    run_store: AgentRunStore | None = None,
    runner: Runner | None = None,
) -> SentinelScanResult:
    """Scan DataHub once; baseline, dismiss cosmetic edits, or launch the agent."""
    client = client or get_datahub_client()
    state_store = state_store or SentinelStateStore()
    run_store = run_store or AgentRunStore()
    candidates = candidates_from_graph(client, keyword=keyword)
    current = SentinelState(keyword=keyword, definitions={
        observed.identity: observed for observed in map(_observe, candidates)
    })
    previous = state_store.load()

    if (previous is None or previous.keyword != keyword) and not investigate_existing:
        state_store.save(current)
        return SentinelScanResult(
            status="baseline_created", observed=len(current.definitions), unchanged=0,
            new=0, semantic_changes=0, cosmetic_changes=0, removed=0,
        )

    before = previous.definitions if previous and previous.keyword == keyword else {}
    new_ids = sorted(set(current.definitions) - set(before))
    removed_ids = sorted(set(before) - set(current.definitions))
    semantic_ids: list[str] = []
    cosmetic_ids: list[str] = []
    unchanged = 0
    for identity in sorted(set(current.definitions) & set(before)):
        old, new = before[identity], current.definitions[identity]
        if old.sql_hash == new.sql_hash:
            unchanged += 1
        elif old.signature_hash == new.signature_hash:
            cosmetic_ids.append(identity)
        else:
            semantic_ids.append(identity)

    material_ids = new_ids + semantic_ids
    change_payload = {
        "keyword": keyword,
        "new_definitions": [_public(current.definitions[item]) for item in new_ids],
        "semantic_changes": [_public(current.definitions[item]) for item in semantic_ids],
        "cosmetic_changes": [_public(current.definitions[item]) for item in cosmetic_ids],
        "removed_definition_ids": removed_ids,
        "unchanged_count": unchanged,
    }

    if not material_ids and not cosmetic_ids:
        state_store.save(current)
        return SentinelScanResult(
            status="no_change", observed=len(current.definitions), unchanged=unchanged,
            new=0, semantic_changes=0, cosmetic_changes=0, removed=len(removed_ids),
        )

    if not material_ids:
        run = run_store.start(
            "Sentinel evaluated DataHub SQL edits that did not change metric semantics.",
            "deterministic:sentinel",
            origin=RunOrigin.SENTINEL,
            trigger=change_payload,
        )
        run_store.record_tool(
            run, "sentinel_change_detection", {"keyword": keyword},
            json.dumps({**change_payload, "decision": "dismissed_as_cosmetic"}),
        )
        run.autonomous_outcome = AutonomousOutcome.DISMISSED_WITH_EVIDENCE
        run_store.complete(
            run,
            f"Dismissed {len(cosmetic_ids)} SQL edit(s): semantic signatures are unchanged.",
        )
        state_store.save(current)
        return _result(
            "dismissed", current, unchanged, new_ids, semantic_ids, cosmetic_ids,
            removed_ids, run,
        )

    goal = _investigation_goal(change_payload)
    run = run_store.start(
        goal, settings.llm_model, origin=RunOrigin.SENTINEL, trigger=change_payload,
    )
    run_store.record_tool(
        run, "sentinel_change_detection", {"keyword": keyword},
        json.dumps({**change_payload, "decision": "investigate"}),
    )
    active_runner = runner or _default_runner
    try:
        execution = active_runner(goal, False, run_store, run)
        completed = run_store.get(execution.run_id) or run
        completed.autonomous_outcome = classify_outcome(completed)
        run_store.save(completed)
        state_store.save(current)
        return _result(
            "investigated", current, unchanged, new_ids, semantic_ids, cosmetic_ids,
            removed_ids, completed,
        )
    except Exception:
        # Do not advance the cursor. The next scan retries material changes.
        failed = run_store.get(run.id) or run
        failed.autonomous_outcome = AutonomousOutcome.NEEDS_HUMAN_DECISION
        run_store.save(failed)
        raise


def watch(
    *, keyword: str = "*", interval_seconds: float = 30.0,
    investigate_existing: bool = False,
) -> None:
    """Continuously scan until interrupted; only the first pass may bootstrap."""
    first = True
    while True:
        result = scan_once(
            keyword=keyword,
            investigate_existing=investigate_existing if first else False,
        )
        print(render_result(result), flush=True)
        first = False
        time.sleep(interval_seconds)


def classify_outcome(run: AgentRun) -> AutonomousOutcome:
    """Derive the autonomous outcome only from recorded deterministic tool results."""
    action = _last_tool_json(run, "tool_stage_canonical_resolution")
    if action and (
        action.get("staged_proposal_ids")
        or any(
            item.get("status") == "pending"
            for item in action.get("existing_resolution_proposals", [])
        )
    ):
        return AutonomousOutcome.STAGED_RESOLUTION
    if action and not action.get("error") and action.get("human_approval_required") is False:
        return AutonomousOutcome.DISMISSED_WITH_EVIDENCE

    investigation = _last_tool_json(run, "tool_investigate_datahub_conflicts")
    if investigation and investigation.get("summary", {}).get("conflicting_pairs") == 0:
        return AutonomousOutcome.DISMISSED_WITH_EVIDENCE
    return AutonomousOutcome.NEEDS_HUMAN_DECISION


def render_result(result: SentinelScanResult) -> str:
    details = (
        f"observed={result.observed} unchanged={result.unchanged} new={result.new} "
        f"semantic={result.semantic_changes} cosmetic={result.cosmetic_changes} "
        f"removed={result.removed}"
    )
    suffix = f" run={result.run_id} outcome={result.outcome.value}" if result.outcome else ""
    return f"sentinel: {result.status} ({details}){suffix}"


def _default_runner(
    goal: str, verbose: bool, store: AgentRunStore, run: AgentRun,
) -> AgentExecution:
    return run_agent_result(goal, verbose=verbose, store=store, run=run)


def _observe(candidate: MetricDefinition) -> ObservedDefinition:
    identity = candidate.query_urn or f"{candidate.dataset_urn}::{candidate.name}"
    signature_json = candidate.signature.model_dump_json() if candidate.signature else "null"
    return ObservedDefinition(
        identity=identity,
        name=candidate.name,
        dataset_urn=candidate.dataset_urn,
        query_urn=candidate.query_urn,
        sql_hash=_hash(candidate.sql),
        signature_hash=_hash(signature_json),
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _public(item: ObservedDefinition) -> dict[str, str]:
    return {
        "identity": item.identity,
        "name": item.name,
        "dataset_urn": item.dataset_urn,
        "query_urn": item.query_urn,
    }


def _investigation_goal(changes: dict) -> str:
    assets = changes["new_definitions"] + changes["semantic_changes"]
    rendered = ", ".join(
        f"{item['name']} ({item['dataset_urn']}, query {item['query_urn']})"
        for item in assets
    )
    return (
        "Sentinel detected new or semantically changed SQL definitions in DataHub: "
        f"{rendered}. Investigate whether they conflict with an existing metric family. "
        "Use deterministic signature and warehouse evidence. Stage a canonical resolution "
        "only if defensible; otherwise state the exact human decision needed."
    )


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


def _result(
    status: str,
    current: SentinelState,
    unchanged: int,
    new_ids: list[str],
    semantic_ids: list[str],
    cosmetic_ids: list[str],
    removed_ids: list[str],
    run: AgentRun,
) -> SentinelScanResult:
    return SentinelScanResult(
        status=status,
        observed=len(current.definitions),
        unchanged=unchanged,
        new=len(new_ids),
        semantic_changes=len(semantic_ids),
        cosmetic_changes=len(cosmetic_ids),
        removed=len(removed_ids),
        run_id=run.id,
        outcome=run.autonomous_outcome,
    )
