from metricguard.agent.runs import AgentRunStore, RunStatus


def test_agent_run_store_records_tools_and_completion(tmp_path):
    store = AgentRunStore(directory=tmp_path)
    run = store.start("Investigate weekly revenue", "test:model")
    store.record_tool(
        run,
        "tool_investigate_datahub_conflicts",
        {"keyword": "weekly revenue"},
        '{"conflicting_pairs": 3}',
    )
    store.complete(run, "Three definitions conflict.")

    loaded = store.get(run.id)
    assert loaded.status == RunStatus.COMPLETED
    assert loaded.final_answer == "Three definitions conflict."
    assert loaded.tool_traces[0].arguments == {"keyword": "weekly revenue"}
    assert loaded.tool_traces[0].error == ""


def test_agent_run_store_preserves_failure_and_truncates_huge_results(tmp_path):
    store = AgentRunStore(directory=tmp_path)
    run = store.start("Fail safely", "test:model")
    store.record_tool(run, "broken_tool", {}, "x" * 60_000, error="boom")
    store.complete(run, "", status=RunStatus.FAILED, error="provider failed")

    loaded = store.list()[0]
    assert loaded.status == RunStatus.FAILED
    assert loaded.error == "provider failed"
    assert loaded.tool_traces[0].error == "boom"
    assert loaded.tool_traces[0].result.endswith("[trace result truncated at 50,000 characters]")


def test_run_store_exposes_stable_trace_path(tmp_path):
    store = AgentRunStore(directory=tmp_path)
    run = store.start("Trace me", "test:model")
    assert store.path_for(run.id) == tmp_path / f"{run.id}.json"
