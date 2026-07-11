# MetricGuard — Progress Log

_Last updated: 2026-07-11_

## Session 2026-07-11 — graph-native agent + DataHub-backed Guard ✅

- Replaced the production agent's seed-first workflow with a DataHub Agent
  Context Kit workflow. With MCP enabled, seed loading and generic mutation
  staging are absent from its tool belt. The agent starts with a composed graph
  investigation (`search` → observed queries → entities/owners/domains/tags →
  downstream lineage), then uses deterministic signatures, clustering, and diffs.
- Added graph-URN divergence: the agent selects two DataHub dataset URNs and the
  tool re-fetches their SQL before warehouse execution. The LLM never relays SQL
  as trusted input. Live proof for Finance vs Executive weekly revenue: **15.06%
  mean divergence, 19.89% max, first divergence 2022-12-26**.
- Added validated canonical-resolution staging: one tool builds the complete
  decision document + signature properties + canonical/divergent tags + redirects.
  The decision document preserves the agent's numeric evidence in DataHub.
  It is approval-gated and semantically idempotent. Existing resolutions yield
  `no_new_proposals`; the agent cannot claim a write occurred.
- Added DataHub-backed Guard. `metricguard guard datahub-check` rehydrates the
  approved `SemanticSignature` from the canonical asset's governed structured
  properties. Verified live: Finance canonical SQL exits 0; the unfiltered
  Executive query exits 1 with `filters` identified as the semantic break.
- Full LLM agent verified live with Gemini: DataHub discovery, two selected
  warehouse proofs, graph follow-up, resolution action, and a concise final report.
- Added durable agent audit traces (`metricguard runs list/show`) and final-answer
  grounding checks. Empty provider responses, invented IDs, unsupported claims,
  and incorrect approval state are retried or replaced with deterministic evidence.
- Family identity now comes from DataHub's governed `metric_family` property, not
  graph search order. Mismatched family staging is refused deterministically.
- Added compact live-verified artifacts under `examples/` for judge inspection.
- Prepared and validated a cross-agent `datahub-semantic-conflicts` Skill draft
  under `contrib/datahub-skills/` for a potential upstream OSS contribution.
- Final completion-audit run: `f3b6493a03` — completed, non-empty grounded final,
  exactly 3 revenue candidates / 1 governed family / 3 conflicting pairs,
  warehouse proof 15.06% mean and 19.89% max, no duplicate proposals, all seven
  equivalent resolution writes executed, and no approval remaining.
- Added the required Apache-2.0 `LICENSE`, GitHub Actions CI, current README/agent
  guidance, clean lint, and expanded coverage to **51 passing tests**.

## Session 2026-07-05 — MCP live, discovery from graph (#3), real write-back (#4) ✅

> Full session narrative (box recovery, MCP reconciliation, bugs, live GMS
> verification, all commands): **`docs/tags.md`**.

- **Fixed the DataHub box** (had gone down): OpenSearch had exited on native-thread
  exhaustion (`pthread_create EAGAIN`, not RAM/disk) — restarted it; and managed
  ingestion was failing on an empty version pin (`acryl-datahub[postgres]==`) — the
  source's CLI Version field was blank, set it to `1.5.0.6`. Added
  `scripts/datahub_doctor.sh` (read-only health check + `--fix` to restart Exited
  containers). Latent: `vm.max_map_count=65530` (<262144) still un-fixed.
- **MCP activated + `_CAPABILITIES` locked down** to the server's *real* tool names
  (verified live). Deleted two fictional write capabilities: the server has **no
  create-glossary-term and no incident tool**. Real mutation tools: `add_tags`,
  `add_terms` (attach existing term only), `add_structured_properties`,
  `save_document`, `update_description`. Fixed a content-block unwrap bug in
  `mcp_client._call` (results arrive as `[{"type":"text","text":"<json>"}]`).
- **#3 DONE — discovery reads from the graph.** New `datahub/discovery.py`
  (`candidates_from_graph`) + `metricguard discover --from-graph`. Live run
  rediscovered BOTH families from *semantics, not names* (search → get_dataset_queries
  → unchanged extractor → clustering): weekly_revenue {weekly_revenue, revenue_tile,
  weekly_bookings}, wau {finance_wau, marketing_wau, wau_tile}, conflicts proven.
  `MetricDefinition` now carries `dataset_urn`/`query_urn` provenance for write-back.
  `StubDataHubClient` emits the same MCP envelope so the path is tested offline
  (`tests/test_graph_discovery.py`).
