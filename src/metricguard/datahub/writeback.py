"""Write-back proposal builders — the canonical-resolution payload.

Given a human/LLM decision about which candidate is canonical, these pure
functions produce `Proposal`s whose `payload` matches the DataHub MCP mutation
tools EXACTLY (arg names verified against the live server 2026-07-05). Nothing
here mutates DataHub — proposals are staged; a human executes them via
`metricguard proposals approve`, the single approval choke point.

Write kinds -> MCP tool (see mcp_client._WRITE_CAPABILITY):
    document    -> save_document        (zero prereq; links to the datasets)
    tag         -> add_tags             (tags auto-create on add)
    description -> update_description    (append a redirect on divergent defs)

Structured properties (the SemanticSignature as governed metadata) are a
separate step: `add_structured_properties` can only *assign* values to property
definitions that already exist, and no MCP tool creates definitions — so those
must be bootstrapped once via the SDK. Deferred; see progress.md #4.
"""

from __future__ import annotations

from metricguard.datahub.proposals import Proposal
from metricguard.models import MetricDefinition

CANONICAL_TAG = "urn:li:tag:metricguard_canonical"
DIVERGENT_TAG = "urn:li:tag:metricguard_divergent"
SIGNATURE_PROP_PREFIX = "urn:li:structuredProperty:metricguard_"


def _signature_property_values(sig) -> dict[str, list[str]]:
    """SemanticSignature -> {structuredProperty urn: [string values]} for add_structured_properties.

    Only non-empty fields are written. Property definitions must exist first
    (scripts/bootstrap_writeback_entities.py --emit).
    """
    values: dict[str, list[str]] = {}

    def put(field: str, items: list) -> None:
        cleaned = [str(i) for i in items if i not in (None, "")]
        if cleaned:
            values[f"{SIGNATURE_PROP_PREFIX}{field}"] = cleaned

    put("aggregation", [sig.aggregation.render()] if sig.aggregation else [])
    put("entity", [sig.entity])
    put("grain", [sig.grain])
    put("timezone", [sig.timezone])
    put("filters", sig.filters)
    put("deduplication", ["true"] if sig.deduplication else [])
    put("null_handling", sig.null_handling)
    put("source_population", sig.source_population)
    return values


def structured_properties_proposal(metric: str, canonical: MetricDefinition) -> Proposal:
    """The money shot: write the canonical SemanticSignature onto the winning
    dataset as governed structured properties."""
    if canonical.signature is None:
        raise ValueError("canonical candidate has no signature to write back")
    return Proposal(
        metric=metric, kind="structured_property", target=canonical.dataset_urn,
        payload={
            "property_values": _signature_property_values(canonical.signature),
            "entity_urns": [canonical.dataset_urn],
        },
        rationale=f"Records the canonical semantic signature of '{metric}' on the "
                  "dataset as governed, queryable metadata.",
    )


def _decision_document(
    metric: str,
    canonical: MetricDefinition,
    divergent: list[MetricDefinition],
    evidence_summary: str = "",
) -> Proposal:
    related = [canonical.dataset_urn, *(d.dataset_urn for d in divergent)]
    superseded = "\n".join(
        f"- `{d.name}`" + (f" (owner: {d.owner})" if d.owner else "") for d in divergent
    ) or "- _(none)_"
    evidence = (
        f"\n\n## Evidence behind this decision\n{evidence_summary.strip()}"
        if evidence_summary.strip() else ""
    )
    content = (
        f"# Canonical definition: {metric}\n\n"
        f"**Chosen canonical:** `{canonical.name}`"
        + (f" (owner: {canonical.owner})" if canonical.owner else "")
        + "\n\n```sql\n"
        f"{canonical.sql.strip()}\n"
        "```\n\n"
        "## Divergent definitions superseded\n"
        f"{superseded}"
        f"{evidence}\n\n"
        "_Resolved by MetricGuard — Semantic Conflict Intelligence._"
    )
    return Proposal(
        metric=metric,
        kind="document",
        target=f"metric:{metric}",
        payload={
            "document_type": "Decision",
            "title": f"Canonical metric definition: {metric}",
            "content": content,
            "related_assets": related,
        },
        rationale=f"Records the canonical choice for '{metric}' and links all "
                  f"{len(related)} competing definitions to the decision.",
    )


def _tag(metric: str, dataset_urn: str, tag_urn: str, why: str) -> Proposal:
    return Proposal(
        metric=metric, kind="tag", target=dataset_urn,
        payload={"tag_urns": [tag_urn], "entity_urns": [dataset_urn]},
        rationale=why,
    )


def _description_redirect(
    metric: str, divergent: MetricDefinition, canonical: MetricDefinition
) -> Proposal:
    note = (
        f"\n\n> ⚠️ **MetricGuard:** this definition diverges from the canonical "
        f"`{canonical.name}` for metric **{metric}**. "
        f"See the MetricGuard decision document before reusing it."
    )
    return Proposal(
        metric=metric, kind="description", target=divergent.dataset_urn,
        payload={
            "entity_urn": divergent.dataset_urn,
            "operation": "append",
            "description": note,
        },
        rationale=f"Warns consumers of '{divergent.name}' that it is not canonical.",
    )


def build_canonical_writeback(
    metric: str,
    canonical: MetricDefinition,
    divergent: list[MetricDefinition],
    *,
    evidence_summary: str = "",
) -> list[Proposal]:
    """Full write-back set for a resolved metric family.

    All candidates must carry a `dataset_urn` (graph-sourced). Produces:
    one decision document, a canonical tag, and per-divergent a divergent tag
    plus a description redirect.
    """
    missing = [c.name for c in (canonical, *divergent) if not c.dataset_urn]
    if missing:
        raise ValueError(
            f"write-back needs graph provenance (dataset_urn); missing on: {missing}. "
            "Discover candidates with `--from-graph`."
        )

    proposals = [_decision_document(metric, canonical, divergent, evidence_summary)]
    if canonical.signature is not None:
        proposals.append(structured_properties_proposal(metric, canonical))
    proposals.append(_tag(metric, canonical.dataset_urn, CANONICAL_TAG,
                          f"Chosen canonical definition for '{metric}'."))
    for d in divergent:
        proposals.append(_tag(metric, d.dataset_urn, DIVERGENT_TAG,
                              f"Diverges from the canonical definition of '{metric}'."))
        proposals.append(_description_redirect(metric, d, canonical))
    return proposals
