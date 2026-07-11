"""SQL parsing + normalization layer (sqlglot).

Goal: two definitions that differ only cosmetically (aliases, formatting,
casing, predicate order) should normalize to comparable forms, so the
signature extractor sees semantics, not style.

Deterministic code only — no LLM anywhere in this module.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers


def parse(sql: str, dialect: str = "postgres") -> exp.Expression:
    """Parse SQL into a normalized sqlglot AST.

    Normalizations applied:
      - identifier casing (lowercased per dialect rules)
      - table aliases resolved to real table names (`e.user_id` -> `events.user_id`),
        so alias choice can't leak into signatures
      - comments stripped (sqlglot drops them on parse by default)
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    tree = normalize_identifiers(tree, dialect=dialect)
    tree = _resolve_column_qualifiers(tree)
    return tree


def _resolve_column_qualifiers(tree: exp.Expression) -> exp.Expression:
    """Rewrite column qualifiers that are table aliases to the real table name.

    Known limit (fine for seed scope): the same table joined twice under two
    aliases collapses to one qualifier.
    """
    alias_map: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        if table.alias and table.alias != table.name:
            alias_map[table.alias] = table.name
    if alias_map:
        for col in tree.find_all(exp.Column):
            if col.table and col.table in alias_map:
                col.set("table", exp.to_identifier(alias_map[col.table]))
    return tree


def canonicalize(sql: str, dialect: str = "postgres") -> str:
    """Return a canonical single-line rendering of the query.

    Useful for cosmetic-equality checks: two queries with identical
    canonical text are byte-for-byte the same semantics as far as
    formatting/aliasing normalization can prove.
    """
    tree = parse(sql, dialect=dialect)
    return tree.sql(dialect=dialect, normalize=True, comments=False)


def canonical_predicate(node: exp.Expression, dialect: str = "postgres") -> str:
    """Canonical text for a single predicate (used for filter comparison)."""
    return node.sql(dialect=dialect, normalize=True, comments=False)


def split_conjuncts(where: exp.Where | None) -> list[exp.Expression]:
    """Split a WHERE clause into its top-level AND-ed predicates."""
    if where is None:
        return []
    return list(where.this.flatten()) if isinstance(where.this, exp.And) else [where.this]


def cte_names(tree: exp.Expression) -> set[str]:
    """Names defined by WITH clauses (must be excluded from source_population)."""
    return {cte.alias_or_name for cte in tree.find_all(exp.CTE)}


def base_tables(tree: exp.Expression) -> list[str]:
    """Physical tables/views referenced, excluding CTE self-references."""
    ctes = cte_names(tree)
    tables: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if name and name not in ctes:
            fq = ".".join(p for p in [table.db, table.name] if p)
            if fq not in tables:
                tables.append(fq)
    return sorted(tables)
