# Session worklog — MCP, graph-native discovery, and real write-back

A complete record of the working session: fixing the DataHub box,
activating the MCP server, flipping discovery to read from the graph (#3), and
making write-back real end-to-end (#4) including the structured-properties
"money shot". Everything here was executed and verified against the live
instance. Commands, findings, bugs, and fixes are all captured.

> Environment: DataHub Core v1.5.0.6 (docker quickstart on remote EC2, SSH host
> `internal_too_aws`, tunneled to `localhost:9002`, GMS via
> `localhost:9002/api/gms`). Warehouse: fiction-retail in the `metric` schema on
> RDS. MCP: `uvx mcp-server-datahub` over stdio.

---

## 0. Is the demo data "outdated"? — decision: don't refresh it

Question raised: the fiction-retail warehouse spans **2023-01-01 → 2024-04-30**
(150k orders), ~2 years stale vs today. Should we refresh it for impact?

**Conclusion: no.** The `weekly_revenue` definitions aggregate the full history
(`DATE_TRUNC('week', o.order_date) ... GROUP BY 1`) with no `NOW()`/"last week"
window, so stale dates break nothing — divergence math, signature extraction,
and the DataHub metadata story are all date-independent. The real impact levers
were #3 (discovery from the graph) and #4 (real write-back), not data freshness.
A cosmetic date-shift in `load_fiction_retail.py` is a 5-minute option if a demo
video needs "current" dates — do it last, if at all.

---

## 1. DataHub box incident and recovery

### 1a. "Failed to list secrets / Search query failed" → OpenSearch down
Symptom in the UI: `Failed to load secrets! ... Failed to list secrets. Search
query failed`. Secrets and ingestion sources are listed *through* the search
index, so a down/red OpenSearch produces exactly this while GMS still answers.

Diagnosis (all read-only):
```bash
ssh internal_too_aws 'docker ps -a --format "table {{.Names}}\t{{.Status}}"'   # opensearch Exited (127)
ssh internal_too_aws 'docker logs --tail 40 datahub-opensearch-1'
```
Root cause: `java.lang.OutOfMemoryError: unable to create native thread
(pthread_create EAGAIN)` — **thread/process-limit exhaustion**, NOT RAM
(`OOMKilled=false`) or disk (27%). The container hit its native-thread ceiling
after ~26h and fataled.

Fix: restart the container (the leaked threads free once it's down):
```bash
ssh internal_too_aws 'docker start datahub-opensearch-1'
```
Result: cluster returned to **yellow** (normal for single-node quickstart — just
unassigned replica shards), UI listed secrets again, source reappeared (it was
always in MySQL; only the index was gone).

### 1b. Ingestion run failing at venv setup → empty version pin
After the restart, the `Datahub-Local` (Postgres) ingestion still failed:
```
uv pip install -r requirements.txt  → non-zero exit status 2
error: Couldn't parse requirement ... at position 48
```
The generated `requirements.txt` was:
```
# Generated at ...
acryl-datahub[postgres]==        # <-- empty version after ==
```
Cause: the ingestion source's **CLI Version** field was set to an *empty string*.
Old pip tolerated `==`; the new `uv`-based executor rejects it.

Fix (UI): **Data Sources** (DataHub Core renames "Ingestion" to "Data Sources")
→ edit `Datahub-Local` → Advanced → **CLI Version** = `1.5.0.6` (the server is
`1.5.0.6+docker`; the `+docker` build tag isn't on PyPI, so pin the clean
`1.5.0.6`, verified resolvable). Re-run succeeds.

### 1c. What the ingestion ▶ ("start") actually does
Triggers the same job as the schedule, ad-hoc. Chain: GMS creates an
`ExecutionRequest` → Kafka → the `datahub-actions` executor builds an isolated
venv (`uv pip install acryl-datahub[postgres]==1.5.0.6`) → runs
`datahub ingest` → the connector introspects Postgres and emits
MetadataChangeProposals → GMS persists to MySQL and indexes into OpenSearch.
**It syncs metadata (table/column shape), not warehouse rows.** With stateful
ingestion on, a re-run also soft-deletes tables that vanished from Postgres.

### 1d. `scripts/datahub_doctor.sh` — repeatable health check + recovery
Read-only by default; `--fix` restarts Exited containers (OpenSearch first);
starts `docker.service` when possible, and verifies that the frontend is
published and answering on the server's port 9002. `--enable-autostart` enables
Docker at boot and sets the long-running quickstart containers to
`restart=unless-stopped`; the one-shot system-update container is excluded.
`--logs` dumps recent ingestion-failure detail. Run from the laptop over the
tunnel host without copying:
```bash
ssh internal_too_aws 'bash -s'          < scripts/datahub_doctor.sh   # diagnose
ssh internal_too_aws 'bash -s' -- --fix < scripts/datahub_doctor.sh   # restart downed containers
ssh internal_too_aws 'bash -s' -- --fix --enable-autostart \
  < scripts/datahub_doctor.sh                                  # recover + survive reboot
```
Checks: container status, memory/disk, **native-thread ceiling** (`threads-max`
vs live), **`vm.max_map_count`** (flags <262144), OpenSearch cluster health, GMS
`/health`, frontend port publishing/server-local HTTP, Docker boot state,
container restart policies, and recent ingestion failures (incl. the
empty-`==` pin). If the server-local UI passes but `localhost:9002` on the
laptop does not, recreate the SSH tunnel with
`ssh -N -L 9002:127.0.0.1:9002 internal_too_aws`.

### 1e. Latent, still-open
- `vm.max_map_count=65530` (< 262144 recommended) — OpenSearch will eventually
  die again. Durable fix: `sysctl -w vm.max_map_count=262144` + persist in
  `/etc/sysctl.d/`, and/or raise the opensearch container's `pids`/`nproc` limit.

---

## 2. MCP activation + capability lockdown

MCP was enabled (`DATAHUB_MCP_TRANSPORT=stdio`). `metricguard datahub tools`
dumped the server's **real** tool names. Reconciled against the guesses hardcoded
in `datahub/mcp_client._CAPABILITIES`:

| Capability | Code had guessed | Server actually has | Verdict |
|---|---|---|---|
| search | search / search_entities / … | **`search`** | ✅ |
| get_dataset_queries | get_dataset_queries / … | **`get_dataset_queries`** | ✅ |
| get_lineage | get_lineage / lineage | **`get_lineage`** | ✅ |
| add_tag | add_tag / add_tags / set_tags | **`add_tags`** | ✅ |
| update_description | update_description / … | **`update_description`** | ✅ |
| structured props | (unknown) | **`add_structured_properties`** (dedicated) | ✅ |
| **upsert_glossary_term** | create/upsert/add_glossary_term | **none** (only `add_terms`, attaches existing) | ❌ no create-term tool |
| **create_incident** | create_incident / raise_incident | **none** | ❌ no incident tool |

Changes to `mcp_client.py`:
- `_CAPABILITIES` trimmed to the verified real names; added `get_entities`;
  deleted the two fictional write capabilities.
- **Unwrap bug fixed** — MCP returns `[{"type":"text","text":"<json>"}]`; the old
  `_call` only `json.loads`-ed when the whole result was a string, so it handed
  downstream the content-block envelope. Added `_unwrap` (concatenate text
  blocks → parse). Without this, *every* MCP read silently returned junk.
- `search_queries`/`get_dataset_queries` now dig into `searchResults`/`queries`.
- `_execute_write` mapping realigned to real tools (see #4).

Consequence for write-back: **no create-glossary-term, no incident tool over
MCP** — the plan had to change (see #4).

---

## 3. #3 — Discovery reads from the graph

The thesis: MetricGuard is *Semantic Conflict Intelligence*, not a catalog
lookup — it must **not** be told which datasets form a family. It pulls candidate
SQL from DataHub and lets the deterministic clustering signals recover the
families (and thus hidden conflicts) from semantics, not from a declaration.

### Probe first (de-risk the key assumption)
A direct probe confirmed `get_dataset_queries(urn=<finance dataset>)` returns the
**actual conflicting SQL** from the graph, and locked the arg names:
`search`→`query`, `get_dataset_queries`→`urn`, `get_lineage`→`urn`,
`get_entities`→`urns`.

### New code
- **`src/metricguard/datahub/discovery.py`** — `candidates_from_graph(client,
  keyword, dialect)`: search → unique dataset URNs → `get_dataset_queries` per
  dataset → build `MetricDefinition` (with graph provenance) → run the
  *unchanged* `extract_signature`. Deduped by query URN.
- **`MetricDefinition`** gained `dataset_urn` / `query_urn` (provenance for
  write-back; empty for seed candidates).
- **`StubDataHubClient`** now emits the *same* MCP envelope shape (via
  `from_specs`), so the discovery path is identical in tests and production.
- **`discover --from-graph [--keyword ...]`** CLI flag (seeds stay the default;
  graph is opt-in and degrades cleanly when DataHub isn't configured).

### Live result
`metricguard discover --from-graph` pulled **6 candidates** and recovered **two
families from semantics despite different names**:
- `weekly_revenue` family → `weekly_revenue` + `revenue_tile` + `weekly_bookings`
  (grouped by shared source `metric.orders`, same entity, same grain), conflicts
  proven (status filter high; `SUM(total_amount)` vs recomputed line-items
  critical; source population critical).
- `wau` family → `finance_wau` + `marketing_wau` + `wau_tile` (COUNT vs
  COUNT(DISTINCT) critical, timezone, anonymous inclusion).

Tests: `tests/test_graph_discovery.py` (recovers the family via the stub, offline).

---

## 4. #4 — Write-back made real

### Mutation tool arg schemas (probed, zero prereq unless noted)
- `save_document(document_type, title, content, related_assets, …)` — zero prereq;
  `related_assets` links to the datasets.
- `add_tags(tag_urns, entity_urns, …)` — **tag entity must exist first**.
- `update_description(entity_urn, operation[replace|append|remove], description)`
  — zero prereq.
- `add_structured_properties(property_values{urn:[vals]}, entity_urns)` — **property
  definitions must exist first**.
- `add_terms(term_urns, entity_urns)` — **glossary term must exist first**.

### Redesigned write-back (no create-term, no incident)
- **`src/metricguard/datahub/writeback.py`** — pure builders returning `Proposal`s
  whose `payload` matches the tool args exactly:
  - decision **document** (canonical SQL + superseded list, linked to all datasets)
  - **structured_property** proposal = the canonical `SemanticSignature` as values
  - canonical **tag** (`metricguard_canonical`)
  - per divergent: **tag** (`metricguard_divergent`) + description **redirect**
  - `build_canonical_writeback` order: `document, structured_property, canonical
    tag, then per-divergent (tag, redirect)`.
- **`resolve --canonical <name> [--keyword ...]`** CLI — discovers the family from
  the graph, splits canonical vs divergent, stages the full set. Verified live:
  staged the correct set for `weekly_revenue` in ~20s.
- **`_is_mutation`** fixed — `save` and `remove` were missing from the markers, so
  `save_document`/`remove_*` would have leaked to the agent's tool belt, bypassing
  the proposal gate (rule #4 violation). Now filtered.

### Two real bugs found while landing the first live write
1. **Silent write failures.** MCP tools report failures as an `"Error calling
   tool ..."` **string**, not an exception, so `_execute_write` marked failed
   writes as *executed*. The first "✔ Executed" for the canonical tag was a lie —
   caught by reading `globalTags` back from GMS (aspect was absent).
   **Fix:** `_call` now raises `RuntimeError` on that error string; `proposals
   approve` catches it and leaves the proposal **pending**. Regression test added.
2. **Per-call MCP respawn (looked like a hang).** `get_tools()` yields
   session-less tools that respawn the stdio server on every `ainvoke` (~10s), so
   discovery on N datasets took ~N×10s. **Fix:** `mcp_client._gather_graph_reads`
   does search + all `get_dataset_queries` inside **one** `client.session()`;
   `candidates_from_graph` uses it via `client.bulk_discovery` (falls back to
   per-call for the stub). Discovery dropped to one startup.
   - Gotcha: killed CLI runs leave **orphaned `uvx mcp-server-datahub` processes**
     that contend and stall new connections. Clear them:
     `pkill -f mcp-server-datahub`.

### Tags/terms/structured-props don't auto-create → bootstrap
`add_tags` failed with `Failed to validate label ... Urn does not exist` — tags
must exist before association, and there's **no MCP create tool**. Same for
glossary terms and structured-property definitions.

- **`scripts/bootstrap_writeback_entities.py --emit`** (SDK REST emitter, same
  pattern as `simulate_org.py`) creates, once:
  - tags `metricguard_canonical`, `metricguard_divergent`
  - 8 structured-property definitions, one per `SemanticSignature` field
    (`urn:li:structuredProperty:metricguard_<field>`, STRING, MULTIPLE, dataset).

### Execution (all through the human-approval gate) + verification
`#1` — approved all 6 proposals. Verified via GMS `entitiesV2`:
- finance `weekly_revenue`: `globalTags = [urn:li:tag:metricguard_canonical]`
- `revenue_tile` & `weekly_bookings`: `metricguard_divergent` tag +
  description contains the MetricGuard redirect.

`#2` — the **structured-properties money shot**. Bootstrapped the definitions,
staged + approved a `structured_property` proposal for the canonical dataset.
Verified in GMS the `structuredProperties` aspect on finance `weekly_revenue`:

| Property | Value |
|---|---|
| aggregation | `SUM(total_amount)` |
| entity | `total_amount` |
| grain | `week` |
| filters | `NOT orders.order_status IN ('canceled', 'returned', 'disputed')` |
| source_population | `metric.orders` |

The canonical semantic signature is now first-class, queryable metadata in the
graph — the closing "aha" and the highest-weighted judging axis.

---

## 5. Files touched this session

New:
- `src/metricguard/datahub/discovery.py` — graph-native candidate discovery
- `src/metricguard/datahub/writeback.py` — proposal builders (incl. structured props)
- `scripts/datahub_doctor.sh` — box health check + recovery
- `scripts/bootstrap_writeback_entities.py` — one-time tag + structured-property defs
- `tests/test_graph_discovery.py`, `tests/test_writeback.py`
- `docs/tags.md` (this file)

Changed:
- `datahub/mcp_client.py` — real capabilities, unwrap fix, error-raising `_call`,
  `_gather_graph_reads`/`bulk_discovery`, real write mapping
- `datahub/base.py` — MCP-shaped stub reads (`from_specs`), removed dead
  `propose_*` builders, kind comment
- `models.py` — `MetricDefinition.dataset_urn`/`query_urn`
- `agent/tools.py` — `_is_mutation` (+save/remove), staging-tool docstring
- `cli.py` — `discover --from-graph`, `resolve`, `proposals approve` error handling
- `progress.md`

State: **35 tests pass**, lint at project baseline.

---

## 6. Open follow-ups
- Wire the **agent loop** (`agent/tools.py` still reads `seeds/`) to
  `candidates_from_graph` so the agent runs discovery→write-back end-to-end.
- Confirm **`divergence`** executes on graph-sourced candidates (weekly_revenue
  has warehouse data; wau is signature-only).
- Fix **`vm.max_map_count`** on the box (see §1e) so OpenSearch stops dying.
- Optional: cosmetic date-shift of the warehouse for a "current"-looking demo (§0).
