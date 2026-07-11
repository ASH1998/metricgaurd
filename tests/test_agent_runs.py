import json

from metricguard.agent.runs import AgentRunStore, AutonomousOutcome, RunOrigin, RunStatus


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


def test_run_store_persists_autonomous_provenance(tmp_path):
    store = AgentRunStore(directory=tmp_path)
    run = store.start(
        "Investigate a changed query",
        "test:model",
        origin=RunOrigin.SENTINEL,
        trigger={"query_urn": "urn:li:query:changed"},
    )
    run.autonomous_outcome = AutonomousOutcome.NEEDS_HUMAN_DECISION
    store.save(run)

    loaded = store.get(run.id)
    assert loaded.origin == RunOrigin.SENTINEL
    assert loaded.trigger["query_urn"] == "urn:li:query:changed"
    assert loaded.autonomous_outcome == AutonomousOutcome.NEEDS_HUMAN_DECISION


def test_run_store_uses_atomic_replace_and_leaves_no_temporary_file(monkeypatch, tmp_path):
    store = AgentRunStore(directory=tmp_path)
    replacements = []

    from metricguard.agent import runs

    real_replace = runs.os.replace

    def record_replace(source, destination):
        assert source.exists()
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(runs.os, "replace", record_replace)
    run = store.start("Write atomically", "test:model")

    assert replacements[0][1] == store.path_for(run.id)
    assert not list(tmp_path.glob("*.tmp"))
    assert store.get(run.id) == run


def test_run_store_tolerates_incomplete_and_invalid_files(tmp_path):
    store = AgentRunStore(directory=tmp_path)
    valid = store.start("Keep valid run", "test:model")
    (tmp_path / "partial.json").write_text('{"id": "partial"')
    (tmp_path / "invalid.json").write_text(json.dumps({"id": "invalid"}))

    assert store.get("partial") is None
    assert store.get("invalid") is None
    assert [run.id for run in store.list()] == [valid.id]
