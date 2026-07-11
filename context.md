# MetricGuard — Project Context

## What we're building
**MetricGuard** is an agent that finds where the *same* business metric is computed with *conflicting* logic across an organization, proves how much the definitions disagree, helps a human pick one canonical version, writes that truth back into DataHub, and then stands guard against future drift.

Built for **Build with DataHub: The Agent Hackathon** (Devpost). Primary category: *Agents That Do Real Work*.

## The problem, in one line
A company asks "how many weekly active users did we have?" and three teams give three different numbers — because each defined "active user" differently in SQL that nobody compares. MetricGuard is the detective that surfaces those hidden conflicts.

## Framing (important — protects originality)
We are **Semantic Conflict Intelligence**, NOT a "Metrics Catalog." DataHub is already building a metrics catalog (open, unmerged PR). We do not compete with it — DataHub catalogs the metrics you *know about*; MetricGuard discovers the conflicting ones you *don't*. This stays novel even after their catalog ships.

## Three product modes
- **Discovery** — "Where do we have competing definitions of X?" Groups candidate implementations, proves where they disagree, quantifies the divergence.
- **Guard** — "Did a new/changed query just drift from the approved definition?" Catches semantic breaks before they ship. (Real feature, not scripted.)
- **Sentinel** — watches DataHub for new or semantically changed SQL definitions,
  opens investigations without a human prompt, and resolves, escalates, or
  dismisses with recorded evidence.

## Core design principle: deterministic core, LLM for judgment
- **Deterministic code:** SQL parsing (sqlglot), semantic signature extraction, signature comparison, query execution, divergence math, drift detection.
- **LLM:** clustering judgment, plain-language explanation of conflicts, proposing canonical options.
- Never let the LLM do the verifiable math. This split is what makes it credible to data-engineer judges.

## The linchpin
The **semantic signature engine** is the critical path. It reduces each SQL definition to a structured summary:
```
{ aggregation, entity, grain, timezone, filters, deduplication, null_handling, source_population }
```
Both Discovery (conflict comparison) and Guard (drift detection) consume it. Build it first, build it solid, unit-test it.

## Human-in-the-loop
The agent **proposes**, a human **approves**. It never silently overwrites organizational truth. Write-back happens only after approval.

## Write-back (stays within stock DataHub Core)
Uses verified entities and tools available on the demo's stock DataHub Core
version — pre-existing glossary terms, structured properties, documents,
descriptions, and canonical-vs-divergent tags. Nothing depends on an unverified
or version-skewed capability.

## Scope guardrails (3-week solo build)
- **One warehouse** (Postgres) + **dbt models** + **seeded dashboard metadata** + **ANSI/Postgres SQL** only.
- No Looker / Tableau / Power BI / Databricks. Avoid the integration swamp.
- Demo clustering only needs to work on our seeded metric families — we control the data.
- UI: **`metricguard ui`** starts and follows investigations, renders evidence,
  and hands staged proposals to the human gate. The CLI remains fully supported;
  **DataHub UI** remains the governed system of record for write-back.

## Tech stack (working assumption)
- Python. `sqlglot` for SQL parsing. DataHub Core via docker quickstart. DataHub **MCP Server** for read + write.
- LLM via API for the judgment/explanation layer.
- Components built as **callable tools from day one**, so agent orchestration is just wiring a decision loop.

## Live integrations and contribution
- **Warehouse:** Postgres execution is live behind `WarehouseExecutor`; semantic-only paths degrade cleanly without a DSN.
- **DataHub:** graph discovery, lineage/context, approval-gated write-back, and graph-backed Guard run through the official Agent Context Kit MCP server.
- **Sentinel:** durable polling fingerprint is the demo trigger; DataHub Actions
  is the future production event transport, not a second decision engine.
- **OSS contribution:** a validated `datahub-semantic-conflicts` Skill draft lives under `contrib/datahub-skills/`; external submission remains a separate human decision.

## Judging criteria we're optimizing for (equally weighted)
Use of DataHub (+ bonus for writing back) · Technical Execution · Originality · Real-World Usefulness · Submission Quality. Plus bonus for a merged/submitted OSS contribution.
