"""MetricGuard CLI — the terminal drives; the DataHub UI shows the write-back.

Commands that work today with no warehouse and no LLM key:
  signature, compare, discover (deterministic part), guard approve/check

Commands that need config:
  discover --explain (LLM_MODEL + API key), agent (same), divergence (POSTGRES_DSN)
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from metricguard.clustering.grouper import cluster_candidates
from metricguard.comparison.diff import compare_signatures
from metricguard.config import settings
from metricguard.guard.contracts import ContractStore
from metricguard.models import ConflictReport, DriftVerdict, MetricDefinition, Severity
from metricguard.signature.extractor import extract_signature

app = typer.Typer(help="MetricGuard — Semantic Conflict Intelligence for DataHub.")
guard_app = typer.Typer(help="Guard mode: approved contracts + drift detection.")
proposals_app = typer.Typer(help="Review + execute the agent's staged write-back proposals.")
datahub_app = typer.Typer(help="DataHub connection utilities.")
runs_app = typer.Typer(help="Inspect durable agent run and tool-call audit trails.")
app.add_typer(guard_app, name="guard")
app.add_typer(proposals_app, name="proposals")
app.add_typer(datahub_app, name="datahub")
app.add_typer(runs_app, name="runs")

console = Console()

_SEV_STYLE = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.COSMETIC: "dim",
}


# ---------------------------------------------------------------------------
# signature / compare — the Week-1 engine, directly inspectable
# ---------------------------------------------------------------------------

@app.command()
def signature(
    sql_file: Path = typer.Argument(..., help="Path to a .sql metric definition"),
    dialect: str = typer.Option(None, help="SQL dialect (defaults to METRICGUARD_DIALECT)"),
):
    """Extract and print the semantic signature of one definition."""
    sig = extract_signature(sql_file.read_text(), dialect=dialect or settings.dialect)
    console.print(Panel(sig.model_dump_json(indent=2), title=f"signature: {sql_file.name}"))


@app.command()
def compare(
    sql_a: Path = typer.Argument(..., help="First definition (.sql)"),
    sql_b: Path = typer.Argument(..., help="Second definition (.sql)"),
    dialect: str = typer.Option(None, help="SQL dialect (defaults to METRICGUARD_DIALECT)"),
):
    """Prove exactly how two definitions semantically differ."""
    d = dialect or settings.dialect
    report = compare_signatures(
        extract_signature(sql_a.read_text(), dialect=d),
        extract_signature(sql_b.read_text(), dialect=d),
        left_name=sql_a.stem, right_name=sql_b.stem,
    )
    _print_conflict(report)
    raise typer.Exit(code=1 if report.is_conflict else 0)


# ---------------------------------------------------------------------------
# discover — the full Discovery flow over seeded candidates
# ---------------------------------------------------------------------------

@app.command()
def discover(
    seeds_dir: Path = typer.Option(Path("seeds/metric_families"), help="Seed families root"),
    from_graph: bool = typer.Option(
        False, "--from-graph",
        help="Discover candidates from DataHub (needs DATAHUB_MCP_TRANSPORT) instead of seeds",
    ),
    keyword: str = typer.Option("*", help="Search term for graph discovery (--from-graph)"),
    explain: bool = typer.Option(False, help="Add LLM explanation + canonical proposals"),
):
    """Where do we have competing definitions? Cluster candidates, prove conflicts."""
    if from_graph:
        from metricguard.datahub.base import get_datahub_client
        from metricguard.datahub.discovery import candidates_from_graph

        client = get_datahub_client()
        console.print(f"Discovering candidates from DataHub (keyword={keyword!r})...")
        candidates = candidates_from_graph(client, keyword=keyword)
        if not candidates:
            console.print(
                "[red]No candidate definitions found in DataHub.[/red] "
                "Is DATAHUB_MCP_TRANSPORT set and the org ingested "
                "(scripts/simulate_org.py)?"
            )
            raise typer.Exit(code=2)
    else:
        candidates = _load_seeds(seeds_dir)
        if not candidates:
            console.print(f"[red]No seed definitions found under {seeds_dir}[/red]")
            raise typer.Exit(code=2)

    console.print(f"Loaded [bold]{len(candidates)}[/bold] candidate definitions.\n")
    clusters = cluster_candidates(candidates)
    if not clusters:
        console.print("[green]No competing definitions detected.[/green]")
        return

    by_name = {c.name: c for c in candidates}
    for cluster in clusters:
        console.print(Panel(
            f"members: [bold]{', '.join(cluster.members)}[/bold]\n"
            f"confidence: [bold]{cluster.confidence:.0%}[/bold]\n"
            + "\n".join(f"  • {e.signal}: {e.detail} (+{e.weight})" for e in cluster.evidence),
            title=f"metric family: {cluster.metric_family}",
        ))

        # pairwise conflict proof within the cluster
        members = cluster.members
        reports: list[ConflictReport] = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = by_name[members[i]], by_name[members[j]]
                report = compare_signatures(a.signature, b.signature,
                                            left_name=a.name, right_name=b.name)
                reports.append(report)
                if report.is_conflict:
                    _print_conflict(report)

        if explain and reports:
            _explain(candidates=[by_name[m] for m in members], report=reports[0])


# ---------------------------------------------------------------------------
# resolve — stage the write-back for a chosen canonical (graph-sourced)
# ---------------------------------------------------------------------------

@app.command()
def resolve(
    canonical: str = typer.Option(..., help="Name of the candidate to make canonical (e.g. 'weekly_revenue')"),
    keyword: str = typer.Option("*", help="Search term used to discover the family in DataHub"),
):
    """Stage the DataHub write-back for a resolved metric family (no mutation yet).

    Discovers the family from the graph, marks --canonical as the truth, and
    stages proposals (decision document + canonical/divergent tags + redirects).
    Execute them with `metricguard proposals approve <id>`.
    """
    from metricguard.datahub.base import get_datahub_client
    from metricguard.datahub.discovery import candidates_from_graph
    from metricguard.datahub.proposals import ProposalStore
    from metricguard.datahub.writeback import build_canonical_writeback

    candidates = candidates_from_graph(get_datahub_client(), keyword=keyword)
    if not candidates:
        console.print("[red]No candidates found in DataHub (need DATAHUB_MCP_TRANSPORT).[/red]")
        raise typer.Exit(code=2)

    clusters = cluster_candidates(candidates)
    by_name = {c.name: c for c in candidates}
    family = next((cl for cl in clusters if canonical in cl.members), None)
    if family is None:
        console.print(
            f"[red]'{canonical}' is not in any discovered family.[/red] "
            f"Candidates: {sorted(by_name)}"
        )
        raise typer.Exit(code=2)

    chosen = by_name[canonical]
    divergent = [by_name[m] for m in family.members if m != canonical]
    proposals = build_canonical_writeback(family.metric_family, chosen, divergent)

    store = ProposalStore()
    for p in proposals:
        store.stage(p)
    console.print(
        f"Staged [bold]{len(proposals)}[/bold] write-back proposals for family "
        f"[bold]{family.metric_family}[/bold] (canonical: [bold]{canonical}[/bold]).\n"
    )
    table = Table(title="staged (pending approval)")
    for col in ("id", "kind", "target"):
        table.add_column(col)
    for p in proposals:
        table.add_row(p.id, p.kind, p.target)
    console.print(table)
    console.print("\n[dim]Review: metricguard proposals show <id> · "
                  "Execute: metricguard proposals approve <id>[/dim]")


# ---------------------------------------------------------------------------
# divergence — executed proof of disagreement (needs POSTGRES_DSN)
# ---------------------------------------------------------------------------

@app.command()
def divergence(
    sql_a: Path = typer.Argument(..., help="First definition (.sql)"),
    sql_b: Path = typer.Argument(..., help="Second definition (.sql)"),
    key_col: str = typer.Option("week_start", help="Join key column (the time bucket)"),
    value_col: str = typer.Option(..., help="The metric value column"),
    limit: int = typer.Option(12, help="Show at most this many diverging points"),
):
    """Execute two definitions against the warehouse and quantify the gap."""
    from metricguard.divergence.engine import compute_divergence
    from metricguard.execution.base import NotConfiguredError, get_executor

    try:
        executor = get_executor()
    except NotConfiguredError as e:
        console.print(f"[yellow]{e}[/yellow]")
        raise typer.Exit(code=2)

    console.print("[dim]executing both definitions...[/dim]")
    report = compute_divergence(
        executor.query(sql_a.read_text()), executor.query(sql_b.read_text()),
        key_col=key_col, value_col=value_col,
        left_name=sql_a.stem, right_name=sql_b.stem,
    )

    headline = (f"mean divergence [bold]{report.mean_pct_divergence}%[/bold] · "
                f"max [bold]{report.max_pct_divergence}%[/bold]")
    if report.first_divergence_key:
        headline += f" · diverging since [bold]{report.first_divergence_key}[/bold]"
    console.print(Panel(headline, title=f"{report.left_name} vs {report.right_name}"))

    diverging = [p for p in report.points if p.abs_divergence > 0]
    table = Table(title=f"largest gaps (of {len(diverging)} diverging periods)")
    table.add_column(key_col)
    table.add_column(report.left_name, justify="right")
    table.add_column(report.right_name, justify="right")
    table.add_column("gap", justify="right")
    table.add_column("gap %", justify="right")
    for p in sorted(diverging, key=lambda p: -p.pct_divergence)[:limit]:
        table.add_row(p.key, f"{p.left_value:,.0f}", f"{p.right_value:,.0f}",
                      f"{p.abs_divergence:,.0f}", f"{p.pct_divergence}%")
    console.print(table)


# ---------------------------------------------------------------------------
# guard — contracts + drift
# ---------------------------------------------------------------------------

@guard_app.command("approve")
def guard_approve(
    metric: str = typer.Argument(..., help="Metric name, e.g. weekly_active_users"),
    sql_file: Path = typer.Argument(..., help="The approved canonical definition (.sql)"),
    approved_by: str = typer.Option("", help="Who approved (for the audit trail)"),
):
    """Capture an approved definition's signature as the guard contract."""
    contract = ContractStore().approve(metric, sql_file.read_text(), approved_by=approved_by)
    console.print(f"[green]Contract saved[/green] for [bold]{metric}[/bold] "
                  f"(approved_by: {approved_by or '—'})")
    console.print(Panel(contract.signature.model_dump_json(indent=2), title="approved signature"))


