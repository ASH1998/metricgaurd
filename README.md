# MetricGuard

> Companies can't agree on their own numbers. MetricGuard finds where metric
> definitions secretly disagree, proves it with data, gets a human to bless one
> version, and then blocks anyone from quietly breaking it.

**Semantic Conflict Intelligence for DataHub.** Finds where the *same* business
metric is computed with *conflicting* logic across an organization, proves how
much the definitions disagree, helps a human pick one canonical version, writes
that truth back into DataHub — and then stands guard against future drift.

Built for **Build with DataHub: The Agent Hackathon**. See
[the product pitch](docs/pitch.md) and [project context](context.md).

## Design principle

**Deterministic core, LLM for judgment.**

| Deterministic code | LLM (via LangChain — any provider) |
|---|---|
| SQL parsing (sqlglot), signature extraction | clustering judgment on ambiguous cases |
| signature comparison, conflict classification | plain-language conflict explanations |
| query execution, divergence math | ranked canonical proposals w/ tradeoffs |
| drift detection (guard mode) | |

The LLM never computes the verifiable math.

## One-command demo environment

Everything a reviewer needs — a Postgres warehouse with ~695k rows of
fiction-retail data, a local DataHub, and a simulated org whose teams compute
the same metrics with conflicting SQL — from a fresh clone:

```bash
make demo        # needs docker + uv (and ~8GB free RAM for DataHub quickstart)
```

The script is idempotent and prints what to try next. It ends with a smoke
test that rediscovers four conflict families from the DataHub graph. Three
families execute against Postgres (`weekly_revenue`, `weekly_order_volume`, and
`weekly_refund_amount`); WAU remains the deliberate signature-only/refusal case. The LLM
agent additionally needs `LLM_MODEL` + a provider API key in `.env`; every
deterministic path (discover, compare, divergence, guard, proposals) works
without one. Teardown: `make demo-down` (warehouse) and
`uv run datahub docker nuke` (DataHub).

## Setup (uv)

```bash
uv sync
uv sync --extra warehouse    # Postgres execution for numeric divergence
uv sync --extra demo         # DataHub SDK/CLI for the demo environment scripts
cp .env.example .env         # then fill in what you have
```

`LLM_MODEL` is a LangChain provider-prefixed string — swap providers freely:
`anthropic:claude-opus-4-8`, `openai:gpt-4o`, ... (install the matching
`langchain-*` extra for non-Anthropic providers: `uv sync --extra openai`).

## Try it

```bash
# extract a semantic signature from one definition
uv run metricguard signature seeds/metric_families/weekly_active_users/marketing_wau.sql

# prove exactly how two definitions disagree
uv run metricguard compare \
  seeds/metric_families/weekly_active_users/marketing_wau.sql \
  seeds/metric_families/weekly_active_users/product_wau.sql

# offline deterministic discovery over the seeded families
uv run metricguard discover

# executed proof of disagreement (needs POSTGRES_DSN — the fiction-retail warehouse)
uv run metricguard divergence \
  seeds/metric_families/weekly_revenue/exec_dashboard_weekly_revenue.sql \
  seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql \
  --value-col weekly_revenue

# two additional independent warehouse-backed disagreements
uv run metricguard divergence \
  seeds/metric_families/weekly_order_volume/fulfillment_order_volume.sql \
  seeds/metric_families/weekly_order_volume/exec_checkout_count.sql \
  --value-col weekly_orders
uv run metricguard divergence \
  seeds/metric_families/weekly_refund_amount/finance_refund_liability.sql \
  seeds/metric_families/weekly_refund_amount/support_customer_refunds.sql \
  --value-col weekly_refunds

# local guard contract (CI-friendly fallback)
uv run metricguard guard approve weekly_active_users \
  seeds/metric_families/weekly_active_users/product_wau.sql --approved-by you
uv run metricguard guard check weekly_active_users path/to/changed_query.sql
#   exit 0 = ok · 1 = semantic break · 2 = no contract  → CI-friendly

# graph-native agent: DataHub discovery → warehouse proof → staged resolution
uv run metricguard agent \
  "Investigate weekly revenue from DataHub, quantify divergence with key_col=week_start \
  and value_col=weekly_revenue, recommend a canonical, and stage the resolution."

# standing agent: baseline once, then watch DataHub for new/changed SQL
uv run metricguard sentinel --once
uv run metricguard sentinel --interval 30

# inspect the durable goal/tool/result/action/final-answer audit trail
uv run metricguard runs list
uv run metricguard runs show <run-id>

# graph-native guard: DataHub's governed signature is the contract
uv run metricguard guard datahub-check \
  'urn:li:dataset:(urn:li:dataPlatform:dbt,marts.finance.weekly_revenue,PROD)' \
  path/to/changed_query.sql

# tests — the signature engine must be trustworthy
uv run pytest
```

