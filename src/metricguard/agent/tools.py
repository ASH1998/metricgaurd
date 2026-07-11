"""Every component exposed as a callable tool (day-one principle, context.md).

Because each engine is already a pure function over serializable models,
agent orchestration is just wiring a decision loop — no retrofitting.

Tools are LangChain `@tool`s so they bind to any chat model via
`llm.bind_tools(...)`.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from metricguard.clustering.grouper import cluster_candidates
from metricguard.comparison.diff import compare_signatures
from metricguard.config import settings
from metricguard.divergence.engine import compute_divergence
from metricguard.execution.base import NotConfiguredError, get_executor
from metricguard.guard.contracts import ContractStore
from metricguard.models import MetricDefinition
from metricguard.signature.extractor import extract_signature


@tool
def tool_extract_signature(sql: str, dialect: str = "") -> str:
    """Extract the semantic signature (aggregation, entity, grain, timezone,
    filters, deduplication, null_handling, source_population) from a SQL
    metric definition. Returns JSON."""
    sig = extract_signature(sql, dialect=dialect or settings.dialect)
    return sig.model_dump_json(indent=2)


@tool
def tool_compare_definitions(sql_a: str, sql_b: str, name_a: str = "A",
                             name_b: str = "B", dialect: str = "") -> str:
    """Compare two SQL metric definitions semantically. Returns a JSON
    conflict report with field-by-field diffs and severities. Use this to
    prove exactly how two definitions disagree."""
    d = dialect or settings.dialect
    report = compare_signatures(
        extract_signature(sql_a, dialect=d),
        extract_signature(sql_b, dialect=d),
        left_name=name_a, right_name=name_b,
    )
    return report.model_dump_json(indent=2)


_SEEDS_ROOT = Path("seeds/metric_families")  # config, not an agent decision


@tool
def tool_load_seed_definitions() -> str:
    """Load ALL candidate metric definitions known to the system (from the
    seed catalog). Takes no arguments. Returns a JSON list of
    {name, sql, source, owner} — use these names in the other tools."""
    out = []
    for manifest_path in sorted(_SEEDS_ROOT.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest.get("definitions", []):
            sql_path = manifest_path.parent / entry["file"]
            out.append({
                "name": entry["name"],
                "sql": sql_path.read_text(),
                "source": entry.get("source", ""),
                "owner": entry.get("owner", ""),
                "family_hint": manifest.get("family", ""),
            })
    if not out:
        return (f"ERROR: no seed definitions found under {_SEEDS_ROOT.resolve()} — "
                "check that you are running from the repository root.")
    return json.dumps(out, indent=2)


@tool
def tool_cluster_candidates(definitions_json: str) -> str:
    """Group candidate definitions (JSON list of {name, sql, source, owner})
    into metric families using deterministic signals. Returns JSON clusters
    with confidence scores and visible evidence."""
    raw = json.loads(definitions_json)
    candidates = []
    for item in raw:
        md = MetricDefinition(**item)
        md.signature = extract_signature(md.sql, dialect=settings.dialect)
        candidates.append(md)
    clusters = cluster_candidates(candidates)
    return json.dumps([c.model_dump() for c in clusters], indent=2)


@tool
def tool_run_divergence(sql_a: str, sql_b: str, key_col: str, value_col: str,
                        name_a: str = "A", name_b: str = "B",
                        segment_col: str = "") -> str:
    """Execute two competing definitions against the warehouse and compute
    numeric divergence (abs/%, first divergence key, segment localization).
    Requires the warehouse connection; returns an explanatory error if it is
    not configured yet."""
    try:
        executor = get_executor()
    except NotConfiguredError as e:
        return json.dumps({"error": str(e)})
    report = compute_divergence(
        executor.query(sql_a), executor.query(sql_b),
        key_col=key_col, value_col=value_col,
        left_name=name_a, right_name=name_b,
        segment_col=segment_col or None,
    )
    largest = sorted(report.points, key=lambda point: point.pct_divergence, reverse=True)[:5]
    return json.dumps({
        "left_name": report.left_name,
        "right_name": report.right_name,
        "periods_compared": len(report.points),
        "mean_pct_divergence": report.mean_pct_divergence,
        "max_pct_divergence": report.max_pct_divergence,
        "total_abs_divergence": report.total_abs_divergence,
        "first_divergence_key": report.first_divergence_key,
        "points": [point.model_dump(mode="json") for point in report.points],
        "largest_divergence_points": [point.model_dump(mode="json") for point in largest],
        "segment_localization": report.segment_localization,
    }, indent=2)


@tool
def tool_check_drift(metric: str, sql: str) -> str:
    """Guard mode: check a new/changed SQL definition against the approved
    contract for a metric. Returns a JSON drift report (ok | drift |
    no_contract)."""
    report = ContractStore().check_drift(metric, sql)
    return report.model_dump_json(indent=2)


@tool
def tool_check_datahub_drift(canonical_dataset_urn: str, proposed_sql: str) -> str:
    """Guard a proposed SQL change against the canonical contract IN DATAHUB.

    The asset must carry the MetricGuard Canonical tag and governed semantic
    signature structured properties. Returns ok, drift, or no_contract with
    exact changed dimensions. Use this for graph-native guard requests.
    """
    from metricguard.datahub.base import get_datahub_client
    from metricguard.guard.datahub_contracts import check_datahub_drift

    return check_datahub_drift(
        get_datahub_client(), canonical_dataset_urn, proposed_sql,
    ).model_dump_json(indent=2)


@tool
def tool_stage_writeback(kind: str, target: str, payload_json: str,
                         rationale: str, metric: str = "") -> str:
    """Stage a DataHub write-back PROPOSAL for human review. This does NOT
    mutate DataHub — a human approves/rejects via `metricguard proposals`.
    kind: document | tag | description | glossary_term | structured_property
    (these map to real MCP mutation tools; there is no create-term or incident
    tool). target: the entity URN or metric name. payload_json: the mutation
    payload as JSON, matching the tool's args. rationale: your evidence-backed
    case for this write."""
    from metricguard.datahub.proposals import Proposal, ProposalStore

    proposal = ProposalStore().stage(Proposal(
        metric=metric, kind=kind, target=target,
        payload=json.loads(payload_json), rationale=rationale,
    ))
    return (f"Staged proposal {proposal.id} ({kind} -> {target}). "
            f"A human can execute it with: metricguard proposals approve {proposal.id}")


def investigate_datahub_conflicts(keyword: str = "*") -> str:
    """Implementation behind the graph-native agent tool (kept directly testable)."""
    from metricguard.datahub.base import get_datahub_client
    from metricguard.datahub.investigation import investigate_graph

    report = investigate_graph(get_datahub_client(), keyword=keyword)
    return json.dumps(report, indent=2)


@tool
def tool_investigate_datahub_conflicts(keyword: str = "*") -> str:
    """Run a complete evidence-first conflict scan FROM THE DATAHUB GRAPH.

    Uses the official DataHub Agent Context Kit MCP server to search assets,
    retrieve observed SQL, ownership, domains, tags, and downstream lineage.
    Then deterministically extracts semantic signatures, clusters likely metric
    families, and compares every pair. Call this first for conflict investigations.
    `keyword` can be `*`, `revenue`, `wau`, or another DataHub search query.
    """
    return investigate_datahub_conflicts(keyword)


def prove_graph_divergence(
    left_dataset_urn: str,
    right_dataset_urn: str,
    key_col: str,
    value_col: str,
    keyword: str,
    segment_col: str = "",
) -> str:
    """Execute two graph-selected candidates without asking the LLM to relay SQL."""
    from metricguard.datahub.base import get_datahub_client
    from metricguard.datahub.investigation import investigated_candidates

    client = get_datahub_client()
    candidates = investigated_candidates(client, keyword)
    by_urn = {candidate.dataset_urn: candidate for candidate in candidates}
    missing = [urn for urn in (left_dataset_urn, right_dataset_urn) if urn not in by_urn]
    if missing:
        return json.dumps({"error": "Dataset URN was not found in this graph scan", "missing": missing})
    try:
        executor = get_executor()
    except NotConfiguredError as exc:
        return json.dumps({"error": str(exc)})
    left, right = by_urn[left_dataset_urn], by_urn[right_dataset_urn]
    report = compute_divergence(
        executor.query(left.sql), executor.query(right.sql),
        key_col=key_col, value_col=value_col,
        left_name=left.name, right_name=right.name,
        segment_col=segment_col or None,
    )
    return report.model_dump_json(indent=2)


@tool
def tool_prove_graph_divergence(
    left_dataset_urn: str,
    right_dataset_urn: str,
    key_col: str,
    value_col: str,
    keyword: str,
    segment_col: str = "",
) -> str:
    """Quantify the disagreement between two candidates selected from DataHub.

    Pass dataset URNs returned by tool_investigate_datahub_conflicts. The tool
    re-fetches their observed SQL from DataHub and runs it against the warehouse,
    returning exact divergence math. It never trusts SQL copied through the LLM.
    """
    return prove_graph_divergence(
        left_dataset_urn, right_dataset_urn, key_col, value_col, keyword, segment_col,
    )


def stage_canonical_resolution(
    metric_family: str,
    canonical_dataset_urn: str,
    rationale: str,
    keyword: str,
) -> str:
    """Build and stage the complete, valid MCP write-back set for one cluster."""
    from metricguard.datahub.base import get_datahub_client
    from metricguard.datahub.investigation import investigated_candidates
    from metricguard.datahub.proposals import ProposalStatus, ProposalStore
    from metricguard.datahub.writeback import build_canonical_writeback

    client = get_datahub_client()
    candidates = investigated_candidates(client, keyword)
    clusters = cluster_candidates(candidates)
    canonical = next(
        (candidate for candidate in candidates if candidate.dataset_urn == canonical_dataset_urn),
        None,
    )
    if canonical is None:
        return json.dumps({"error": "Canonical dataset URN was not found", "urn": canonical_dataset_urn})
    if canonical.family_hint and metric_family != canonical.family_hint:
        return json.dumps({
            "error": "Requested family does not match DataHub governed metric_family",
            "requested_metric_family": metric_family,
            "datahub_metric_family": canonical.family_hint,
            "no_proposals_staged": True,
        })
    cluster = next(
        (
            item for item in clusters
            if item.metric_family == metric_family and canonical.name in item.members
        ),
        None,
    )
    if cluster is None:
        return json.dumps({
            "error": "Canonical candidate is not in the requested metric family",
            "metric_family": metric_family,
            "available_families": [item.metric_family for item in clusters],
        })
    divergent = [
        candidate for candidate in candidates
        if candidate.dataset_urn != canonical.dataset_urn and candidate.name in cluster.members
    ]
    proposals = build_canonical_writeback(
        metric_family, canonical, divergent, evidence_summary=rationale,
    )
    store = ProposalStore()
    existing = {
        _proposal_identity(item): item
        for item in store.list()
        if item.status != ProposalStatus.REJECTED
    }
    staged, skipped = [], []
    for proposal in proposals:
        proposal.rationale = f"{proposal.rationale} Agent evidence: {rationale}"
        key = _proposal_identity(proposal)
        if key in existing:
            skipped.append(existing[key])
            continue
        staged.append(store.stage(proposal).id)
    status = "staged_for_human_approval" if staged else "no_new_proposals"
    existing_statuses = [
        {"id": item.id, "status": item.status.value} for item in skipped
    ]
    pending_existing = [
        item["id"] for item in existing_statuses if item["status"] == "pending"
    ]
    approval_required = bool(staged or pending_existing)
    if staged:
        action_summary = f"Staged {len(staged)} new proposals; none were executed."
        next_command = "metricguard proposals list"
    elif pending_existing:
        action_summary = "No new proposals were staged; equivalent proposals are pending approval."
        next_command = "metricguard proposals list"
    else:
        action_summary = "No new proposals were staged; the equivalent resolution is already executed."
        next_command = "No approval required."
    return json.dumps({
        "status": status,
        "metric_family": metric_family,
        "canonical": {"name": canonical.name, "dataset_urn": canonical.dataset_urn},
        "divergent_dataset_urns": [candidate.dataset_urn for candidate in divergent],
        "staged_proposal_ids": staged,
        "already_staged_or_executed_ids": [item.id for item in skipped],
        "existing_resolution_proposals": existing_statuses,
        "action_summary": action_summary,
        "human_approval_required": approval_required,
        "next_command": next_command,
    }, indent=2)


def _proposal_identity(proposal) -> tuple[str, str, str]:
    """Semantic idempotency key for a resolution mutation.

    Decision-document wording may improve as owner display names are enriched;
    the same linked resolution must not create duplicate documents on every run.
    Other writes remain payload-sensitive so a real signature/tag change can be staged.
    """
    payload = proposal.payload
    if proposal.kind == "document":
        payload = {"related_assets": sorted(payload.get("related_assets", []))}
    return proposal.kind, proposal.target, json.dumps(payload, sort_keys=True)


@tool
def tool_stage_canonical_resolution(
    metric_family: str,
    canonical_dataset_urn: str,
    rationale: str,
    keyword: str,
) -> str:
    """Stage a complete canonical-resolution write-back from graph evidence.

    Select a canonical dataset URN from the investigation report. This stages a
    DataHub decision document, governed SemanticSignature structured properties,
    canonical/divergent tags, and warning redirects. It is idempotent and NEVER
    mutates DataHub; a human must approve each staged mutation in the CLI.
    """
    return stage_canonical_resolution(metric_family, canonical_dataset_urn, rationale, keyword)


def build_tools(
    *, include_seeds: bool = True, include_generic_writeback: bool = True,
) -> list[BaseTool]:
    """The agent's local tool belt.

    Direct mutation tools are intentionally NOT here — the agent's only write
    power is STAGING proposals (tool_stage_writeback); execution happens via
    the human-approval gate in the CLI.
    """
    tools = [
        tool_extract_signature,
        tool_compare_definitions,
        tool_cluster_candidates,
        tool_run_divergence,
        tool_check_drift,
    ]
    if include_generic_writeback:
        tools.append(tool_stage_writeback)
    if include_seeds:
        tools.insert(0, tool_load_seed_definitions)
    return tools


async def build_all_tools() -> list[BaseTool]:
    """Local tools + DataHub MCP tools (search/lineage/queries) when configured.

    With MCP enabled, discovery genuinely starts FROM DataHub metadata instead
    of local seed files.
    """
    from metricguard.config import settings

    graph_enabled = bool(settings.datahub_mcp_transport)
    tools = build_tools(
        include_seeds=not graph_enabled,
        include_generic_writeback=not graph_enabled,
    )
    if settings.datahub_mcp_transport:
        from metricguard.datahub.mcp_client import load_datahub_mcp_tools

        tools += [
            tool_investigate_datahub_conflicts,
            tool_prove_graph_divergence,
            tool_stage_canonical_resolution,
            tool_check_datahub_drift,
        ]
        mcp_tools = await load_datahub_mcp_tools()
        # read-only tools go to the agent; mutations stay behind the proposal gate
        tools += [t for t in mcp_tools if not _is_mutation(t.name)]
    return tools


_MUTATION_MARKERS = ("create", "update", "add", "set", "delete", "upsert",
                     "patch", "raise", "save", "remove")


def _is_mutation(tool_name: str) -> bool:
    name = tool_name.lower()
    return any(name.startswith(m) or f"_{m}" in name for m in _MUTATION_MARKERS)