- **#4 DONE — write-back is real, verified live in GMS.** `resolve --canonical` →
  `proposals approve` → real mutations: canonical/divergent tags across all 3
  weekly_revenue datasets, description redirects, a decision document, and the
  canonical SemanticSignature written as structured properties on the winning
  dataset. Needed `scripts/bootstrap_writeback_entities.py --emit` first (tags +
  structured-property defs don't auto-create). Fixed a silent-write-failure bug and
  a per-call MCP-respawn perf issue along the way. **35 tests pass.**

### Write-back plan CHANGED (important for #4)
The original plan (create canonical glossary term + conflict incident) is **not doable
over MCP** — those tools don't exist. Redesigned payload, all confirmed-available:
`add_structured_properties` (canonical SemanticSignature onto the winning dataset —
*the money shot*), `add_tags` (`metricguard:canonical` / `:divergent`), `save_document`
(canonical def + numeric divergence proof), `update_description`. Glossary term is an
optional stretch (pre-create it, then `add_terms`). Incident: dropped from MCP scope.


## Where the project stands

MetricGuard's **deterministic core is done and solid** (signature extraction, comparison,
divergence math, guard/drift, agent loop). The gap identified this session was that the
project was **DataHub-native in name only** — discovery read from local `seeds/*.sql`, the
MCP path was scaffolded but never activated, and write-back only hit an in-memory stub.

