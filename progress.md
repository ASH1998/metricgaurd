# MetricGuard — Progress Log

_Last updated: 2026-07-12. Deep session narratives: `docs/tags.md`._

## Status: core complete; operational UI and sentinel vertical slices built

Everything below is **built**; each item states whether it was verified against
live infrastructure or locally:

- **Deterministic core** — signature extraction, comparison/severity, divergence
  math, clustering, guard drift. Full suite passing, CI (ruff + pytest), Apache-2.0.
- **Graph-native discovery** — `discover --from-graph` rediscovers four seeded
  conflict families from *semantics, not names*, through the official DataHub
  MCP server (search → observed queries → extractor → clustering).
- **Agent** — composed graph investigation (search → SQL → owners/domains/tags →
  lineage) → deterministic proofs → validated canonical-resolution staging.
  Durable audit runs (`runs list/show`), grounded final answers (invented IDs /
  unsupported claims / wrong approval state are caught and replaced). Grounding
  failures are now durable `grounding_check_intervention` trace events, including
  the exact reason a rewrite was demanded. Family identity comes from DataHub's
  governed `metric_family` property. Verified live with Gemini; reference audited
  run: `f3b6493a03`.
- **Sentinel foundation** — `metricguard sentinel` maintains a durable DataHub
  query-definition fingerprint. First scan baselines safely; unchanged definitions
  are skipped; cosmetic SQL edits become `dismissed_with_evidence` without an LLM
  call; new or semantic changes open an autonomous run through the existing agent.
  Trigger provenance and one of the three terminal outcomes are persisted and
  rendered in the Mission Control timeline. Locally verified with deterministic
  stubs; live two-scan DataHub verification remains.
- **MetricGuard UI** — `metricguard ui` is now an operational local application,
  not a demo-only replay page. Users can list and switch durable investigations,
  start a real agent investigation from the browser, follow running work through
  SSE, inspect the agent/evidence trail, and view executed divergence as an inline
  SVG chart. The browser calls the existing agent/run-store path; it does not call
  an LLM directly or bypass the DataHub approval choke point.
- **Decision-first UX** — Sentinel runs now have meaningful asset-based titles and
  origin/outcome badges; staged resolutions, refusals, dismissals, and failures
  render as a prominent next-action banner. Raw tool arguments are collapsed behind
  evidence disclosures, exact warehouse proof failures replace generic empty states,
  and investigations with multiple successful divergence proofs get chart tabs.
- **Replay + integration API** — frozen JSON contract v1.0, `GET /api/runs`,
  `GET /api/runs/<id>`, `POST /api/investigations`, and SSE
  `/api/stream/<id>`. `metricguard ui --replay <run-id>` keeps client-timed replay;
  `--export <run-id> -o site/` emits a zero-backend snapshot using the same page
  and contract.
- **UI verification** — modern responsive application shell, prior-run navigation,
  new-investigation dialog, explicit empty/offline/no-warehouse states, and live
  rendering of the real 71-point weekly-revenue proof. Visually checked at 1280px
  and 390px with no horizontal overflow. Opening `index.html` through `file://`
  intentionally shows startup guidance because artifacts require the local server.
- **Live-flow hardening** — run saves use fsync + atomic replacement; readers skip
  transient or invalid JSON; SSE retains browser auto-reconnect; replay mode rejects
  investigations; the mutation endpoint requires `application/json`; local and graph
  divergence tools now expose the same full-points chart contract. Chart formatting
  distinguishes currency metrics from counts and handles identical/zero series.
- **Warehouse proof** — Finance vs Executive weekly revenue: **15.06% mean /
  19.89% max divergence, first divergence 2022-12-26**, executed live.
- **Expanded conflict estate** — 12 competing definitions across four families.
  Two additional Postgres-backed proofs are live-verified: Fulfillment vs
  Executive order volume (**30.05% mean / 34.40% max**) and Finance vs Customer
  Success refunds (**3.11% mean / 15.14% max**). WAU remains signature-only so
  refusal is exercised rather than every scenario conveniently producing data.
  Live DataHub MCP readback returned all 12 candidates as exactly four families.
  Sentinel run `ce0f39f0c3` autonomously caught the newly emitted order/refund
  definitions, executed two proofs, staged the refund resolution, and escalated
  the order-volume naming split for human judgment.
- **Write-back is real** — approved proposals executed in GMS: canonical/divergent
  tags, decision document with numeric evidence, description redirects, and the
  canonical SemanticSignature as governed structured properties. All mutations
  pass through the single `DataHubClient.write()` approval gate.
