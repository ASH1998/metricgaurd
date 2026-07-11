# MetricGuard — The Top-1 Plan (final 4 weeks)

**Window:** now → Aug 10, 2026, 5pm ET. Three build weeks + one post-prod week.
**Premise:** the full loop (discover → prove → resolve → write back → guard) is
built and live-verified (see `progress.md`). What separates a strong finalist
from the winner is no longer features — it is what a *judge experiences*:
can they run it, can they see the agent think, can they fail to break it.

## The five gaps between top-5 and top-1

1. **Reproducibility** — judges must go from `git clone` to watching the agent
   resolve a conflict, on their machine, without us. `make demo` exists but has
   never been executed end-to-end.
2. **Visible agency** — the agent is genuinely agentic (iterative tool loop,
   proof-pair selection, refusal capability, grounding self-rejection) but the
   agency is *compressed*: one composed investigation tool + a prompt that
   mandates calling it first. To a judge it can read as: click → pipeline →
   summary → proposals. The fix is not "more LLM" — it is exposing more
   consequential decisions in the product experience, and adding a standing
   **sentinel mode** where the agent acts on its own initiative. Proactivity is
   not agency: the polling trigger is transport; the agency is the decision
   chain after it (relevant? conflicts with what? evidence missing? prove?
   resolve / refuse / dismiss?).
3. **Something to SEE** — the CLI + DataHub tags don't produce a "whoa" moment.
   The demo needs a visual that is *agentic*, not dashboard wallpaper. Answer:
   **Mission Control** (week 2) — a live view of the agent investigating, being
   fact-checked, proving divergence, and waiting at the human gate.
4. **Robustness proof** — the demo now has 12 conflicting candidates across four
   independently clustered families, three with live Postgres divergence. It still
   needs explicit near-miss controls so a skeptic can see what stays out.
5. **The bonus criteria** — OSS contribution (Skill draft is ready, unsubmitted)
   and Submission Quality (video, Devpost text — reserved for week 4).

---

## Week 1 (Jul 12–18) — "It runs anywhere"

Goal: a judge-grade environment that cannot embarrass us.

- [ ] Execute `make demo` end-to-end on a docker machine (fresh clone, no .env).
      Fix what breaks. Re-run until boring. This also de-risks demo day.
- [ ] Verify one live `--emit` with SDK 1.6 against the 1.5 server (or pin `<1.6`).
- [ ] Fix `vm.max_map_count` on the EC2 box (OpenSearch death = dead demo), or
      decide the demo runs fully local and the box is only a backup.
- [ ] **Negative controls in the org sim**: seed 3–4 near-miss assets that must
      NOT cluster into the families — e.g. `monthly_revenue` (different grain
      story), `weekly_signups` (different entity), an `avg_order_value` tile,
      a raw table with a `revenue` column that isn't a metric. Tests assert
      exclusion with visible clustering evidence.
- [x] **Broaden positive cases**: add executable `weekly_order_volume` and
      `weekly_refund_amount` families over the existing Postgres source. The
      catalog now has four independent conflicts rather than one repeated story.
- [ ] **Extractor honesty**: unsupported SQL constructs produce an explicit
      "signature incomplete: <construct>" outcome — never a silent misread.
      Matters for the live-judging moment when someone pastes arbitrary SQL.

Milestone: fresh clone → `make demo` → agent resolves weekly_revenue, ignores
the decoys — on a machine we've never touched.

## Week 2 (Jul 19–25) — "Watch the agent think"

Goal: the agent's judgment is legible, demonstrably non-scripted — and visible
on a screen that makes people lean in.

### MetricGuard UI (`metricguard ui`) — the operational workspace

A local operational UI over the same MetricGuard agent and deterministic core.
It starts investigations, follows durable run traces in `.metricguard/runs/`,
renders investigation/divergence evidence, and hands staged proposals to the
existing human approval gate. It is a real product path, not a separate demo
runtime or a second implementation of MetricGuard.

**Architecture — the JSON contract is the seam.** The frontend is one static
page (HTML + inline JS/SVG, no build step, no framework) that consumes only a
frozen JSON contract: run trace, investigation report, divergence points,
proposal states. Where that JSON comes from is invisible to the page.

**The product is `metricguard ui`, and it works everywhere** — local dev, a
judge's fresh clone, `make demo`: starlette/uvicorn (now core deps) serving the
page + `GET /api/runs`, `GET /api/runs/<id>`, `POST /api/investigations`, and
SSE `/api/stream/<id>`. Replay (`--replay <run-id>`) and live operation are both
served this way. The APIs call the existing agent/run stores; they do not bypass
the deterministic engines or approval choke point.

