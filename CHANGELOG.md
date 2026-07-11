# Changelog

All notable **repository-wide** changes are documented here — cross-cutting work
that touches shared infrastructure or more than one skill. This is separate from
each skill's own version: every skill package versions independently in its own
`.claude-plugin/plugin.json` and is released/tagged on its own schedule (see the
root [README](README.md)'s "Releasing A Skill" section). Do not duplicate a
single skill's routine release here; log an entry only when the change is
repo-wide or spans more than one package.

The current repo version lives in [`VERSION`](VERSION) and is tagged
`repo-vX.Y.Z`.

## [1.3.3] - 2026-07-11

Shared-infrastructure patch release for the proof-packet engine:

- Hardened `workpaper-kit` so saved proofs that already live inside the output
  folder and collide with generated artifact names are copied to
  `source-proof-*` before render time, preserving the original proof bytes and
  hashes.
- Bumped `workpaper-kit` to `1.0.1` and regenerated the vendored
  `scripts/_workpaper.py` copies in `get-yearly-fx-rate`,
  `get-year-end-fx-rate`, and `statements-to-interest`.

## [1.3.2] - 2026-07-11

Shared-infrastructure release for the workpaper engine and the packages that
now depend on it:

- Added explicit versioning to `workpaper-kit` with its own
  `.claude-plugin/plugin.json`.
- Refactored `statements-to-interest` to render through the shared
  `ReportlabPacketRenderer`.
- Regenerated the vendored `scripts/_workpaper.py` copies in
  `get-yearly-fx-rate` and `get-year-end-fx-rate`.
- Bumped `get-yearly-fx-rate` to 1.3.4, `get-year-end-fx-rate` to 1.4.3, and
  `statements-to-interest` to 1.2.1.

## [1.3.1] - 2026-07-09

Initial global version marker, set to 1.3.1 rather than a fresh 1.0.0 to reflect
the repo's existing maturity (matching the highest current per-skill version,
`fbar-threshold-check` at 1.3.1) instead of implying this collection is brand
new. Snapshot of the repo as of this point, following:

- Extraction of `workpaper-kit`, the shared workpaper/proof-packet engine behind
  `get-yearly-fx-rate` and `get-year-end-fx-rate` (vendored, auto-synced
  `_workpaper.py` copies in each skill; both migrated with byte-identical /
  byte-compatible output versus their pre-extraction baselines).
- A refined, unified PDF design for both FX skills' proof packets.
- Trust-boundary hardening of the shared kit (path-safe folder naming,
  non-finite-rate rejection, non-file-proof handling, fixed-precision
  reciprocals, `extra_json` reserved-key guards) plus a non-latin1 PDF
  transliteration fallback, so source text outside latin-1 degrades legibly
  instead of dropping to `?`.
- `get-yearly-fx-rate` and `get-year-end-fx-rate` both released at 1.3.0.
- Hosted/remote-session file-delivery guidance added to every artifact-producing
  skill (`get-yearly-fx-rate`, `get-year-end-fx-rate`, `fbar-threshold-check`,
  `statements-to-interest`, `statement-intake-preflight`), so an agent running in
  a sandboxed session actively pushes generated files to the user instead of
  relying solely on local path links, which are not downloadable outside a local
  desktop session.

## Versioning Policy

Bump the repo version and add an entry here when a change:

- adds, removes, or substantially reworks a skill package;
- changes shared cross-cutting infrastructure (CI, git hooks, `workpaper-kit`,
  `privacy-gate` rules, repo-wide conventions) in a way that affects more than
  one package;
- is a milestone worth remembering independent of any single skill's own
  release.

Do not bump for a single skill's routine release (already tracked by its own
`plugin.json` version and `<skill-folder>-vX.Y.Z` tag), a doc/typo fix scoped to
one package, or a CI tweak with no behavior change.