@guard_app.command("check")
def guard_check(
    metric: str = typer.Argument(..., help="Metric name with an approved contract"),
    sql_file: Path = typer.Argument(..., help="New/changed definition to check (.sql)"),
):
    """Warn-before-ship: does this change semantically break the approved definition?

    Exit codes: 0 ok · 1 drift · 2 no contract. Wire into CI/pre-deploy.
    """
    report = ContractStore().check_drift(metric, sql_file.read_text())

    if report.verdict == DriftVerdict.OK:
        console.print(f"[green]✔ {report.message}[/green]")
        raise typer.Exit(code=0)
    if report.verdict == DriftVerdict.NO_CONTRACT:
        console.print(f"[yellow]{report.message}[/yellow]")
        raise typer.Exit(code=2)

    console.print(f"[bold red]✘ {report.message}[/bold red]")
    table = Table(title=f"drift: {metric}")
    table.add_column("field")
    table.add_column("approved")
    table.add_column("proposed")
    table.add_column("severity")
    for d in report.diffs:
        table.add_row(d.field, d.left, d.right,
                      f"[{_SEV_STYLE[d.severity]}]{d.severity.value}[/{_SEV_STYLE[d.severity]}]")
    console.print(table)
    raise typer.Exit(code=1)


@guard_app.command("datahub-check")
def guard_datahub_check(
    canonical_dataset_urn: str = typer.Argument(
        ..., help="DataHub URN tagged MetricGuard Canonical",
    ),
    sql_file: Path = typer.Argument(..., help="New/changed definition to check (.sql)"),
):
    """Check SQL against the canonical signature governed inside DataHub.

    Exit codes match local guard check: 0 ok · 1 drift · 2 no graph contract.
    """
    from metricguard.datahub.base import get_datahub_client
    from metricguard.guard.datahub_contracts import check_datahub_drift

    report = check_datahub_drift(
        get_datahub_client(), canonical_dataset_urn, sql_file.read_text(),
    )
    if report.verdict == DriftVerdict.OK:
        console.print(f"[green]✔ {report.message}[/green]")
        raise typer.Exit(code=0)
    if report.verdict == DriftVerdict.NO_CONTRACT:
        console.print(f"[yellow]{report.message}[/yellow]")
        raise typer.Exit(code=2)
    console.print(f"[bold red]✘ {report.message}[/bold red]")
    table = Table(title=f"DataHub contract drift: {report.metric}")
    for column in ("field", "DataHub canonical", "proposed", "severity"):
        table.add_column(column)
    for diff in report.diffs:
        table.add_row(diff.field, diff.left, diff.right, diff.severity.value)
    console.print(table)
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# proposals — the human-approval seam for the agent's write-backs
# ---------------------------------------------------------------------------

