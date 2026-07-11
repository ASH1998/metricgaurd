from metricguard.datahub.mcp_client import _structured_search_query


def test_human_metric_labels_become_precise_datahub_queries():
    assert _structured_search_query("weekly revenue") == "/q weekly+revenue"
    assert _structured_search_query("weekly_revenue") == "/q weekly+revenue"
    assert _structured_search_query(" /q revenue* ") == "/q revenue*"
    assert _structured_search_query("*") == "*"
