# Running MetricGuard

Run commands from the repository root through `uv`:

```bash
cd /Users/ashutosh/Developer/metricgaurd
uv run metricguard --help
```

CLI output uses Rich-formatted tables and panels. `metricguard ui` provides the
operational workspace for live investigations and recorded replays; DataHub's
UI remains the governed system of record after approved write-back.

---

## Run it in the order of the story

### 1. Inspect one definition's semantic signature
```bash
uv run metricguard signature seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql
```
→ a panel showing the extracted `{aggregation, entity, grain, filters,
source_population, …}`.

### 2. Prove two definitions disagree
_(no DB, no API key needed)_
```bash
uv run metricguard compare \
  seeds/metric_families/weekly_revenue/exec_dashboard_weekly_revenue.sql \
  seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql
```
→ a conflict table with fields, both sides, severity, and "why it matters."
Exit code `1` when they conflict.

### 3. Discover conflicts across all candidates
```bash
uv run metricguard discover
```
→ clusters the seeded definitions into families with confidence + evidence,
then prints pairwise conflict tables. Add `--explain` for an LLM explanation
and ranked canonical proposals (needs `LLM_MODEL` + a provider API key).

### 4. The money shot — executed divergence against the live warehouse
```bash
uv run metricguard divergence \
  seeds/metric_families/weekly_revenue/exec_dashboard_weekly_revenue.sql \
  seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql \
  --value-col weekly_revenue
```
→ headline panel (`mean 15.06% · max 19.89% · diverging since 2022-12-26`) plus
a table of the largest weekly gaps in dollars. _Takes ~20s — it's querying
Postgres._ Needs `POSTGRES_DSN`.

### 5. Guard mode — the CI gate
```bash
uv run metricguard guard approve weekly_revenue \
  seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql --approved-by you

# then check any changed query:
uv run metricguard guard check weekly_revenue some_changed_query.sql
```
→ `✔` green (exit 0) for cosmetic changes, `✘` red drift table (exit 1) for
semantic breaks. The exit code is contractual — a CI pipeline reads it
(`0` ok · `1` drift · `2` no contract).

### 6. The agent — goal-directed investigation
_(uses the configured LLM, ~60–90s)_
```bash
uv run metricguard agent "Investigate conflicting definitions of weekly revenue, prove the divergence against the warehouse (key_col=week_start, value_col=weekly_revenue), and stage write-back proposals."
```
→ each tool call streams by with a `→` prefix as it works, then a final report
panel. It ends by staging write-back proposals (it never writes to DataHub
directly).

### 7. See what the agent staged, and (when ready) execute
```bash
uv run metricguard proposals list              # table of pending write-backs
uv run metricguard proposals show <id>         # full payload + rationale for one
uv run metricguard proposals approve <id>      # asks y/n, then writes to DataHub
uv run metricguard proposals reject <id>       # dismiss (kept for the audit trail)
```

### 8. Mission Control — inspect live or replayed investigations
```bash
uv run metricguard ui
uv run metricguard ui --replay <run-id>
```

### 9. Sentinel — let a DataHub change start the investigation
```bash
# First pass records a baseline without alerting on the existing catalog.
uv run metricguard sentinel --once

# Standing mode; ingest a new/changed Query entity in another terminal.
uv run metricguard sentinel --interval 30
```
→ unchanged definitions are skipped, cosmetic SQL edits are dismissed with
signature evidence, and new or semantic changes open autonomous runs. Every
such run records `staged_resolution`, `needs_human_decision`, or
`dismissed_with_evidence`.

---

## Two things about "seeing output"

- **The CLI and Mission Control are two views over the same run store.** The
  browser never calls an LLM or bypasses the approval gate.
- **The DataHub UI (`http://localhost:9002`, login `datahub`/`datahub`) is where
  write-back becomes visible** — after you `proposals approve` an item, the
  tags, structured properties, decision documents, description redirects, and
  attached pre-existing glossary terms appear there. That step needs the MCP
  path live; without `DATAHUB_MCP_TRANSPORT`, approval uses the in-memory stub
  and cannot produce visible graph changes.

For a zero-LLM first check, start with steps **1 → 2 → 3**. The executed proof
also needs `POSTGRES_DSN`; agent and sentinel investigations additionally need
DataHub MCP plus the configured model provider.

---

## Quick reference

| Command | Needs | What you see |
|---|---|---|
| `signature <sql>` | nothing | one signature panel |
| `compare <a> <b>` | nothing | conflict table (exit 1 if conflict) |
| `discover` | nothing (`--explain` needs LLM) | clusters + pairwise conflicts |
| `divergence <a> <b> --value-col X` | `POSTGRES_DSN` | dollar-gap table over time |
| `guard approve <metric> <sql>` | nothing | saves the contract |
| `guard check <metric> <sql>` | a saved contract | drift verdict (exit 0/1/2) |
| `agent "<goal>"` | `LLM_MODEL` + API key | streamed investigation + report |
| `sentinel [--once]` | DataHub MCP + agent credentials for material changes | autonomous run or evidence-backed dismissal |
| `ui [--replay <id>]` | nothing for replay; agent credentials for live starts | Mission Control timeline + divergence proof |
| `proposals list/show/approve/reject` | approve needs DataHub | staged write-backs |
| `datahub tools` | `DATAHUB_MCP_TRANSPORT` | MCP server's tool names |