@proposals_app.command("list")
def proposals_list(
    all: bool = typer.Option(False, "--all", help="Include executed/rejected"),
):
    """Show staged write-back proposals awaiting review."""
    from metricguard.datahub.proposals import ProposalStatus, ProposalStore

    proposals = ProposalStore().list(status=None if all else ProposalStatus.PENDING)
    if not proposals:
        console.print("[dim]No proposals." + ("" if all else " (try --all)") + "[/dim]")
        return
    table = Table(title="write-back proposals")
    for col in ("id", "status", "kind", "target", "metric", "rationale"):
        table.add_column(col, overflow="fold")
    for p in proposals:
        table.add_row(p.id, p.status.value, p.kind, p.target, p.metric or "—", p.rationale)
    console.print(table)


@proposals_app.command("show")
def proposals_show(proposal_id: str):
    """Full detail of one proposal, including the mutation payload."""
    from metricguard.datahub.proposals import ProposalStore

    proposal = ProposalStore().get(proposal_id)
    if proposal is None:
        console.print(f"[red]No proposal '{proposal_id}'[/red]")
        raise typer.Exit(code=2)
    console.print(Panel(proposal.model_dump_json(indent=2), title=f"proposal {proposal_id}"))


@proposals_app.command("approve")
def proposals_approve(proposal_id: str):
    """HUMAN APPROVAL: execute a staged proposal against DataHub."""
    from metricguard.datahub.base import get_datahub_client
    from metricguard.datahub.proposals import ProposalStore

    store = ProposalStore()
    proposal = store.get(proposal_id)
    if proposal is None:
        console.print(f"[red]No proposal '{proposal_id}'[/red]")
        raise typer.Exit(code=2)

    console.print(Panel(proposal.model_dump_json(indent=2), title="you are about to execute"))
    if not typer.confirm("Write this to DataHub?"):
        console.print("[dim]Aborted — proposal left pending.[/dim]")
        raise typer.Exit(code=1)

    try:
        executed = store.approve(proposal_id, get_datahub_client())
    except Exception as e:  # write failed — proposal stays pending, report loudly
        console.print(f"[red]✗ Write failed — proposal left pending.[/red]\n{e}")
        raise typer.Exit(code=1)
    console.print(f"[green]✔ Executed[/green] {executed.kind} -> {executed.target}. "
                  "Check the DataHub UI.")


