# MetricGuard — Progress Log

_Last updated: 2026-07-11. Deep session narratives: `docs/tags.md`._

## Status: core complete, verified live end-to-end

Everything below is **built and live-verified**, not planned:

- **Deterministic core** — signature extraction, comparison/severity, divergence
  math, clustering, guard drift. 51 passing tests, CI (ruff + pytest), Apache-2.0.
- **Graph-native discovery** — `discover --from-graph` rediscovered both seeded
  conflict families from *semantics, not names*, through the official DataHub
  MCP server (search → observed queries → extractor → clustering).
- **Agent** — composed graph investigation (search → SQL → owners/domains/tags →
  lineage) → deterministic proofs → validated canonical-resolution staging.
  Durable audit runs (`runs list/show`), grounded final answers (invented IDs /
  unsupported claims / wrong approval state are caught and replaced). Family
  identity comes from DataHub's governed `metric_family` property. Verified live
  with Gemini; reference audited run: `f3b6493a03`.
- **Warehouse proof** — Finance vs Executive weekly revenue: **15.06% mean /
  19.89% max divergence, first divergence 2022-12-26**, executed live.
- **Write-back is real** — approved proposals executed in GMS: canonical/divergent
  tags, decision document with numeric evidence, description redirects, and the
  canonical SemanticSignature as governed structured properties. All mutations
  pass through the single `DataHubClient.write()` approval gate.
- **DataHub-backed Guard** — `guard datahub-check` rehydrates the approved
  signature from governed properties. Live: canonical SQL exits 0; unfiltered
  Executive query exits 1 with `filters` as the break.
- **Simulated org** — `scripts/simulate_org.py`: 5 domains, 5 teams, 8 datasets
  (dbt/superset/postgres), 6 Query entities with the conflicting SQL, ownership +
  lineage. 54 aspects, idempotent.
- **Judge environment** — `make demo`: compose warehouse (:5433) + committed
  gzipped data (`data/fiction_retail/`) + DataHub quickstart + postgres ingestion
  recipe + org simulation + write-back bootstrap + graph-discovery smoke test.
  ⚠️ Built and reviewed but **not yet executed end-to-end** (needs a docker
  machine) — first item in the plan.
- **Artifacts** — live-verified `examples/`, `contrib/datahub-skills/` Skill
  draft (submission still a human decision), LICENSE, CI, README.

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

The path from top-5 to top-1 lives in `MetricGuard_3Week_Plan.md`.
