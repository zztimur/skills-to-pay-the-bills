# Changelog

All notable published changes are documented here. This is the release history
for the repository as a collection: a release can contain one skill update,
shared-infrastructure work, or a wider milestone. Individual package manifests
may retain their own version metadata, but they do not have separate tags,
changelogs, or GitHub Releases.

The current repository version lives in [`VERSION`](VERSION), is tagged
`vX.Y.Z`, and has one matching GitHub Release.

## [1.3.4] - 2026-07-12

- Bumped `statements-to-interest` to 1.2.2 and made its README runtime setup
  explicit: use the Python path returned by Codex's
  `load_workspace_dependencies` rather than assuming the system `python` has
  the PDF libraries installed.

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

Bump the root version and add an entry here for every published repository
release, including a release that contains only one skill change. Do not bump
the version for ordinary edits or commits that have not been deliberately
released yet.

Use `scripts/release-repo.sh` to prepare and publish a release. It makes the
root version bump, generates this entry, runs the release checks, creates the
global `vX.Y.Z` tag, and pushes it for the GitHub Release workflow. Skill
manifests are package metadata only; do not create per-skill tags, changelogs,
or GitHub Releases.
