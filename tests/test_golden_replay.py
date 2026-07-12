import json
from pathlib import Path

from metricguard.agent.runs import AgentRunStore
from metricguard.ui.contracts import build_mission_control_run
from metricguard.ui.replay import GOLDEN_REPLAY_ALIAS, resolve_replay_run


ROOT = Path(__file__).resolve().parents[1]


def test_golden_alias_resolves_the_single_committed_replay(tmp_path):
    local_store = AgentRunStore(tmp_path / "local")
    golden_directory = tmp_path / "examples" / "golden_run"
    golden_store = AgentRunStore(golden_directory)
    golden = golden_store.start("Flagship frozen warehouse audit", "test:model")

    resolved = resolve_replay_run(
        GOLDEN_REPLAY_ALIAS,
        local_store=local_store,
        golden_directory=golden_directory,
    )

    assert resolved is not None
    store, run_id = resolved
    assert store.directory == golden_directory
    assert run_id == golden.id


def test_replay_prefers_local_ids_and_accepts_golden_deep_links(tmp_path):
    local_store = AgentRunStore(tmp_path / "local")
    local = local_store.start("Local run", "test:model")
    golden_directory = tmp_path / "examples" / "golden_run"
    golden_store = AgentRunStore(golden_directory)
    golden = golden_store.start("Flagship frozen warehouse audit", "test:model")

    local_resolved = resolve_replay_run(
        local.id, local_store=local_store, golden_directory=golden_directory,
    )
    golden_resolved = resolve_replay_run(
        golden.id, local_store=local_store, golden_directory=golden_directory,
    )

    assert local_resolved == (local_store, local.id)
    assert golden_resolved is not None
    golden_replay_store, golden_replay_id = golden_resolved
    assert golden_replay_store.directory == golden_directory
    assert golden_replay_id == golden.id


def test_golden_alias_requires_exactly_one_shipped_run(tmp_path):
    local_store = AgentRunStore(tmp_path / "local")
    golden_directory = tmp_path / "examples" / "golden_run"
    golden_store = AgentRunStore(golden_directory)
    golden_store.start("One", "test:model")
    golden_store.start("Two", "test:model")

    assert resolve_replay_run(
        GOLDEN_REPLAY_ALIAS,
        local_store=local_store,
        golden_directory=golden_directory,
    ) is None


def test_shipped_golden_replay_has_all_frozen_proofs(tmp_path):
    golden_directory = ROOT / "examples" / "golden_run"
    resolved = resolve_replay_run(
        GOLDEN_REPLAY_ALIAS,
        local_store=AgentRunStore(tmp_path / "local"),
        golden_directory=golden_directory,
    )
    frozen = {
        proof["id"]: proof
        for proof in json.loads((ROOT / "examples" / "warehouse_proofs.json").read_text())["proofs"]
    }

    assert resolved is not None
    store, run_id = resolved
    run = store.get(run_id)
    assert run is not None
    assert run.status.value == "completed"
    assert run.model == "deterministic:frozen catalog + Postgres proofs"
    contract = build_mission_control_run(run)
    assert len(contract.divergences) == 3
    assert contract.divergences[0].total_abs_divergence == frozen["weekly_revenue"]["total_abs_divergence"]
    assert contract.divergences[1].total_abs_divergence == frozen["weekly_order_volume"]["total_abs_divergence"]
    assert contract.divergences[2].total_abs_divergence == frozen["weekly_refund_amount"]["total_abs_divergence"]
