# Changelog

Notable product changes are recorded here. MetricGuard is still pre-release, so
entries are grouped by date rather than a public version number.

## 2026-07-16

### Added

- Automatic organization-wide conflict discovery when a live workspace has no
  investigations.
- LLM-planned broad-request coordination with deterministic fallback and up to six
  focused child investigations.
- Parent/child run relationships and delegated-run provenance in the durable run
  store and UI contract.
- Stop and delete controls for local investigations.
- A run-scoped governed-proposals API and dedicated Proposals tab.
- This changelog.

### Changed

- Increased the configurable agent reasoning budget to 40 iterations through
  `METRICGUARD_MAX_AGENT_ITERATIONS`.
- Replaced the ambiguous “Iteration Limit” product label with “Needs continuation.”
- Switched the UI typography to a JetBrains Mono-first system font stack.
- Simplified proposal cards into compact, action-first rows with readable asset
  names; full URNs, proposal IDs, and agent rationale now live in collapsed
  technical details.
- Nested delegated investigations under their coordinating scan in the sidebar.
- Moved completed resolution proposals into their own review tab while preserving
  the existing human approval gate.

### Fixed

- Preserved UTF-8 text when the Windows-native UI reads and exports run artifacts.
- Prevented broad discovery prompts from forcing every conflict family through one
  monolithic agent loop.
