"""Guard contracts backed by governed SemanticSignature properties in DataHub."""

from __future__ import annotations

import re
from typing import Any

from metricguard.comparison.diff import compare_signatures
from metricguard.config import settings
from metricguard.datahub.base import DataHubClient
from metricguard.datahub.writeback import CANONICAL_TAG, SIGNATURE_PROP_PREFIX
from metricguard.models import (
    Aggregation,
    DriftReport,
    DriftVerdict,
    SemanticSignature,
)
from metricguard.signature.extractor import extract_signature


def signature_from_datahub_entity(entity: dict[str, Any]) -> SemanticSignature | None:
    """Rehydrate the canonical signature written by MetricGuard's MCP proposals."""
    tag_urns = {
        (item.get("tag") or {}).get("urn", "")
        for item in (entity.get("tags") or {}).get("tags", [])
    }
    if CANONICAL_TAG not in tag_urns:
        return None

    raw: dict[str, list[str]] = {}
    for item in (entity.get("structuredProperties") or {}).get("properties", []):
        prop = item.get("structuredProperty") or {}
        urn = prop.get("urn", "")
        qualified_name = (prop.get("definition") or {}).get("qualifiedName", "")
        field = ""
        if urn.startswith(SIGNATURE_PROP_PREFIX):
            field = urn.removeprefix(SIGNATURE_PROP_PREFIX)
        elif qualified_name.startswith("metricguard_"):
            field = qualified_name.removeprefix("metricguard_")
        if field not in SemanticSignature.FIELDS:
            continue
        values = []
        for value in item.get("values", []):
            scalar = next(
                (value[key] for key in ("stringValue", "numberValue") if key in value),
                None,
            )
            if scalar is not None:
                values.append(str(scalar))
        raw[field] = values

    if not raw:
        return None
    return SemanticSignature(
        aggregation=_parse_aggregation(_first(raw.get("aggregation"))),
        entity=_first(raw.get("entity")),
        grain=_first(raw.get("grain")),
        timezone=_first(raw.get("timezone")),
        filters=raw.get("filters", []),
        deduplication=(_first(raw.get("deduplication")) or "").lower() == "true",
        null_handling=raw.get("null_handling", []),
        source_population=raw.get("source_population", []),
    )


def check_datahub_drift(
    client: DataHubClient,
    canonical_dataset_urn: str,
    proposed_sql: str,
    *,
    dialect: str | None = None,
) -> DriftReport:
    """Compare proposed SQL to the approved contract stored on a DataHub asset."""
    entity = client.get_entities(canonical_dataset_urn)
    if not isinstance(entity, dict):
        entity = entity[0] if entity else {}
    metric = _metric_name(entity, canonical_dataset_urn)
    canonical = signature_from_datahub_entity(entity)
    if canonical is None:
        return DriftReport(
            metric=metric,
            verdict=DriftVerdict.NO_CONTRACT,
            message=(
                "The DataHub asset is not tagged MetricGuard Canonical or has no "
                "MetricGuard signature properties. Resolve and approve it first."
            ),
        )
    proposed = extract_signature(proposed_sql, dialect=dialect or settings.dialect)
    comparison = compare_signatures(
        canonical, proposed,
        left_name=f"DataHub contract:{metric}", right_name="proposed change",
    )
    if comparison.is_conflict:
        fields = ", ".join(diff.field for diff in comparison.diffs)
        return DriftReport(
            metric=metric,
            verdict=DriftVerdict.DRIFT,
            diffs=comparison.diffs,
            message=f"SEMANTIC BREAK from DataHub canonical '{metric}': {fields} changed.",
        )
    return DriftReport(
        metric=metric,
        verdict=DriftVerdict.OK,
        message="No semantic drift from the DataHub canonical signature.",
    )


def _parse_aggregation(rendered: str | None) -> Aggregation | None:
    if not rendered:
        return None
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\((DISTINCT\s+)?(.*)\)", rendered.strip())
    if not match:
        raise ValueError(f"Invalid MetricGuard aggregation property: {rendered!r}")
    argument = match.group(3).strip()
    return Aggregation(
        function=match.group(1).upper(),
        argument=None if argument == "*" else argument,
        distinct=bool(match.group(2)),
    )


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None


def _metric_name(entity: dict[str, Any], fallback: str) -> str:
    properties = entity.get("properties") or {}
    custom = properties.get("customProperties") or {}
    if isinstance(custom, list):
        custom = {item.get("key"): item.get("value") for item in custom}
    return custom.get("metric_family") or properties.get("name") or fallback
