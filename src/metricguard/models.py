"""Core data models.

`SemanticSignature` is the linchpin of the whole system (see context.md):
both Discovery (conflict comparison) and Guard (drift detection) consume it.
Everything here is a plain, serializable Pydantic model — deterministic code
produces these; the LLM only ever *reads* them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Semantic signature — the structured summary of one SQL metric definition
# ---------------------------------------------------------------------------

class Aggregation(BaseModel):
    """e.g. COUNT(DISTINCT user_id) -> function=COUNT, argument=user_id, distinct=True"""
    function: str
    argument: str | None = None
    distinct: bool = False

    def render(self) -> str:
        inner = f"DISTINCT {self.argument}" if self.distinct else (self.argument or "*")
        return f"{self.function}({inner})"


class SemanticSignature(BaseModel):
    """The semantic fingerprint of one metric definition.

    Field semantics:
      aggregation       — the measure itself (COUNT DISTINCT vs COUNT is a conflict)
      entity            — what is being counted/summed (user_id, session_id, ...)
      grain             — time bucketing (week, day, month) incl. week-start if detectable
      timezone          — explicit tz conversion applied to the time column, if any
      filters           — canonicalized WHERE predicates (sorted, normalized)
      deduplication     — DISTINCT usage outside the aggregate (SELECT DISTINCT, etc.)
      null_handling     — COALESCE/IFNULL/IS NOT NULL treatments observed
      source_population — base tables/views feeding the definition (CTE names excluded)
    """

    aggregation: Aggregation | None = None
    entity: str | None = None
    grain: str | None = None
    timezone: str | None = None
    filters: list[str] = Field(default_factory=list)
    deduplication: bool = False
    null_handling: list[str] = Field(default_factory=list)
    source_population: list[str] = Field(default_factory=list)

    # Comparable field names, in display order (ClassVar so pydantic doesn't
    # treat it as a model field)
    FIELDS: ClassVar[tuple[str, ...]] = (
        "aggregation", "entity", "grain", "timezone",
        "filters", "deduplication", "null_handling", "source_population",
    )


class MetricDefinition(BaseModel):
    """A candidate implementation of a metric, wherever we found it."""
    name: str                      # e.g. "marketing_wau"
    sql: str
    dialect: str = "postgres"
    source: str = ""               # where it came from: dbt model, dashboard, DataHub query, ...
    owner: str = ""                # team/person, if known
    family_hint: str = ""          # governed DataHub family identity, when available
    signature: SemanticSignature | None = None
    # graph provenance — populated when the candidate is discovered from DataHub,
    # so write-back (#4) can target the exact entities. Empty for seed candidates.
    dataset_urn: str = ""
    query_urn: str = ""


# ---------------------------------------------------------------------------
# Conflict detection (Discovery mode)
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    CRITICAL = "critical"   # changes what is being measured (aggregation, entity, population)
    HIGH = "high"           # changes the number materially (grain, timezone, filters, dedup)
    MEDIUM = "medium"       # can change the number (null handling)
    COSMETIC = "cosmetic"   # no semantic difference


class FieldDiff(BaseModel):
    field: str
    left: str
    right: str
    severity: Severity
    note: str = ""


class ConflictReport(BaseModel):
    """Deterministic, field-by-field statement of how two definitions disagree."""
    left_name: str
    right_name: str
    diffs: list[FieldDiff] = Field(default_factory=list)

    @property
    def is_conflict(self) -> bool:
        return any(d.severity != Severity.COSMETIC for d in self.diffs)

    @property
    def worst_severity(self) -> Severity:
        order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.COSMETIC]
        for sev in order:
            if any(d.severity == sev for d in self.diffs):
                return sev
        return Severity.COSMETIC


# ---------------------------------------------------------------------------
# Divergence (executed proof of disagreement)
# ---------------------------------------------------------------------------

class DivergencePoint(BaseModel):
    key: str                       # e.g. the week bucket "2026-05-11"
    left_value: float
    right_value: float
    abs_divergence: float
    pct_divergence: float          # relative to left, in percent


class DivergenceReport(BaseModel):
    left_name: str
    right_name: str
    points: list[DivergencePoint] = Field(default_factory=list)
    mean_pct_divergence: float = 0.0
    max_pct_divergence: float = 0.0
    first_divergence_key: str | None = None
    # Optional: where the gap concentrates, e.g. {"platform=mobile-web": 71.2}
    segment_localization: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Clustering (candidate grouping into metric families)
# ---------------------------------------------------------------------------

class ClusterEvidence(BaseModel):
    signal: str                    # "name_similarity" | "shared_sources" | "same_entity" | "same_grain"
    detail: str
    weight: float


class CandidateCluster(BaseModel):
    metric_family: str
    members: list[str]             # MetricDefinition names
    confidence: float              # 0..1
    evidence: list[ClusterEvidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Guard mode (contracts + drift)
# ---------------------------------------------------------------------------

class Contract(BaseModel):
    """An approved canonical signature, captured after human approval."""
    metric: str
    signature: SemanticSignature
    approved_by: str = ""
    approved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    canonical_sql: str = ""


class DriftVerdict(str, Enum):
    OK = "ok"                      # semantically identical (cosmetic changes only)
    DRIFT = "drift"                # semantic break from the approved contract
    NO_CONTRACT = "no_contract"


class DriftReport(BaseModel):
    metric: str
    verdict: DriftVerdict
    diffs: list[FieldDiff] = Field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# LLM outputs (judgment layer) — structured so LangChain can enforce the schema
# ---------------------------------------------------------------------------

class CanonicalProposal(BaseModel):
    """One ranked option for the canonical definition, produced by the LLM."""
    rank: int
    based_on: str                  # which candidate definition it favors
    rationale: str
    tradeoffs: str


class ConflictExplanation(BaseModel):
    summary: str                   # plain-language explanation of the conflict
    business_impact: str
    proposals: list[CanonicalProposal] = Field(default_factory=list)