- **The gate re-proves before it writes (2026-07-12)** — resolution proposals now
  carry a deterministic evidence snapshot (canonical query urn + staging-time
  SemanticSignature); `proposals approve` re-reads the current SQL from DataHub,
  re-extracts the signature, and blocks with `StaleEvidenceError` if the
  definition semantically changed (or vanished) since staging. Cosmetic edits
  still pass — signature equality is the check, not text equality.
  `--skip-verification` is the explicit human override; legacy proposals without
  a snapshot approve with an "unverified" note. Locally verified with stub
  clients (6 new tests).
- **Impact numbers + judge-proofing (2026-07-12)** —
  `DivergenceReport.total_abs_divergence`: the cumulative gap, live-verified —
  **Finance vs Executive weekly revenue disagree by $28.9M across 71 weeks**.
  Surfaced in the CLI headline, agent tool JSON, UI contract, and a fifth
  Mission Control stat card (currency-aware, with a client-side fallback sum for
  runs recorded before the field existed; browser-verified on both a count
  metric, 34.7K, and a currency metric, $28.9M). `divergence --segment-col`
  exposes the existing gap-concentration math in the CLI. `metricguard doctor`
  checks warehouse, DataHub GMS, MCP handshake, LLM key, and local stores, and
  prints the exact fix per failure — all five checks verified green against the
  live environment.
- **DataHub-backed Guard** — `guard datahub-check` rehydrates the approved
  signature from governed properties. Live: canonical SQL exits 0; unfiltered
  Executive query exits 1 with `filters` as the break.
- **Simulated org** — `scripts/simulate_org.py`: 8 domains, 8 teams, 14 derived/source
  datasets (dbt/superset/postgres), 12 Query entities with conflicting SQL, ownership +
  lineage. 54 aspects, idempotent.
- **Judge environment** — `make demo`: compose warehouse (:5433) + committed
  gzipped data (`data/fiction_retail/`) + DataHub quickstart + postgres ingestion
  recipe + org simulation + write-back bootstrap + graph-discovery smoke test.
  ⚠️ Built and reviewed but **not yet executed end-to-end** (needs a docker
  machine) — first item in the plan.
- **Artifacts** — live-verified `examples/`, `contrib/datahub-skills/` Skill
  draft (submission still a human decision), LICENSE, CI, README. Starlette and
  Uvicorn are core dependencies; the packaged wheel includes the single-file UI.

## Environment facts

- **DataHub**: remote EC2 docker quickstart **v1.5.0.6**, tunneled to
  `localhost:9002`, GMS via `/api/gms`, token auth ON (PAT in `.env`, expires
  ~2026-10). Latent risk: `vm.max_map_count=65530 < 262144` — OpenSearch can die
  under load (`scripts/datahub_doctor.sh --fix` restarts it).
- **Warehouse**: fiction-retail (~695k rows, 10 tables) in the `metric` schema on
  RDS; local equivalent via `docker-compose.demo.yml`.
- **SDK skew**: local `acryl-datahub` is 1.6.0.13, server is 1.5.0.6. Dry-runs
  pass; verify the first live `--emit`, or pin `<1.6` in the `demo` extra.
- **MCP mutation tools that actually exist**: `add_tags`, `add_terms`,
  `add_structured_properties`, `save_document`, `update_description`. There is
  **no create-glossary-term or incident tool** — tag/structured-property
  *definitions* must be pre-created (`scripts/bootstrap_writeback_entities.py`).
- **Gotchas**: MCP reports write failures as an `"Error calling tool ..."` STRING
  (now raised, not swallowed); killed CLI runs leave orphaned
  `uvx mcp-server-datahub` processes (`pkill -f mcp-server-datahub`).

## Eligibility — resolved

Organizers confirmed the Jul 6 – Aug 10, 2026 window governs **submission**, not
when work started. This repo is the submission repo.

## What remains

The current UI is the operational foundation, not the finished feature. Next:

- ship a committed golden run so replay works from a fresh clone;
- add the organization conflict map with negative controls visibly excluded;
- add proposal review/status and human approval hand-off in the UI while keeping
  every mutation behind `DataHubClient.write()`;
- execute `make demo` end-to-end on a clean Docker machine;
- finish the refusal scenario, decision-legibility traces, and Guard PR workflow.
- live-verify sentinel against an ingested rogue Query entity, then decompose the
  composed investigation into narrower, change-scoped agent decisions.
- the week-2 big swings (see plan): semantic ablation (per-dimension gap
  attribution), blast-radius quantification from lineage, guard break → SQL
  construct localization.
- ⚠️ reconcile recorded divergence numbers before the video: docs/examples say
  weekly_revenue is 15.06% mean / 19.89% max, but the live warehouse today gives
  **13.07% / 16.59%** (same first-divergence date) — the data moved after the
  numbers were recorded. Freeze the warehouse, then refresh `examples/`,
  README, and this file together.

The ordered plan lives in `MetricGuard_3Week_Plan.md`.
