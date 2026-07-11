"""Signature comparison + conflict classification (deterministic).

Produces the structured statement of exactly how two definitions disagree:
"these disagree on window + anonymous inclusion + timezone".
"""

from __future__ import annotations

from metricguard.models import (
    ConflictReport,
    FieldDiff,
    SemanticSignature,
    Severity,
)

# How much each disagreeing field matters. Rationale:
#   critical — changes WHAT is measured
#   high     — changes the number materially
#   medium   — can change the number at the margins
_FIELD_SEVERITY: dict[str, Severity] = {
    "aggregation": Severity.CRITICAL,
    "entity": Severity.CRITICAL,
    "source_population": Severity.CRITICAL,
    "grain": Severity.HIGH,
    "timezone": Severity.HIGH,
    "filters": Severity.HIGH,
    "deduplication": Severity.HIGH,
    "null_handling": Severity.MEDIUM,
}

_FIELD_NOTES: dict[str, str] = {
    "aggregation": "The measures themselves differ — these are not the same metric computation.",
    "entity": "Different things are being counted.",
    "source_population": "The definitions read from different underlying populations.",
    "grain": "Time windows differ — weekly vs daily/monthly bucketing will never reconcile.",
    "timezone": "Bucket boundaries shift with the timezone; totals near midnight move between periods.",
    "filters": "Row inclusion rules differ — one definition counts rows the other excludes.",
    "deduplication": "One definition removes duplicate rows the other keeps.",
    "null_handling": "NULLs are treated differently and may be silently dropped or kept.",
}


def _render(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value) if value else "—"
    if hasattr(value, "render"):
        return value.render()  # Aggregation
    return str(value)


def compare_signatures(
    left: SemanticSignature,
    right: SemanticSignature,
    left_name: str = "left",
    right_name: str = "right",
) -> ConflictReport:
    """Field-by-field diff of two semantic signatures."""
    diffs: list[FieldDiff] = []

    for field in SemanticSignature.FIELDS:
        lval, rval = getattr(left, field), getattr(right, field)
        if _values_equal(field, lval, rval):
            continue
        diffs.append(
            FieldDiff(
                field=field,
                left=_render(lval),
                right=_render(rval),
                severity=_FIELD_SEVERITY.get(field, Severity.MEDIUM),
                note=_note_for(field, lval, rval),
            )
        )

    return ConflictReport(left_name=left_name, right_name=right_name, diffs=diffs)


def _values_equal(field: str, lval: object, rval: object) -> bool:
    if field in ("filters", "null_handling", "source_population"):
        return set(lval or []) == set(rval or [])  # order-insensitive
    return lval == rval


def _note_for(field: str, lval: object, rval: object) -> str:
    note = _FIELD_NOTES.get(field, "")
    if field == "filters":
        lset, rset = set(lval or []), set(rval or [])
        only_left, only_right = sorted(lset - rset), sorted(rset - lset)
        parts = []
        if only_left:
            parts.append(f"only in left: {only_left}")
        if only_right:
            parts.append(f"only in right: {only_right}")
        if parts:
            note = f"{note} ({'; '.join(parts)})"
    return note