The winning surface for the hackathon (per the [rules](https://datahub.devpost.com/rules):
"Use of DataHub" is the most-emphasized criterion, rewarding *contributing back to the graph*)
requires the org's metric logic to **live inside DataHub** so the agent discovers conflicts
*from the graph*. This session cleared that risk.

## Done this session ✅

### 1. Go/no-go audit (objective)
- Confirmed the deterministic engine is real and strong; agent loop is a genuine (if small)
  7-tool ReAct loop.
- Confirmed the weak axes: **no DataHub reads** (candidates came from disk), **MCP never
  activated** (`_CAPABILITIES` were unverified guesses), **write-back never mutated a real
  DataHub** (stub only).
- Verdict: idea is worth pursuing **iff** discovery starts from the graph and write-back is
  real. Those became the plan.

### 2. Org + lineage simulation ingested into DataHub ✅
Built `scripts/simulate_org.py` — turns the seed manifests into a real org in DataHub Core.
**54 metadata change proposals emitted and verified queryable.**

Now live in the graph (http://localhost:9002):
- **5 domains** (the simulated org): Business Intelligence, Finance, Marketing, Product, Sales Ops
- **8 metric datasets** across 3 platforms (dbt / superset / postgres), each team-owned:
  - `dbt:marts.finance.weekly_revenue` (finance-data)
  - `superset:executive_kpis.revenue_tile` (bi-team)
  - `superset:sales_ops.weekly_bookings` (sales-operations)
  - `dbt:marts.finance.finance_wau`, `dbt:marts.marketing.marketing_wau`, `superset:product_kpis.wau_tile`
  - + upstream `postgres.*` source tables (lineage-linked)
- **6 Query entities** carrying the actual conflicting SQL, attached to their datasets
- Ownership (corpGroups), domains, subtypes, upstream lineage

Two conflict families MetricGuard should **rediscover from the graph**:
- `weekly_revenue`: 3 competing defs — bi-team, finance-data, sales-operations
- `weekly_active_users`: 3 competing defs — marketing-analytics, product-analytics, finance-data

Deliberately **no shared glossary term** yet — the canonical term / canonical-vs-divergent
tags / conflict incident are reserved as the **write-back payload**, preserving the
"discover conflicts you didn't know about" story.

### 3. DataHub connectivity + token auth ✅ (was the blocker)
- Installed `acryl-datahub` SDK into the venv.
- Diagnosed the wall: frontend proxy at `localhost:9002/api/gms` returned `401` because the
  server ran `METADATA_SERVICE_AUTH_ENABLED=false` (PAT not validated; only session login worked).
- **Enabled token auth on the server** (docker quickstart on the remote EC2 host):
  - Added `METADATA_SERVICE_AUTH_ENABLED=true` to the `environment:` of both
    `datahub-gms-quickstart` and `frontend-quickstart` in
    `~/.datahub/quickstart/docker-compose.yml`.
  - Recreated both containers **preserving** the existing `DATAHUB_TOKEN_SERVICE_SIGNING_KEY`
    and `DATAHUB_TOKEN_SERVICE_SALT` (read off the running container) so the existing PAT
    stayed valid. Passed `DATAHUB_VERSION=v1.5.0.6` explicitly (raw compose doesn't inject it).
  - Verified: `GET /api/gms/config` with the Bearer token → `200`.
- The PAT in `.env` (`DATAHUB_TOKEN`, minted 2026-07-04, expires ~2026-10) now authenticates.
  The MCP server (`uvx mcp-server-datahub`) will authenticate the same way.

## Environment / facts to remember

- **DataHub**: remote EC2 (`ip-172-31-26-236`), docker quickstart v1.5.0.6, tunneled to
  `localhost:9002`. GMS reached via frontend proxy `localhost:9002/api/gms`.
  Login `datahub`/`datahub`. Token auth now ON.
- **Warehouse**: fiction-retail data in `metric` schema on RDS (`POSTGRES_DSN` in `.env`).
- **MCP**: `DATAHUB_MCP_TRANSPORT` still empty (stub). Command `uvx mcp-server-datahub`.
  Not yet activated — next step.
- **Reproduce the org ingestion**: `python scripts/simulate_org.py --emit`
  (idempotent; `--dry-run` validates offline).

## Next steps (open)

- [x] **#3 Flip discovery to the graph** — DONE (see 2026-07-05 session above).
- [x] **#4 Make write-back real** — DONE + PROVEN LIVE END-TO-END. Full round trip:
  graph discovery → `resolve --canonical <name>` stages proposals → `proposals approve`
  (human gate) → real MCP mutation. `datahub/writeback.py` builds Proposals with payloads
  matching the real tools (`save_document`/`add_tags`/`update_description`/
  `add_structured_properties`); `build_canonical_writeback` order = document,
  structured_property, canonical tag, then per-divergent (tag, description redirect).
  EXECUTED + VERIFIED IN GMS (`entitiesV2`): canonical/divergent tags on all 3 weekly_revenue
  datasets, description redirects on the 2 divergent, decision document, AND the money shot —
  the canonical SemanticSignature written onto finance `weekly_revenue` as structured
  properties (aggregation=SUM(total_amount), entity=total_amount, grain=week, filters,
  source_population).
  - KEY CONSTRAINT: tags/terms/structured-props do NOT auto-create — `add_tags` fails
    "Urn does not exist" unless the entity exists first, and there is NO MCP create tool.
    `scripts/bootstrap_writeback_entities.py --emit` (SDK) creates the 2 tags + 8 signature
    structured-property definitions once; run it before approving tag/structured proposals.
  - BUG FIXED (silent write failure): MCP reports failures as an "Error calling tool ..."
    STRING, not an exception — `_execute_write` was marking failed writes executed. `_call`
    now raises; `proposals approve` catches it and leaves the proposal pending.
  - BUG FIXED (`_is_mutation`): `save`/`remove` were missing markers, so save_document/
    remove_* would leak to the agent, bypassing the proposal gate. Now filtered.
  - PERF FIX: `mcp_client._gather_graph_reads` does all discovery reads over ONE MCP session
    (`bulk_discovery`); before, each tool call respawned the stdio server (~10s) so discovery
    looked like a hang. GOTCHA: killed CLI runs leave orphaned `uvx mcp-server-datahub` procs
    that stall new connections — `pkill -f mcp-server-datahub`.
  - Full narrative + commands + GMS verification: **`docs/tags.md`**.
- [x] Wire the agent loop to graph-native investigation; production MCP mode no
  longer exposes the seed loader.
- [x] Verify divergence on graph-sourced candidates against the live warehouse.
- [ ] Fix `vm.max_map_count` on the box so OpenSearch stops dying.

## Eligibility note

Rules require projects be **newly created during the submission window (Jul 6 – Aug 10, 2026)**.
This repo is scaffolding — the real submission will be registered later in a fresh repo.
