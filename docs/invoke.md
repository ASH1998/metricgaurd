# Running MetricGuard

The entry point is installed in the venv, so you have two equivalent ways to
run — pick one:

```bash
cd /Users/ashutosh/Developer/scaffolding

# Option A — activate once, then use the short command:
source .venv/bin/activate
metricguard --help

# Option B — no activation, explicit each time:
.venv/bin/python -m metricguard.cli --help
```

**All output is Rich-formatted tables and panels printed straight to your
terminal** — that *is* the output; there are no files to open (except staged
proposals, see step 7). The examples below use Option A (`metricguard ...`).

---

## Run it in the order of the story

### 1. Inspect one definition's semantic signature
```bash
metricguard signature seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql
```
→ a panel showing the extracted `{aggregation, entity, grain, filters,
source_population, …}`.

### 2. Prove two definitions disagree
_(no DB, no API key needed)_
```bash
metricguard compare \
  seeds/metric_families/weekly_revenue/exec_dashboard_weekly_revenue.sql \
  seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql
```
→ a conflict table with fields, both sides, severity, and "why it matters."
Exit code `1` when they conflict.

### 3. Discover conflicts across all candidates
```bash
metricguard discover
```
→ clusters the seeded definitions into families with confidence + evidence,
then prints pairwise conflict tables. Add `--explain` for an LLM explanation
and ranked canonical proposals (needs `LLM_MODEL` + a provider API key).

### 4. The money shot — executed divergence against the live warehouse
```bash
metricguard divergence \
  seeds/metric_families/weekly_revenue/exec_dashboard_weekly_revenue.sql \
  seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql \
  --value-col weekly_revenue
```
→ headline panel (`mean 13.07% · max 16.59% · diverging since 2022-12-26`) plus
a table of the largest weekly gaps in dollars. _Takes ~20s — it's querying
Postgres._ Needs `POSTGRES_DSN`.

### 5. Guard mode — the CI gate
```bash
metricguard guard approve weekly_revenue \
  seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql --approved-by you

# then check any changed query:
metricguard guard check weekly_revenue some_changed_query.sql ; echo "exit: $?"
```
→ `✔` green (exit 0) for cosmetic changes, `✘` red drift table (exit 1) for
semantic breaks. The exit code is contractual — a CI pipeline reads it
(`0` ok · `1` drift · `2` no contract).

### 6. The agent — full autonomous investigation
_(uses the configured LLM, ~60–90s)_
```bash
metricguard agent "Investigate conflicting definitions of weekly revenue, prove the divergence against the warehouse (key_col=week_start, value_col=weekly_revenue), and stage write-back proposals."
```
→ each tool call streams by with a `→` prefix as it works, then a final report
panel. It ends by staging write-back proposals (it never writes to DataHub
directly).

### 7. See what the agent staged, and (when ready) execute
```bash
metricguard proposals list              # table of pending write-backs
metricguard proposals show <id>         # full payload + rationale for one
metricguard proposals approve <id>      # asks y/n, then writes to DataHub
metricguard proposals reject <id>       # dismiss (kept for the audit trail)
```

---

## Two things about "seeing output"

- **The terminal is the UI for everything except write-back.** If a table
  looks cramped, widen your terminal window — Rich wraps to fit.
- **The DataHub UI (`http://localhost:9002`, login `datahub`/`datahub`) is where
  write-back becomes visible** — after you `proposals approve` an item, the
  glossary term / tags / incident appear there. That step needs the MCP path
  live (the `METADATA_SERVICE_AUTH_ENABLED=true` server flag). Until then
  `proposals approve` runs against the in-memory stub, so it confirms success
  but won't show in the UI yet.

To watch a run without configuring anything extra, start with steps
**2 → 4 → 6** — that's the discover-prove-agent arc and needs nothing beyond
what's already set up.

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
| `proposals list/show/approve/reject` | approve needs DataHub | staged write-backs |
| `datahub tools` | `DATAHUB_MCP_TRANSPORT` | MCP server's tool names |
