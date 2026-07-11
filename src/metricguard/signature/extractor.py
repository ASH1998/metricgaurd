"""Semantic signature extraction — the critical path (Week 1).

Reduces a SQL metric definition to a structured summary:
  { aggregation, entity, grain, timezone, filters, deduplication,
    null_handling, source_population }

Scope guardrail (context.md): this must be *correct on our seeded metric
families*, not general-purpose for every SQL in the wild. Harden against
the seed data first; extend case-by-case.

Deterministic code only — no LLM anywhere in this module.
"""

from __future__ import annotations

from sqlglot import exp

from metricguard.models import Aggregation, SemanticSignature
from metricguard.parsing.normalize import (
    base_tables,
    canonical_predicate,
    parse,
    split_conjuncts,
)

# Aggregate functions we recognize as "the measure"
_AGG_TYPES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)


def extract_signature(sql: str, dialect: str = "postgres") -> SemanticSignature:
    tree = parse(sql, dialect=dialect)
    select = _outermost_select(tree)

    return SemanticSignature(
        aggregation=_extract_aggregation(select),
        entity=_extract_entity(select),
        grain=_extract_grain(select),
        timezone=_extract_timezone(tree),
        filters=_extract_filters(tree, dialect),
        deduplication=_extract_deduplication(select),
        null_handling=_extract_null_handling(tree, dialect),
        source_population=base_tables(tree),
    )


def _outermost_select(tree: exp.Expression) -> exp.Select:
    if isinstance(tree, exp.Select):
        return tree
    select = tree.find(exp.Select)
    if select is None:
        raise ValueError("No SELECT found — is this a metric definition?")
    return select


def _find_aggregate(select: exp.Select) -> exp.AggFunc | None:
    """First aggregate function in the outermost projection list."""
    for projection in select.expressions:
        agg = projection.find(*_AGG_TYPES)
        if agg is not None:
            return agg
    # Fallback: aggregate anywhere in the select (e.g. inside HAVING-driven defs)
    return select.find(*_AGG_TYPES)


def _extract_aggregation(select: exp.Select) -> Aggregation | None:
    agg = _find_aggregate(select)
    if agg is None:
        return None

    arg = agg.this
    distinct = False
    if isinstance(arg, exp.Distinct):
        distinct = True
        arg = arg.expressions[0] if arg.expressions else None

    argument = None
    if arg is not None:
        if isinstance(arg, exp.Star):
            argument = "*"
        elif isinstance(arg, exp.Column):
            argument = arg.name
        else:
            # expression measure, e.g. SUM(quantity * unit_price * (1 - discount_pct/100))
            # — keep the whole canonical expression; collapsing to one column would
            # make different formulas look identical
            argument = arg.sql(normalize=True)

    return Aggregation(function=agg.key.upper(), argument=argument, distinct=distinct)


def _extract_entity(select: exp.Select) -> str | None:
    """The thing being measured — the column inside the primary aggregate."""
    agg = _extract_aggregation(select)
    if agg is None or agg.argument in (None, "*"):
        return agg.argument if agg else None
    return agg.argument


def _extract_grain(select: exp.Select) -> str | None:
    """Time bucketing: DATE_TRUNC unit (or dialect equivalents) in the projection/GROUP BY."""
    for scope in [select, select.args.get("group")]:
        if scope is None:
            continue
        for trunc in scope.find_all(exp.DateTrunc, exp.TimestampTrunc):
            unit = trunc.args.get("unit")
            if unit is not None:
                return unit.name.lower() if hasattr(unit, "name") else str(unit).strip("'").lower()
    return None


def _extract_timezone(tree: exp.Expression) -> str | None:
    """Explicit timezone conversion: AT TIME ZONE 'X' or CONVERT_TIMEZONE('X', ...)."""
    at_tz = tree.find(exp.AtTimeZone)
    if at_tz is not None:
        zone = at_tz.args.get("zone")
        if isinstance(zone, exp.Literal):
            return zone.this
        return zone.sql() if zone is not None else None

    for func in tree.find_all(exp.Anonymous):
        if func.name and func.name.lower() == "convert_timezone" and func.expressions:
            first = func.expressions[0]
            if isinstance(first, exp.Literal):
                return first.this
    return None


def _extract_filters(tree: exp.Expression, dialect: str) -> list[str]:
    """All WHERE predicates across the query (incl. CTEs), canonicalized and sorted.

    Sorting makes filter comparison order-insensitive; canonicalization makes
    it alias/format-insensitive. JOIN ... ON conditions are intentionally NOT
    treated as filters (they define the population via source joins).
    """
    predicates: list[str] = []
    for where in tree.find_all(exp.Where):
        for conjunct in split_conjuncts(where):
            predicates.append(canonical_predicate(conjunct, dialect))
    return sorted(set(predicates))


def _extract_deduplication(select: exp.Select) -> bool:
    """DISTINCT at the row level (SELECT DISTINCT / QUALIFY row_number() = 1)."""
    if select.args.get("distinct") is not None:
        return True
    qualify = select.args.get("qualify")
    if qualify is not None and qualify.find(exp.Window) is not None:
        return True
    return False


def _extract_null_handling(tree: exp.Expression, dialect: str) -> list[str]:
    """COALESCE/IFNULL expressions and IS [NOT] NULL predicates observed."""
    treatments: list[str] = []
    for node in tree.find_all(exp.Coalesce):
        treatments.append(node.sql(dialect=dialect, normalize=True))
    for node in tree.find_all(exp.Is):
        treatments.append(node.sql(dialect=dialect, normalize=True))
    return sorted(set(treatments))
