# Verified example outputs

These compact artifacts come from the live self-hosted DataHub + Postgres run.
Reviewers can inspect them without an LLM key or running infrastructure.

- [`weekly_revenue_evidence.json`](weekly_revenue_evidence.json) — graph-native
  discovery, lineage, deterministic conflicts, warehouse divergence, and the
  completed resolution state.
- [`warehouse_proofs.json`](warehouse_proofs.json) — the frozen, live-executed
  results for all three Postgres proof pairs, tied to the committed
  `data/fiction_retail` fixture hash. This is the single numerical source for
  the demo recording and public documentation.
- [`datahub_guard_drift.json`](datahub_guard_drift.json) — a changed Executive
  query rejected against the canonical signature stored in DataHub.

Reproduce the audited agent run:

```bash
uv run metricguard agent \
  "Investigate weekly revenue from DataHub, quantify Finance versus the Executive \
  tile with key_col=week_start and value_col=weekly_revenue, recommend only from \
  graph and warehouse facts, and invoke the resolution tool."

uv run metricguard runs list
uv run metricguard runs show <run-id>
```

Reproduce the graph-backed guard verdict:

```bash
uv run metricguard guard datahub-check \
  'urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)' \
  seeds/metric_families/weekly_revenue/exec_dashboard_weekly_revenue.sql
# exit 1: semantic drift
```