@proposals_app.command("reject")
def proposals_reject(proposal_id: str):
    """Reject a staged proposal (kept for the audit trail)."""
    from metricguard.datahub.proposals import ProposalStore

    ProposalStore().reject(proposal_id)
    console.print(f"[yellow]Rejected[/yellow] proposal {proposal_id}.")


# ---------------------------------------------------------------------------
# datahub — connection utilities
# ---------------------------------------------------------------------------

@datahub_app.command("tools")
def datahub_tools():
    """List the tools the DataHub MCP server actually exposes.

    Use this on Day 1-2 to lock down the capability mapping in
    metricguard/datahub/mcp_client.py (_CAPABILITIES).
    """
    if not settings.datahub_mcp_transport:
        console.print("[yellow]DATAHUB_MCP_TRANSPORT is not set — MCP is disabled.[/yellow]")
        raise typer.Exit(code=2)

    from metricguard.datahub.mcp_client import get_mcp_client

    table = Table(title="DataHub MCP tools")
    table.add_column("name")
    table.add_column("description", overflow="fold")
    for t in get_mcp_client().describe_tools():
        table.add_row(t["name"], t["description"])
    console.print(table)


# ---------------------------------------------------------------------------
# agent — the tool-calling decision loop
# ---------------------------------------------------------------------------

@app.command()
def agent(
    goal: str = typer.Argument(..., help='e.g. "Find conflicting definitions of weekly active users"'),
):
    """Run the MetricGuard agent (requires LLM_MODEL + provider API key)."""
    from metricguard.agent.loop import run_agent_result  # lazy: needs provider pkg

    console.print(f"[dim]model: {settings.llm_model}[/dim]\n")
    result = run_agent_result(goal)
    console.print(Panel(result.answer, title="MetricGuard"))
    console.print(f"[dim]audit trace: {result.trace_path} (run {result.run_id})[/dim]")


