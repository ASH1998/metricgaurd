# MetricGuard — The Top-1 Plan (final 4 weeks)

**Window:** now → Aug 10, 2026, 5pm ET. Three build weeks + one post-prod week.
**Premise:** the full loop (discover → prove → resolve → write back → guard) is
built and live-verified (see `progress.md`). What separates a strong finalist
from the winner is no longer features — it is what a *judge experiences*:
can they run it, can they see the agent think, can they fail to break it.

## The four gaps between top-5 and top-1

1. **Reproducibility** — judges must go from `git clone` to watching the agent
   resolve a conflict, on their machine, without us. `make demo` exists but has
   never been executed end-to-end.
2. **Visible agency** — the loop works, but most judgment is compressed into one
   composed investigation tool and a scripted prompt. Judges of "Agents That Do
   Real Work" need to *watch decisions happen*: which pair to prove, when to
   refuse to act, what needs a human.
3. **Robustness proof** — the demo world is 6 rigged candidates that all cluster.
   A skeptic's first question: "does it just match everything?" There are no
   negative controls, and the extractor has never met SQL it wasn't tuned for.
4. **The bonus criteria** — OSS contribution (Skill draft is ready, unsubmitted)
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

Goal: the agent's judgment is legible and demonstrably non-scripted.

- [ ] **Refusal scenario**: an investigation where evidence is genuinely
      ambiguous (e.g. WAU family — no warehouse data, symmetric graph evidence).
      The agent must decline to stage and state exactly what human decision is
      needed. This is the single most convincing "real agent" beat we can show.
- [ ] **Decision legibility**: `runs show` renders the *why* — why this pair was
      proven, why this canonical, which evidence carried it. Judges read traces.
- [ ] **Guard as ongoing work**: a real GitHub Actions example in-repo that runs
      `guard datahub-check` on PR-changed SQL (the exit codes are already
      contractual). Turns "agent did work once" into "agent guards forever" —
      the category's "next person or agent inherits the knowledge" line, live.
- [ ] Full-catalog scan (`keyword=*`) handles the enlarged org (families +
      decoys) in one run without prompt surgery.
- [ ] Rehearse the three demo beats end-to-end: discover+resolve · refuse ·
      guard-catch.

Milestone: three rehearsed scenarios, each showing a different kind of judgment,
none of which work by luck.

## Week 3 (Jul 26–Aug 1) — "OSS, scale, freeze"

Goal: bonus criteria locked; everything frozen and boring.

- [ ] **Submit the Skill upstream** (`datahub-semantic-conflicts`) — early enough
      that a maintainer response can land before judging. Merged/submitted OSS
      is an explicit bonus criterion.
- [ ] README final pass *as a judge*: follow it verbatim on a clean machine.
- [ ] Refresh `examples/` from the final environment (numbers must match the
      video we record in week 4).
- [ ] Freeze: seeds, org sim, scenarios, `.env` shape. Anything not frozen by
      Aug 1 gets cut, not finished.

Milestone: submission-ready repo; only presentation work remains.

## Week 4 (Aug 2–10) — Post-production (reserved)

- 3-minute video: open on the fight (two dashboards, two numbers) → live graph
  discovery → the 15%+ divergence proof → human approves → write-back visible in
  DataHub UI → guard catches the drift PR. Record against the frozen env.
- Devpost text, category selection, screenshots, survey opt-in.
- Slack: buffer for overruns. Submit **Aug 8**, not Aug 10.

---

## Risk register (updated)

| Risk | Mitigation |
|---|---|
| `make demo` hides an env-specific dependency | Week-1 item #1; test on a machine we've never configured |
| OpenSearch dies on the EC2 box mid-demo | Fix max_map_count now; local quickstart is the primary demo path |
| Decoys accidentally cluster (embarrassing inverse) | Deterministic clustering signals + tests before any demo |
| Skill PR sits unreviewed | Submit in week 3's first days; "submitted" already scores |
| Scope creep in week 2 agent work | Only the three scenarios; no new engines, no new tools unless a scenario demands one |
| SDK 1.6 vs server 1.5 emit incompatibility | One live emit check in week 1; pin if it bites |

## What we deliberately do NOT do

- No custom frontend, no second warehouse, no BI-tool integrations (unchanged).
- No multi-agent theater — one agent that visibly reasons beats two that don't.
- No extractor generalization beyond honest failure — seeds stay the golden path.
