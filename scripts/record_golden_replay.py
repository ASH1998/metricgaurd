"""Record the no-LLM, frozen-evidence Mission Control replay.

The golden replay is intentionally deterministic: it inspects the committed
catalog, executes the three approved proof pairs, and refuses to write an
artifact if any result differs from ``examples/warehouse_proofs.json``. It
never sends SQL or warehouse output to an LLM, and therefore replays without
live DataHub infrastructure.

Run from the repository root:

    uv run python scripts/record_golden_replay.py
"""

from __future__ import annotations

import json
from pathlib import Path

from metricguard.agent.runs import AgentRun, AgentRunStore, RunStatus
from metricguard.clustering.grouper import cluster_candidates
from metricguard.comparison.diff import compare_signatures
from metricguard.divergence.engine import compute_divergence
from metricguard.execution.base import get_executor
from metricguard.models import MetricDefinition
from metricguard.signature.extractor import extract_signature


REPO = Path(__file__).resolve().parent.parent
GOLDEN_DIRECTORY = REPO / "examples" / "golden_run"
GOLDEN_RUN_ID = "golden_frozen_audit"
FREEZE_MANIFEST = REPO / "examples" / "warehouse_proofs.json"

PROOF_PAIRS = (
    ("weekly_revenue", "exec_dashboard_weekly_revenue", "finance_weekly_revenue"),
    ("weekly_order_volume", "fulfillment_order_volume", "exec_checkout_count"),
    ("weekly_refund_amount", "finance_refund_liability", "support_customer_refunds"),
)


def _frozen_proofs() -> dict[str, dict[str, object]]:
    payload = json.loads(FREEZE_MANIFEST.read_text())
    return {proof["id"]: proof for proof in payload["proofs"]}


def _require_frozen_result(
    proof_id: str,
    result: dict[str, object],
    expected: dict[str, object],
) -> None:
    fields = (
        "periods_compared",
        "mean_pct_divergence",
        "max_pct_divergence",
        "total_abs_divergence",
        "first_divergence_key",
    )
    mismatches = [
        f"{field}: expected {expected[field]!r}, got {result.get(field)!r}"
        for field in fields
        if result.get(field) != expected[field]
    ]
    if mismatches:
        raise RuntimeError(f"{proof_id} diverged from frozen evidence: {'; '.join(mismatches)}")


def _load_catalog() -> tuple[list[MetricDefinition], dict[str, str]]:
    """Load all queryable positive definitions and near-miss controls."""
    candidates: list[MetricDefinition] = []
    control_reasons: dict[str, str] = {}
    manifests = sorted((REPO / "seeds" / "metric_families").glob("*/manifest.json"))
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["definitions"]:
            candidate = MetricDefinition(
                name=entry["name"],
                sql=(manifest_path.parent / entry["file"]).read_text(),
                source=entry.get("source", ""),
                owner=entry.get("owner", ""),
                family_hint=manifest["family"],
            )
            candidate.signature = extract_signature(candidate.sql)
            candidates.append(candidate)

    controls_path = REPO / "seeds" / "negative_controls" / "manifest.json"
    controls = json.loads(controls_path.read_text())
    for entry in controls["definitions"]:
        if entry["kind"] != "query":
            continue
        candidate = MetricDefinition(
            name=entry["name"],
            sql=(controls_path.parent / entry["file"]).read_text(),
            source=entry["source"],
            owner=entry["owner"],
        )
        candidate.signature = extract_signature(candidate.sql)
        candidates.append(candidate)
        control_reasons[candidate.name] = entry["reason"]
    return candidates, control_reasons


def _catalog_report(candidates: list[MetricDefinition], control_reasons: dict[str, str]) -> dict[str, object]:
    clusters = cluster_candidates(candidates)
    by_name = {candidate.name: candidate for candidate in candidates}
    conflict_count = 0
    critical_count = 0
    cluster_payloads = []
    clustered_names = {name for cluster in clusters for name in cluster.members}
    for cluster in clusters:
        conflicts = []
        members = [by_name[name] for name in cluster.members]
        for index, left in enumerate(members):
            for right in members[index + 1:]:
                conflict = compare_signatures(
                    left.signature, right.signature, left_name=left.name, right_name=right.name,
                )
                if not conflict.is_conflict:
                    continue
                conflict_count += 1
                if conflict.worst_severity.value == "critical":
                    critical_count += 1
                conflicts.append(conflict.model_dump(mode="json"))
        cluster_payloads.append({**cluster.model_dump(mode="json"), "conflicts": conflicts})
    unclustered = [candidate.name for candidate in candidates if candidate.name not in clustered_names]
    return {
        "source": "Committed MetricGuard seed catalog (no LLM, no live graph dependency)",
        "summary": {
            "candidate_count": len(candidates),
            "metric_family_count": len(clusters),
            "conflicting_pairs": conflict_count,
            "critical_pairs": critical_count,
        },
        "clusters": cluster_payloads,
        "unclustered_query_candidates": unclustered,
        "negative_control_reasons": {
            name: control_reasons[name] for name in unclustered if name in control_reasons
        },
    }


