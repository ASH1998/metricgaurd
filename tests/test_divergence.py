"""Divergence math — pure deterministic computation over rows."""

from metricguard.divergence.engine import compute_divergence


def rows(*tuples, cols=("week_start", "weekly_active_users")):
    return [dict(zip(cols, t)) for t in tuples]


def test_divergence_basic():
    left = rows(("2026-05-04", 100), ("2026-05-11", 100), ("2026-05-18", 100))
    right = rows(("2026-05-04", 100), ("2026-05-11", 82), ("2026-05-18", 90))

    report = compute_divergence(left, right, key_col="week_start",
                                value_col="weekly_active_users")
    assert report.first_divergence_key == "2026-05-11"
    assert report.max_pct_divergence == 18.0
    assert report.total_abs_divergence == 28.0  # 0 + 18 + 10 — the cumulative gap
    by_key = {p.key: p for p in report.points}
    assert by_key["2026-05-04"].pct_divergence == 0.0
    assert by_key["2026-05-18"].abs_divergence == 10.0


def test_segment_localization():
    cols = ("week_start", "weekly_active_users", "platform")
    left = rows(("w1", 100, "web"), ("w1", 100, "mobile-web"), cols=cols)
    right = rows(("w1", 98, "web"), ("w1", 60, "mobile-web"), cols=cols)

    report = compute_divergence(left, right, key_col="week_start",
                                value_col="weekly_active_users",
                                segment_col="platform")
    # the gap concentrates in mobile-web (40 of 42 total gap)
    assert max(report.segment_localization,
               key=report.segment_localization.get) == "platform=mobile-web"


def test_missing_keys_treated_as_zero():
    left = rows(("w1", 50))
    right = rows(("w2", 50))
    report = compute_divergence(left, right, key_col="week_start",
                                value_col="weekly_active_users")
    assert {p.key for p in report.points} == {"w1", "w2"}
    assert all(p.pct_divergence == 100.0 for p in report.points)
