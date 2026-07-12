# Golden Mission Control replay

This directory holds exactly one completed deterministic `AgentRun` JSON. It
is intentionally committed rather than stored in `.metricguard/runs/`, so a
fresh clone can replay the flagship evidence audit without DataHub, Postgres,
or an LLM:

```bash
uv run metricguard ui --replay golden
```

The run must be regenerated only from the frozen warehouse proof snapshot in
[`../warehouse_proofs.json`](../warehouse_proofs.json), using
`uv run python scripts/record_golden_replay.py`. Keep its raw AgentRun JSON
here; `metricguard ui` projects it through the same runtime contract used for
live investigations.