## Architecture

```
src/metricguard/
├── parsing/       sqlglot normalization (aliases, formatting, CTEs)
├── signature/     semantic signature extraction  ← the linchpin
├── comparison/    field-by-field diff + conflict classification
├── execution/     WarehouseExecutor ABC + live Postgres + StaticExecutor for tests
├── divergence/    executed-proof math: %, first-divergence-date, segment localization
├── clustering/    signal-based candidate grouping + confidence + evidence
├── llm/           LangChain judgment layer (provider-agnostic, schema-enforced output)
├── datahub/       Agent Context Kit MCP reads, graph investigation, gated write-back
├── guard/         local and DataHub-backed canonical contracts + drift detection
├── agent/         graph-native tools + provider-agnostic decision loop
├── sentinel.py    durable graph fingerprint + autonomous investigation trigger
└── cli.py         discover / compare / guard / agent / sentinel / ui
```

## Live integrations

- **Warehouse** — set `POSTGRES_DSN` + `uv sync --extra warehouse` for numeric divergence.
- **DataHub MCP** — set `DATAHUB_MCP_TRANSPORT=stdio` (runs `uvx mcp-server-datahub`
  with your `DATAHUB_GMS_URL`/`DATAHUB_TOKEN`) or `http` + `DATAHUB_MCP_URL`.
  With MCP on, local seed loading is removed from the agent's tool belt. The
  agent searches DataHub, reads observed SQL/ownership/domains/lineage, computes
  conflicts, executes selected definitions, and stages valid DataHub mutations.

This is built with DataHub's **Agent Context Kit** and official MCP server. It
does not depend on DataHub Cloud's private-beta hosted Agents product, so it runs
against self-hosted DataHub Core.

**Human-in-the-loop — the agent does real work, but never writes alone:**

```
agent investigates ──► stages proposals ──► human reviews ──► write-back executes
(MCP reads, signatures,   .metricguard/       metricguard        visible in the
 divergence, drift)       proposals/*.json    proposals approve   DataHub UI
```

Every approved mutation funnels through one gated `DataHubClient.write()` entrypoint
that raises unless explicitly approved. The agent's tool belt contains no
direct mutation tools — its only write power is staging proposals; mutation-
shaped MCP tools are filtered out before binding.

Every agent run is persisted under `.metricguard/runs/`. See the compact,
live-verified artifacts in [`examples/`](examples/).

Mission Control leads with the decision state and human next action, distinguishes
Sentinel-triggered work from human investigations, collapses technical tool payloads
until requested, and lets reviewers switch between multiple executed divergence
proofs from the same investigation.

## Standing-agent mode

`metricguard sentinel` observes query definitions through DataHub and stores its
cursor at `.metricguard/sentinel/state.json`. The first scan creates a baseline
unless `--investigate-existing` is supplied. Later scans:

- skip unchanged definitions;
- create an evidence-backed dismissal run for SQL edits whose semantic signature
  did not change;
- open an autonomous agent investigation for new definitions or semantic changes.

Every sentinel run records its exact trigger and ends as
`staged_resolution`, `needs_human_decision`, or `dismissed_with_evidence`.
Polling is the self-hosted demo transport; the decision and approval paths are
the same ones used by human-started investigations.

## License

[Apache-2.0](LICENSE).