def main() -> None:
    frozen = _frozen_proofs()
    store = AgentRunStore(GOLDEN_DIRECTORY)
    run = AgentRun(
        id=GOLDEN_RUN_ID,
        goal=(
            "Deterministic flagship audit: scan the frozen catalog and prove the three "
            "frozen warehouse conflicts without an LLM."
        ),
        model="deterministic:frozen catalog + Postgres proofs",
    )
    store.save(run)

    try:
        print("Reading the frozen catalog...", flush=True)
        candidates, control_reasons = _load_catalog()
        investigation = _catalog_report(candidates, control_reasons)
        print("Catalog read complete; recording deterministic proofs...", flush=True)
        store.record_tool(
            run,
            "tool_investigate_frozen_catalog",
            {"catalog": "seeds/metric_families + seeds/negative_controls", "mode": "deterministic_no_llm"},
            json.dumps(investigation, indent=2),
        )

        by_name = {candidate.name: candidate for candidate in candidates}
        executor = get_executor()
        proof_results: dict[str, dict[str, object]] = {}
        for proof_id, left_name, right_name in PROOF_PAIRS:
            missing = [name for name in (left_name, right_name) if name not in by_name]
            if missing:
                raise RuntimeError(f"{proof_id} candidates missing from frozen catalog: {', '.join(missing)}")
            expected = frozen[proof_id]
            report = compute_divergence(
                executor.query(by_name[left_name].sql),
                executor.query(by_name[right_name].sql),
                key_col=str(expected["key_col"]),
                value_col=str(expected["value_col"]),
                left_name=left_name,
                right_name=right_name,
            )
            result = {**report.model_dump(mode="json"), "periods_compared": len(report.points)}
            _require_frozen_result(proof_id, result, expected)
            proof_results[proof_id] = result
            print(f"Verified {proof_id} against the frozen manifest.", flush=True)
            store.record_tool(
                run,
                "tool_run_divergence",
                {
                    "left_name": left_name,
                    "right_name": right_name,
                    "key_col": expected["key_col"],
                    "value_col": expected["value_col"],
                },
                json.dumps(result, indent=2),
            )

        excluded = sorted(investigation["unclustered_query_candidates"])
        store.record_tool(
            run,
            "deterministic_freeze_check",
            {"manifest": str(FREEZE_MANIFEST.relative_to(REPO))},
            json.dumps({
                "status": "matched_frozen_manifest",
                "proof_ids": list(proof_results),
                "unclustered_query_candidates": excluded,
                "note": "No LLM was invoked; this replay contains deterministic catalog and warehouse evidence only.",
            }, indent=2),
        )

        summary = investigation["summary"]
        revenue = proof_results["weekly_revenue"]
        order_volume = proof_results["weekly_order_volume"]
        refunds = proof_results["weekly_refund_amount"]
        store.complete(
            run,
            "\n".join((
                "### Frozen evidence audit (no LLM)",
                "",
                f"The frozen catalog yielded {summary['candidate_count']} query candidates in "
                f"{summary['metric_family_count']} conflict families; unclustered query controls: "
                f"{', '.join(excluded) or 'none'}.",
                "",
                f"- Weekly revenue: {revenue['mean_pct_divergence']}% mean / "
                f"{revenue['max_pct_divergence']}% max; cumulative gap "
                f"${revenue['total_abs_divergence']:,.0f}.",
                f"- Weekly order volume: {order_volume['mean_pct_divergence']}% mean / "
                f"{order_volume['max_pct_divergence']}% max; cumulative gap "
                f"{order_volume['total_abs_divergence']:,.0f} orders.",
                f"- Weekly refunds: {refunds['mean_pct_divergence']}% mean / "
                f"{refunds['max_pct_divergence']}% max; cumulative gap "
                f"${refunds['total_abs_divergence']:,.0f}.",
                "",
                "All three live proofs matched the frozen manifest. This evidence-only run "
                "does not make or stage a canonical recommendation; the human approval gate remains unchanged.",
            )),
            status=RunStatus.COMPLETED,
        )
    except Exception as exc:
        store.complete(run, "", status=RunStatus.FAILED, error=str(exc))
        raise

    print(f"Golden replay recorded at {store.path_for(run.id)}")


if __name__ == "__main__":
    main()
