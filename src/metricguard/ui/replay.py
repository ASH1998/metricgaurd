"""Resolve portable Mission Control replays without local runtime state."""

from __future__ import annotations

from pathlib import Path

from metricguard.agent.runs import AgentRunStore


GOLDEN_REPLAY_ALIAS = "golden"
GOLDEN_REPLAY_DIRECTORY = Path("examples/golden_run")


def resolve_replay_run(
    run_id: str,
    *,
    local_store: AgentRunStore,
    golden_directory: Path = GOLDEN_REPLAY_DIRECTORY,
) -> tuple[AgentRunStore, str] | None:
    """Return the store and concrete ID for a local or shipped replay.

    ``--replay golden`` is intentionally an alias rather than a historical run
    ID so a fresh clone can replay the committed investigation. Explicit IDs
    still support existing local runs, and then the shipped run by its concrete
    ID for deep links.
    """
    if local_store.get(run_id) is not None:
        return local_store, run_id
    if not golden_directory.is_dir():
        return None

    golden_store = AgentRunStore(directory=golden_directory)
    if run_id != GOLDEN_REPLAY_ALIAS:
        return (golden_store, run_id) if golden_store.get(run_id) is not None else None

    golden_runs = golden_store.list()
    if len(golden_runs) != 1:
        return None
    return golden_store, golden_runs[0].id
