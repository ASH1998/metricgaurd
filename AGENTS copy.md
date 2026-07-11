# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

MetricGuard — Semantic Conflict Intelligence for DataHub, built for the "Build with DataHub" hackathon (submission closes Aug 10, 2026). It discovers where an org computes the *same* business metric with *conflicting* SQL logic, proves the disagreement, helps a human pick a canonical definition, writes it back to DataHub, and guards against future drift. Read `context.md` (framing, scope guardrails) and `MetricGuard_3Week_Plan.md` (schedule, milestones) before making design decisions.

## Commands

```bash
uv sync                          # install core dependencies
uv sync --extra warehouse        # adds psycopg for live divergence execution
uv run pytest                    # full test suite
uv run pytest tests/test_signature_extraction.py -k cosmetic   # single test
uv run ruff check src tests      # lint
uv run metricguard --help        # CLI entry point (also: signature, compare, discover, guard, agent)
```

CLI commands that work with zero config: `signature`, `compare`, `discover`, `guard approve/check`. Commands needing config: `discover --from-graph`, `resolve`, `guard datahub-check`, and `agent` (DataHub MCP; agent also needs LLM_MODEL + provider API key); divergence execution needs POSTGRES_DSN. Guard exit codes are contractual: 0 ok, 1 drift, 2 no contract — CI depends on them.

## Architecture — the rules that matter

**1. Deterministic core, LLM for judgment — never blur this line.** Parsing (`parsing/`), signature extraction (`signature/`), comparison (`comparison/`), divergence math (`divergence/`), drift detection (`guard/`), and clustering signals (`clustering/`) are pure deterministic code with no LLM calls. The LLM layer (`llm/`, LangChain) only *consumes* their outputs to produce explanations, canonical proposals, and clustering judgment. Never move verifiable math into a prompt; never let LLM output feed back into the deterministic engines as fact.

**2. `SemanticSignature` (models.py) is the linchpin.** Both Discovery (conflict comparison) and Guard (drift detection) consume it. The schema is `{aggregation, entity, grain, timezone, filters, deduplication, null_handling, source_population}`. Changing its fields ripples into `comparison/diff.py` (severity map `_FIELD_SEVERITY`), `guard/contracts.py` (stored contract JSONs become stale), and the tests. The extractor (`signature/extractor.py`) only needs to be correct on the seeded metric families in `seeds/` — do not generalize it to arbitrary SQL at the cost of seed correctness.

**3. External connections live behind ABCs — code against the interface.**
- Warehouse: `execution/base.py` (`WarehouseExecutor`). `get_executor()` raises `NotConfiguredError` until `POSTGRES_DSN` is set; callers must catch it and degrade (signature comparison works without execution; only divergence proof needs the DB). `PostgresExecutor` is live and `StaticExecutor` provides canned rows for tests.
- DataHub: `datahub/base.py` (`DataHubClient`). `MCPDataHubClient` uses the official DataHub Agent Context Kit MCP server for graph reads and approved writes; `StubDataHubClient` mirrors it in tests. Write-back uses verified stock tools: tags, structured properties, documents, descriptions, and attachment of pre-existing glossary terms. The MCP server cannot create glossary terms or incidents.

**4. Human-in-the-loop is enforced at one choke point.** Every DataHub mutation goes through `DataHubClient.write()`, which raises `ApprovalRequiredError` unless explicitly approved. Do not add write paths that bypass it or expose direct MCP mutation tools to the agent. The agent may only stage validated `Proposal`s; the CLI approval gate executes them.

**5. Every component is a callable tool.** Engines are pure functions over serializable Pydantic models, exposed as LangChain `@tool`s in `agent/tools.py`, so agent orchestration (`agent/loop.py`) is just a bind_tools decision loop. New capabilities should follow this shape: pure function → Pydantic models in/out → registered tool.

**6. LLM access is provider-agnostic via LangChain.** Model selection is the `LLM_MODEL` env var (provider-prefixed, e.g. `anthropic:Codex-opus-4-8`) through `init_chat_model` in `llm/client.py`. Structured outputs use `with_structured_output` with Pydantic models from `models.py`. Don't import provider SDKs directly.

## Seeds and tests

`seeds/metric_families/<family>/` = a `manifest.json` plus one `.sql` per candidate definition. The `weekly_active_users` family is the golden demo scenario with deliberately rigged conflicts (distinct vs non-distinct count, anonymous inclusion, timezone, event filters, source population). Tests in `tests/` assert the extractor recovers exactly these rigged dimensions — if you change a seed SQL, update the matching assertions, and vice versa. Cosmetic invariance (aliases, casing, whitespace, predicate order must produce identical signatures) is a hard requirement tested in `test_cosmetic_variants_produce_identical_signatures`.

## Scope guardrails (from context.md — resist violating these)

- One warehouse (Postgres), ANSI/Postgres SQL only. No Looker/Tableau/Power BI/Databricks integrations.
- Clustering only needs to work on the seeded families.
- UI is the CLI + DataHub's own UI for write-back. No custom frontend.
- Framing is "Semantic Conflict Intelligence", not "Metrics Catalog" — DataHub catalogs metrics you know about; MetricGuard discovers conflicting ones you don't.
