"""Divergence engine — executed proof of how much definitions disagree.

Pure deterministic math over query results. Produces the
"18% divergence since May 14, concentrated in mobile-web" story.

Inputs are rows already fetched via the WarehouseExecutor abstraction;
this module never talks to a database itself.
"""

from __future__ import annotations

from metricguard.execution.base import Row
from metricguard.models import DivergencePoint, DivergenceReport


def compute_divergence(
    left_rows: list[Row],
    right_rows: list[Row],
    key_col: str,
    value_col: str,
    left_name: str = "left",
    right_name: str = "right",
    segment_col: str | None = None,
) -> DivergenceReport:
    """Join two result sets on `key_col` and quantify per-key divergence.

    - abs/% divergence per key (percent relative to the left value)
    - mean/max percent divergence
    - first key (sorted ascending) where the values disagree
    - optional segment localization if `segment_col` present in both inputs
    """
    left_by_key = _index(left_rows, key_col, value_col)
    right_by_key = _index(right_rows, key_col, value_col)

    points: list[DivergencePoint] = []
    for key in sorted(set(left_by_key) | set(right_by_key)):
        lv = float(left_by_key.get(key, 0.0))
        rv = float(right_by_key.get(key, 0.0))
        abs_div = abs(lv - rv)
        pct_div = (abs_div / abs(lv) * 100.0) if lv != 0 else (100.0 if rv != 0 else 0.0)
        points.append(DivergencePoint(
            key=key, left_value=lv, right_value=rv,
            abs_divergence=round(abs_div, 4), pct_divergence=round(pct_div, 2),
        ))

    diverging = [p for p in points if p.abs_divergence > 0]
    report = DivergenceReport(
        left_name=left_name,
        right_name=right_name,
        points=points,
        mean_pct_divergence=round(sum(p.pct_divergence for p in points) / len(points), 2) if points else 0.0,
        max_pct_divergence=max((p.pct_divergence for p in points), default=0.0),
        total_abs_divergence=round(sum(p.abs_divergence for p in points), 2),
        first_divergence_key=diverging[0].key if diverging else None,
    )

    if segment_col is not None:
        report.segment_localization = _localize_segments(
            left_rows, right_rows, segment_col, value_col
        )
    return report


def _key_str(value: object) -> str:
    """Render join keys compactly: midnight datetimes become plain dates."""
    if hasattr(value, "hour") and hasattr(value, "date"):
        if (value.hour, value.minute, value.second) == (0, 0, 0):
            return value.date().isoformat()
    return str(value)


def _index(rows: list[Row], key_col: str, value_col: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        key = _key_str(row[key_col])
        out[key] = out.get(key, 0.0) + float(row[value_col] or 0)
    return out


def _localize_segments(
    left_rows: list[Row], right_rows: list[Row], segment_col: str, value_col: str
) -> dict[str, float]:
    """Share of the total absolute gap contributed by each segment value."""
    def by_segment(rows: list[Row]) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in rows:
            if segment_col in row:
                seg = str(row[segment_col])
                out[seg] = out.get(seg, 0.0) + float(row[value_col] or 0)
        return out

    lseg, rseg = by_segment(left_rows), by_segment(right_rows)
    gaps = {seg: abs(lseg.get(seg, 0.0) - rseg.get(seg, 0.0)) for seg in set(lseg) | set(rseg)}
    total_gap = sum(gaps.values())
    if total_gap == 0:
        return {}
    return {
        f"{segment_col}={seg}": round(gap / total_gap * 100.0, 1)
        for seg, gap in sorted(gaps.items(), key=lambda kv: -kv[1])
        if gap > 0
    }