@app.command()
def ui(
    replay: str = typer.Option(
        "", "--replay", metavar="RUN_ID",
        help="Serve one recorded run in client-timed replay mode",
    ),
    export: str = typer.Option(
        "", "--export", metavar="RUN_ID",
        help="Export one recorded run as a zero-backend static snapshot",
    ),
    output: Path = typer.Option(Path("site"), "--output", "-o", help="Static export directory"),
    host: str = typer.Option("127.0.0.1", help="Mission Control bind address"),
    port: int = typer.Option(8765, min=1, max=65535, help="Mission Control port"),
):
    """Launch the read-only Mission Control dashboard over recorded agent artifacts."""
    from metricguard.agent.runs import AgentRunStore
    from metricguard.ui.server import create_app, export_run

    if replay and export:
        console.print("[red]Choose either --replay or --export, not both.[/red]")
        raise typer.Exit(code=2)

    store = AgentRunStore()
    selected_id = export or replay
    if export:
        run = store.get(export)
        if run is None:
            console.print(f"[red]No agent run '{export}'[/red]")
            raise typer.Exit(code=2)
        index_path = export_run(run, output)
        console.print(f"[green]Mission Control snapshot exported[/green] to {index_path}")
        return

    if replay and store.get(replay) is None:
        console.print(f"[red]No agent run '{replay}'[/red]")
        raise typer.Exit(code=2)
    if not selected_id:
        runs = store.list()
        selected_id = runs[0].id if runs else ""

    import uvicorn

    url = f"http://{host}:{port}"
    suffix = f" (replay {selected_id})" if selected_id else ""
    console.print(f"Mission Control{suffix}: [bold blue]{url}[/bold blue]")
    console.print("[dim]Read-only view. Governance actions remain in DataHub and the CLI.[/dim]")
    uvicorn.run(create_app(store, preferred_run_id=selected_id), host=host, port=port)


@runs_app.command("list")
def runs_list():
    """List agent runs with status and tool-call count."""
    from metricguard.agent.runs import AgentRunStore

    runs = AgentRunStore().list()
    if not runs:
        console.print("[dim]No agent runs recorded.[/dim]")
        return
    table = Table(title="MetricGuard agent runs")
    for column in ("id", "status", "started", "model", "tools", "goal"):
        table.add_column(column, overflow="fold")
    for run in runs:
        table.add_row(
            run.id, run.status.value, run.started_at.isoformat(), run.model,
            str(len(run.tool_traces)), run.goal,
        )
    console.print(table)


@runs_app.command("show")
def runs_show(run_id: str):
    """Show the exact goal, calls, results, errors, and final answer for one run."""
    from metricguard.agent.runs import AgentRunStore

    run = AgentRunStore().get(run_id)
    if run is None:
        console.print(f"[red]No agent run '{run_id}'[/red]")
        raise typer.Exit(code=2)
    console.print(Panel(run.model_dump_json(indent=2), title=f"agent run {run_id}"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_seeds(seeds_dir: Path) -> list[MetricDefinition]:
    candidates: list[MetricDefinition] = []
    for manifest_path in sorted(seeds_dir.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest.get("definitions", []):
            sql = (manifest_path.parent / entry["file"]).read_text()
            md = MetricDefinition(
                name=entry["name"], sql=sql,
                source=entry.get("source", ""), owner=entry.get("owner", ""),
                dialect=manifest.get("dialect", settings.dialect),
            )
            md.signature = extract_signature(md.sql, dialect=md.dialect)
            candidates.append(md)
    return candidates


def _print_conflict(report: ConflictReport) -> None:
    if not report.diffs:
        console.print(f"[green]✔ {report.left_name} and {report.right_name} "
                      f"are semantically identical.[/green]")
        return
    table = Table(title=f"conflict: {report.left_name} vs {report.right_name} "
                        f"(worst: {report.worst_severity.value})")
    table.add_column("field")
    table.add_column(report.left_name, overflow="fold")
    table.add_column(report.right_name, overflow="fold")
    table.add_column("severity")
    table.add_column("why it matters", overflow="fold")
    for d in report.diffs:
        style = _SEV_STYLE[d.severity]
        table.add_row(d.field, d.left, d.right,
                      f"[{style}]{d.severity.value}[/{style}]", d.note)
    console.print(table)


def _explain(candidates: list[MetricDefinition], report: ConflictReport) -> None:
    from metricguard.llm.client import explain_conflict  # lazy: needs API key

    console.print("[dim]Asking the LLM for judgment (explanation + proposals)...[/dim]")
    explanation = explain_conflict(candidates, report)
    console.print(Panel(explanation.summary, title="what's going on"))
    console.print(Panel(explanation.business_impact, title="business impact"))
    for p in sorted(explanation.proposals, key=lambda p: p.rank):
        console.print(Panel(
            f"[bold]based on:[/bold] {p.based_on}\n"
            f"[bold]why:[/bold] {p.rationale}\n"
            f"[bold]tradeoffs:[/bold] {p.tradeoffs}",
            title=f"canonical proposal #{p.rank}",
        ))
    console.print("[dim]Write-back to DataHub requires human approval "
                  "(METRICGUARD_REQUIRE_APPROVAL=true).[/dim]")


if __name__ == "__main__":
    app()
