"""Signal-based clustering of candidate definitions into metric families.

Deterministic signals (name similarity, shared sources, same entity, same
grain) produce a confidence score with visible evidence. Tuned to the seeded
metric families — it does not need to generalize to every warehouse
(scope guardrail, context.md).

The LLM's clustering *judgment* (ambiguous cases) sits on top of this in the
agent loop; the scores here are the evidence it reasons over.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from metricguard.models import CandidateCluster, ClusterEvidence, MetricDefinition

# Signal weights — sum of matched weights becomes the confidence (capped at 1.0)
_W_NAME = 0.35
_W_SOURCES = 0.30
_W_ENTITY = 0.25
_W_GRAIN = 0.10

_PAIR_THRESHOLD = 0.5  # minimum pairwise score to consider two candidates the same family


def cluster_candidates(candidates: list[MetricDefinition]) -> list[CandidateCluster]:
    """Group candidates into metric families using deterministic signals.

    Requires each candidate to have `signature` populated (run the extractor
    first). Uses greedy union-find over pairwise scores.
    """
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    pair_evidence: dict[tuple[int, int], tuple[float, list[ClusterEvidence]]] = {}
    for i in range(n):
        for j in range(i + 1, n):
            score, evidence = _pair_score(candidates[i], candidates[j])
            pair_evidence[(i, j)] = (score, evidence)
            if score >= _PAIR_THRESHOLD:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters: list[CandidateCluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue  # a lone definition is not a conflict candidate
        scores, evidence = [], []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                key = (min(members[a], members[b]), max(members[a], members[b]))
                s, ev = pair_evidence[key]
                scores.append(s)
                evidence.extend(ev)
        clusters.append(CandidateCluster(
            metric_family=_family_name(candidates, members),
            members=[candidates[m].name for m in members],
            confidence=round(min(sum(scores) / len(scores), 1.0), 2),
            evidence=evidence,
        ))
    return sorted(clusters, key=lambda c: -c.confidence)


def _pair_score(a: MetricDefinition, b: MetricDefinition) -> tuple[float, list[ClusterEvidence]]:
    # A governed family label is a hard negative boundary, not a positive shortcut.
    # We still require semantic evidence to group definitions that share a hint,
    # but must never merge explicitly different families merely because they read
    # the same source at the same grain (e.g. weekly revenue vs weekly order count).
    if a.family_hint and b.family_hint and a.family_hint != b.family_hint:
        return 0.0, [ClusterEvidence(
            signal="different_governed_families",
            detail=f"'{a.family_hint}' != '{b.family_hint}'",
            weight=0.0,
        )]

    score = 0.0
    evidence: list[ClusterEvidence] = []

    name_sim = SequenceMatcher(None, _stem(a.name), _stem(b.name)).ratio()
    if name_sim >= 0.5:
        w = _W_NAME * name_sim
        score += w
        evidence.append(ClusterEvidence(
            signal="name_similarity",
            detail=f"'{a.name}' ~ '{b.name}' ({name_sim:.0%})",
            weight=round(w, 2),
        ))

    if a.signature and b.signature:
        # A different reporting grain is a near miss, not a conflict: monthly
        # revenue and weekly revenue may share a source and measure, but answer
        # different business questions.  Likewise, an average is not a sum or
        # count just because it touches the same population.  These hard
        # negatives keep common dashboard decoys out of a discovered family.
        a_grain, b_grain = a.signature.grain, b.signature.grain
        if a_grain and b_grain and a_grain != b_grain:
            return 0.0, [ClusterEvidence(
                signal="different_grain",
                detail=f"'{a_grain}' != '{b_grain}'",
                weight=0.0,
            )]
        a_agg, b_agg = a.signature.aggregation, b.signature.aggregation
        if a_agg and b_agg and a_agg.function != b_agg.function:
            return 0.0, [ClusterEvidence(
                signal="different_aggregation",
                detail=f"'{a_agg.function}' != '{b_agg.function}'",
                weight=0.0,
            )]
        shared = set(a.signature.source_population) & set(b.signature.source_population)
        if shared:
            score += _W_SOURCES
            evidence.append(ClusterEvidence(
                signal="shared_sources", detail=f"both read {sorted(shared)}", weight=_W_SOURCES,
            ))
        if a.signature.entity and a.signature.entity == b.signature.entity:
            score += _W_ENTITY
            evidence.append(ClusterEvidence(
                signal="same_entity", detail=f"both measure '{a.signature.entity}'", weight=_W_ENTITY,
            ))
        if a.signature.grain and a.signature.grain == b.signature.grain:
            score += _W_GRAIN
            evidence.append(ClusterEvidence(
                signal="same_grain", detail=f"both bucket by '{a.signature.grain}'", weight=_W_GRAIN,
            ))

    return round(score, 3), evidence


def _stem(name: str) -> str:
    """Strip team prefixes/suffixes so 'marketing_wau' ~ 'finance_wau'."""
    return name.lower().replace("-", "_")


def _family_name(candidates: list[MetricDefinition], members: list[int]) -> str:
    """Longest common token run across member names, else first member's name."""
    hints = {
        candidates[index].family_hint for index in members
        if candidates[index].family_hint
    }
    if len(hints) == 1:
        return hints.pop()
    token_sets = [set(candidates[m].name.lower().split("_")) for m in members]
    common = set.intersection(*token_sets) if token_sets else set()
    if common:
        return "_".join(sorted(common))
    # Stable fallback: graph search order must never rename a family.
    return sorted(candidates[m].name for m in members)[0]
