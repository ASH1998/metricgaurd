# MetricGuard — The Pitch

## Thesis

**Deterministic proof, agent judgment, human authority — and an agent that acts
on its own initiative but knows when to say "this needs a human."**

## The four pillars

1. **Deterministic proof.** Parsing, semantic signatures, conflict diffs,
   warehouse divergence, and drift detection are pure code. The numbers a
   judge sees — *15.06% mean divergence, first diverged 2022-12-26* — were
   executed, not estimated. The LLM never does the verifiable math.

2. **Agent judgment.** The LLM decides what the evidence means: which conflict
   deserves warehouse proof, whether a canonical recommendation is defensible,
   what follow-up context to pull from the graph. It chooses among
   deterministic operations; it never replaces them.

3. **Human authority.** Every DataHub mutation passes through one approval
   choke point. The agent's only write power is staging proposals; a human
   executes them, and the resolution lands as governed metadata in stock
   DataHub — where the next person or agent inherits it.

4. **Initiative with humility.** Sentinel mode notices new or changed SQL in
   the graph without being asked, investigates, and ends every autonomous run
   in exactly one of three outcomes: `staged_resolution`,
   `needs_human_decision`, or `dismissed_with_evidence`. It escalates what it
   cannot defend — an agent that knows the boundary of its own evidence.

## Why now

AI didn't shrink this problem — it exploded it. Agents write SQL on demand
now, and every generated query is a fresh chance to quietly redefine
"revenue." Human-scale metric drift took months; agent-scale drift happens
per conversation. MetricGuard is the control plane that keeps every agent's
numbers honest — the agent that guards the other agents.

## In one breath

A new SQL definition appears in DataHub. Nobody asks MetricGuard anything.
It notices, determines the change threatens an approved business metric,
gathers graph and warehouse evidence, and either stages a governed resolution
or explicitly asks for the one human decision it needs.

## The product loop

`metricguard sentinel` keeps a durable fingerprint of the SQL definitions
observed through DataHub. A new definition or semantic change opens an agent
investigation; a cosmetic edit is dismissed by deterministic signature evidence
without spending an LLM call. The resulting case appears in the same durable run
store and Mission Control timeline as a human-started investigation.

The trigger is intentionally boring. The product value is the bounded decision
chain after it: *is this relevant, what might it conflict with, which evidence is
missing, is warehouse proof warranted, and can a resolution be defended?*