**github.io is one frozen snapshot, not a channel**: `metricguard ui --export
<run-id> -o site/` emits the page + the golden run's JSON; deploy that ONCE to
gh-pages as the "quick check" link (on boot the page probes `/api/runs`; no
backend → static replay from `./data/`). No maintenance, no drift — it's a
screenshot that happens to be alive.

Replay timing is client-side from recorded trace timestamps. Build order:
replay mode FIRST (judge/video/offline-safe), live SSE second — the demo
survives without live.

**Aesthetics**: match DataHub's visual family (spacing, type scale, navy/blue
palette, card layout) so Mission Control ↔ DataHub UI reads as one coherent
product. No logo/trade-dress copying — "native companion," not fake DataHub.
Freeze the JSON contract early so frontend and backend never drift.

Four panels, built in this order (later ones are cuttable):

1. **Agent timeline** — tool calls stream in with args/results; grounding-check
   interventions render as visible events ("final answer failed grounding →
   rewrite demanded"). Watching the agent get *fact-checked live* is the beat
   no other team will have.
2. **Divergence proof** — the two revenue series drawn from the real divergence
   points, gap shaded, "first divergence 2022-12-26" annotated. The money shot.
3. **Org conflict map** — the discovered org assembling as evidence arrives:
   datasets/owners/domains/lineage, cluster halos, conflict edges colored by
   severity — and the week-1 decoys visibly staying OUTSIDE the clusters.
4. **Approval gate** — proposal cards flip pending → approved → executed.
   Approval itself stays a human action through the existing gated path.

Hard scope rules: **no npm, no build step, no framework** — one HTML file,
inline JS/SVG. The browser never calls an LLM directly; it invokes the existing
MetricGuard agent API, and every governance mutation still passes through the
approval-gated DataHub client. If scope threatens week 2, cut panels 4 then 3.

### Decompose the investigation into visible decisions

Keep the deterministic engines untouched; give the agent *narrower* operations
so its choices become the product experience: identify changed candidate → find
plausible peers → inspect org context → compare selected definitions → choose a
proof pair → assess evidence sufficiency → resolve / refuse / escalate. The LLM
chooses among deterministic operations — it never replaces their math. The
composed mega-tool stays as the fast path; the decomposed path will become the
preferred sentinel and demo path once its focused operations are verified.

- [ ] Add change-scoped graph tools for plausible peers and focused context.
- [ ] Separate pair selection, evidence-sufficiency assessment, and terminal
      decision into legible trace events. The first sentinel slice still hands
      material changes to the composed investigation path.

### Sentinel mode (`metricguard sentinel`) — the standing agent

The winning beat: *a new SQL definition appears in DataHub. Nobody asks
MetricGuard anything. It notices, determines the change threatens an approved
metric, gathers graph + warehouse evidence, and either stages a governed
resolution or explicitly asks for the missing human judgment.*

- [x] Persist an observed-definition fingerprint; first scan baselines without
      alerting, cosmetic SQL edits are dismissed by equal semantic signatures,
      and new/semantic changes open durable autonomous investigations. The run
      records exact trigger provenance and appears through the existing UI API.
- [ ] Live-verify a second scan against the demo DataHub after ingesting a rogue
      Query entity; rehearse restart/retry behavior and capture the golden run.
- Polling is the **demo transport**, presented as such; DataHub Actions (the
  real-time metadata-event framework) is the documented production path.
- [x] **Three first-class terminal outcomes** persisted on autonomous runs:
  `staged_resolution` · `needs_human_decision` · `dismissed_with_evidence`.
  Dismissed-with-evidence is what separates a sentinel from an alert generator.
- [x] **Show why effort was spent** — the sentinel trace renders skipped,
  material, and cosmetic counts plus the investigate/dismiss decision. Continue
  extending this with agent decisions like "skipped 18 unchanged
  queries", "timezone + source_population changed", "chose this pair for
  largest downstream blast radius", "warehouse unavailable; canonical would be
  under-supported; no proposal staged."

### Agent scenarios (the content the UI displays)

- [x] **Sentinel beat**: ingest new competing queries into
      DataHub live; the dashboard grows an investigation nobody asked for, with
      a staged proposal (or explicit refusal) at the human gate. Pairs with the
      narrative reframe: agents mass-produce SQL now — MetricGuard is the agent
      that keeps other agents' numbers honest.
- [ ] **Refusal scenario**: an investigation where evidence is genuinely
      ambiguous (e.g. WAU family — no warehouse data, symmetric graph evidence).
      The agent declines to stage and states exactly what human decision is
      needed — i.e. `needs_human_decision` exercised on camera.
- [ ] **Guard as ongoing work**: a real GitHub Actions example in-repo that runs
      `guard datahub-check` on PR-changed SQL (exit codes are already
      contractual) — the CI complement to sentinel.
- [x] Full-catalog scan handles the current 12-definition/four-family org in one
      run without prompt surgery.
- [ ] Re-verify the full-catalog scan after the negative-control decoys land.

Milestone: sentinel catches an unannounced change end-to-end on screen, and the
three rehearsed scenarios (discover+resolve · refuse · guard-catch) each show a
different kind of judgment, with the *why* legible in the timeline.

## Week 3 (Jul 26–Aug 1) — "OSS, scale, freeze"

Goal: bonus criteria locked; everything frozen and boring.

- [ ] **Submit the Skill upstream** (`datahub-semantic-conflicts`) — target the
      official `datahub-project/datahub-skills` registry (ships five skills:
      setup/search/lineage/enrich/quality; ours is a credible sixth — a genuinely
      new workflow, which is exactly what DataHub says skills are for). Submit
      early enough that a maintainer response can land before judging.
- [ ] **`datahub-agent-context` SDK spike (timeboxed, half a day)** — compare
      its `build_langchain_tools(DataHubClient)` path against our MCP path:
      tool coverage, self-hosted compatibility, and whether writes can be forced
      through the approval choke point. Adopt ONLY if it deletes meaningful code
      or unlocks capabilities — not for the sentence. Note: docs are ahead of
      our 1.5 server (e.g. `add_glossary_terms` vs `add_terms` naming) — runtime
      capability verification stays regardless.
- [ ] README final pass *as a judge*: follow it verbatim on a clean machine
      (including `metricguard ui --replay` on the shipped example run).
- [ ] Ship a **golden replay run** in `examples/` so judges get the Mission
      Control experience with zero infrastructure.
- [ ] Deploy the **one frozen gh-pages snapshot** (`metricguard ui --export` of
      the golden run, deployed once, then left alone) and link it in README +
      Devpost as the zero-install quick check.
- [ ] Refresh `examples/` from the final environment (numbers must match the
      video we record in week 4).
- [ ] Freeze: seeds, org sim, scenarios, UI, `.env` shape. Anything not frozen
      by Aug 1 gets cut, not finished.

Milestone: submission-ready repo; only presentation work remains.

## Week 4 (Aug 2–10) — Post-production (reserved)

- 3-minute video, storyboarded around the UI: open on the fight (two
  dashboards, two numbers) → agent timeline streams → divergence chart draws
  the 15% gap → human approves at the gate → cut to stock DataHub UI showing
  the governed write-back → **climax: sentinel** — a rogue AI-generated query
  lands in DataHub, nobody touches anything, and MetricGuard catches it,
  proves it, and asks for the one human decision it needs. Record against the
  frozen env.
- Devpost text, category selection, screenshots, survey opt-in.
- Slack: buffer for overruns. Submit **Aug 8**, not Aug 10.

---

## Risk register (updated)

| Risk | Mitigation |
|---|---|
| `make demo` hides an env-specific dependency | Week-1 item #1; test on a machine we've never configured |
| OpenSearch dies on the EC2 box mid-demo | Fix max_map_count now; local quickstart is the primary demo path |
| Decoys accidentally cluster (embarrassing inverse) | Deterministic clustering signals + tests before any demo |
| Mission Control scope creep | Replay-first; panel cut order 4→3; one file, no framework, read-only |
| Sentinel scope creep (event infra, schedulers) | Durable polling fingerprint is built as the demo transport; DataHub Actions remains a documented future path |
| Decomposed agent path gets flaky vs mega-tool | Mega-tool remains the fast path; decomposed ops are additive, each still deterministic inside |
| Skill PR sits unreviewed | Submit in week 3's first days; "submitted" already scores |
| SDK 1.6 vs server 1.5 emit incompatibility | One live emit check in week 1; pin if it bites |

## What we deliberately do NOT do

- No second frontend-only implementation of MetricGuard: the UI is the
  operational layer over the existing agent, deterministic evidence, durable
  runs, and proposals. DataHub remains the governed system of record and every
  mutation retains the human approval gate.
- No second warehouse, no BI-tool integrations (unchanged).
- No multi-agent theater — one agent that visibly reasons beats two that don't.
- No extractor generalization beyond honest failure — seeds stay the golden path.
