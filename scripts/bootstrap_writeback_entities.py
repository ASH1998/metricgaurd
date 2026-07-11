"""One-time bootstrap for MetricGuard write-back entities.

DataHub Core's MCP mutation tools can *associate* tags/terms/structured-props
with datasets, but they cannot *create* the definitions — and `add_tags` fails
with "Urn does not exist" if the tag entity isn't there first. This script
creates those definitions via the DataHub SDK (the same emitter simulate_org.py
uses). Idempotent: re-emitting a TagProperties aspect just overwrites it.

    .venv/bin/python scripts/bootstrap_writeback_entities.py --emit
    .venv/bin/python scripts/bootstrap_writeback_entities.py            # dry-run

After this, `metricguard proposals approve <tag-proposal>` succeeds.
"""

from __future__ import annotations

import argparse
import sys

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    StructuredPropertyDefinitionClass,
    TagPropertiesClass,
)
from dotenv import dotenv_values

# tag urn -> (display name, description). Must match writeback.CANONICAL/DIVERGENT_TAG.
TAGS = {
    "urn:li:tag:metricguard_canonical": (
        "MetricGuard Canonical",
        "The definition MetricGuard resolved as canonical for this metric family.",
    ),
    "urn:li:tag:metricguard_divergent": (
        "MetricGuard Divergent",
        "A definition that diverges from the canonical one for this metric family.",
    ),
}

# One structured property per SemanticSignature field — the canonical signature
# becomes governed metadata on the winning dataset. Names match
# writeback.SIGNATURE_PROP_PREFIX + <field>.
SIGNATURE_FIELDS = {
    "aggregation": "Aggregation / measure",
    "entity": "Entity measured",
    "grain": "Time grain",
    "timezone": "Timezone",
    "filters": "Filter predicates",
    "deduplication": "Deduplication",
    "null_handling": "Null handling",
    "source_population": "Source population",
}


def build() -> list[MetadataChangeProposalWrapper]:
    mcps = [
        MetadataChangeProposalWrapper(
            entityUrn=urn, aspect=TagPropertiesClass(name=name, description=desc)
        )
        for urn, (name, desc) in TAGS.items()
    ]
    for field, disp in SIGNATURE_FIELDS.items():
        mcps.append(MetadataChangeProposalWrapper(
            entityUrn=f"urn:li:structuredProperty:metricguard_{field}",
            aspect=StructuredPropertyDefinitionClass(
                qualifiedName=f"metricguard_{field}",
                displayName=f"MetricGuard: {disp}",
                valueType="urn:li:dataType:datahub.string",
                cardinality="MULTIPLE",
                entityTypes=["urn:li:entityType:datahub.dataset"],
                description=f"Canonical {disp.lower()} for this metric, written back by MetricGuard.",
            ),
        ))
    return mcps


def emit(mcps: list[MetadataChangeProposalWrapper]) -> None:
    from datahub.emitter.rest_emitter import DataHubRestEmitter

    env = dotenv_values(".env")
    gms = (env.get("DATAHUB_GMS_URL") or "").strip()
    token = (env.get("DATAHUB_TOKEN") or "").strip()
    if not gms:
        sys.exit("DATAHUB_GMS_URL not set in .env")
    print(f"Connecting to {gms} ...")
    emitter = DataHubRestEmitter(gms_server=gms, token=token)
    emitter.test_connection()
    for mcp in mcps:
        emitter.emit(mcp)
        print(f"  created {mcp.entityUrn}")
    print("Done. Tags + structured properties now exist — write-back will succeed.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit", action="store_true", help="push to DataHub")
    args = ap.parse_args()
    mcps = build()
    if args.emit:
        emit(mcps)
    else:
        for m in mcps:
            print(f"[dry-run] {m.entityUrn}")
        print("\nNothing sent. Use --emit to create the tags.")


if __name__ == "__main__":
    main()
