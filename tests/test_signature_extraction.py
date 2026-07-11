"""Signature engine correctness on the seeded definitions.

Milestone 1 gate: given the rigged seed SQL, the extractor must recover every
semantic dimension we rigged. If these fail, everything downstream is wrong.
"""

from metricguard.signature.extractor import extract_signature


def test_marketing_signature(marketing_sql):
    sig = extract_signature(marketing_sql)
    assert sig.aggregation is not None
    assert sig.aggregation.function == "COUNT"
    assert sig.aggregation.distinct is True
    assert sig.entity == "user_id"
    assert sig.grain == "week"
    assert sig.timezone is None                      # UTC by omission
    assert sig.source_population == ["events"]
    assert any("event_type" in f for f in sig.filters)


def test_product_signature(product_sql):
    sig = extract_signature(product_sql)
    assert sig.aggregation.distinct is True
    assert sig.timezone == "America/New_York"        # the rigged tz conflict
    assert any("is_anonymous" in f for f in sig.filters)
    assert any("heartbeat" in f for f in sig.filters)


def test_finance_signature(finance_sql):
    sig = extract_signature(finance_sql)
    assert sig.aggregation.function == "COUNT"
    assert sig.aggregation.distinct is False         # the rigged non-distinct count
    assert sig.source_population == ["billable_events"]  # different population


def test_cosmetic_variants_produce_identical_signatures():
    """Aliases, casing, whitespace, and predicate order must not matter."""
    a = """
        SELECT DATE_TRUNC('week', e.event_at) AS wk,
               COUNT(DISTINCT e.user_id) AS wau
        FROM events e
        WHERE e.event_type = 'click' AND e.is_anonymous = FALSE
        GROUP BY 1
    """
    b = """
        select date_trunc('week', ev.event_at) as week_bucket,
            count(distinct ev.user_id)
        from events AS ev
        where ev.is_anonymous = false
          and ev.event_type = 'click'
        group by 1
    """
    assert extract_signature(a) == extract_signature(b)


def test_select_distinct_is_deduplication():
    sig = extract_signature("SELECT DISTINCT user_id FROM events")
    assert sig.deduplication is True


def test_cte_names_excluded_from_population():
    sql = """
        WITH active AS (SELECT user_id, event_at FROM events WHERE is_anonymous = FALSE)
        SELECT DATE_TRUNC('week', event_at) AS wk, COUNT(DISTINCT user_id)
        FROM active
        GROUP BY 1
    """
    sig = extract_signature(sql)
    assert sig.source_population == ["events"]       # 'active' is a CTE, not a source
    assert any("is_anonymous" in f for f in sig.filters)  # CTE filters still count
