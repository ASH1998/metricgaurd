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
2. **Visible agency** — the loop works, but most judgment is compressed into one
   composed investigation tool and a scripted prompt. Judges of "Agents That Do
   Real Work" need to *watch decisions happen*: which pair to prove, when to
   refuse to act, what needs a human.
3. **Something to SEE** — the CLI + DataHub tags don't produce a "whoa" moment.
   The demo needs a visual that is *agentic*, not dashboard wallpaper. Answer:
   **Mission Control** (week 2) — a live view of the agent investigating, being
   fact-checked, proving divergence, and waiting at the human gate.
4. **Robustness proof** — the demo world is 6 rigged candidates that all cluster.
   A skeptic's first question: "does it just match everything?" There are no
   negative controls, and the extractor has never met SQL it wasn't tuned for.
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

### Agent scenarios (the content Mission Control displays)

- [ ] **Refusal scenario**: an investigation where evidence is genuinely
      ambiguous (e.g. WAU family — no warehouse data, symmetric graph evidence).
      The agent must decline to stage and state exactly what human decision is
      needed. The single most convincing "real agent" beat we can show.
- [ ] **Decision legibility**: run traces carry the *why* — why this pair was
      proven, why this canonical, which evidence carried it — so both
      `runs show` and the timeline panel can render it.
- [ ] **Guard as ongoing work**: a real GitHub Actions example in-repo that runs
      `guard datahub-check` on PR-changed SQL (exit codes are already
      contractual). Turns "agent did work once" into "agent guards forever."
- [ ] Full-catalog scan (`keyword=*`) handles the enlarged org (families +
      decoys) in one run without prompt surgery.

Milestone: `metricguard ui --replay` plays the golden run start-to-finish, and
three rehearsed scenarios (discover+resolve · refuse · guard-catch) each show a
different kind of judgment on screen.

## Week 3 (Jul 26–Aug 1) — "OSS, scale, freeze"

Goal: bonus criteria locked; everything frozen and boring.

- [ ] **Submit the Skill upstream** (`datahub-semantic-conflicts`) — early enough
      that a maintainer response can land before judging. Merged/submitted OSS
      is an explicit bonus criterion.
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

- 3-minute video, now storyboarded around Mission Control: open on the fight
  (two dashboards, two numbers) → agent timeline starts streaming → conflict
  map assembles, decoys stay out → divergence chart draws the 15% gap → human
  approves at the gate → cut to DataHub UI showing the write-back → guard
  catches the drift PR. Record against the frozen env.
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
