# Changelog

All notable published changes are documented here. This is the release history
for the repository as a collection: a release can contain one skill update,
shared-infrastructure work, or a wider milestone. Individual package manifests
may retain their own version metadata, but they do not have separate tags,
changelogs, or GitHub Releases.

The current repository version lives in [`VERSION`](VERSION), is tagged
`vX.Y.Z`, and has one matching GitHub Release.

## [1.4.2] - 2026-07-16

### Changed

- `repository`: chore: update skill-forge submodule
- `repository`: fix(openai-metadata): use supported dependency lists
- `repository`: chore: update skill-forge submodule to v0.4.0
- `repository`: chore: bump skill-forge submodule to v0.5.1
- `scripts`: chore: keep release checks free of Python bytecode caches
- `repository`: fix: keep proof-packet headings with their first row

## [1.4.1] - 2026-07-14

### Changed

- `repository`: docs: refresh root README
- `fbar-threshold-check`: docs: update fbar-threshold-check README
- `get-yearly-fx-rate`: docs: update get-yearly-fx-rate README
- `privacy-gate`: docs: update privacy-gate README
- `statement-intake-preflight`: docs: update statement-intake-preflight README
- `statements-to-interest`: docs: update statements-to-interest README
- `workpaper-kit`: docs: update workpaper-kit README
- `repository`: chore: bump skill-forge submodule pointer
- `fbar-threshold-check`: fix(fbar): reject colliding artifact paths
- `fbar-threshold-check`: fix(fbar): verify FX packet integrity
- `statements-to-interest`: fix(interest): protect output artifacts and FX values
- `statements-to-interest`: test(interest): gate final PDF integration
- `repository`: ci: gate releases on PDF workflows
- `get-yearly-fx-rate`: fix(yearly-fx): reject Treasury reporting rates
- `get-yearly-fx-rate`: fix(yearly-fx): reject incomplete calendar years

## [1.4.0] - 2026-07-14

### Changed

- `statement-intake-preflight`: test(statement-intake-preflight): add period resolution baselines
- `statement-intake-preflight`: feat(statement-intake-preflight): capture period evidence
- `statement-intake-preflight`: feat(statement-intake-preflight): resolve source-bound period years
- `statement-intake-preflight`: feat(statement-intake-preflight): confirm unresolved period years
- `statement-intake-preflight`: feat(statement-intake-preflight): confirm account opening dates
- `statement-intake-preflight`: test(statement-intake-preflight): preserve generated date protections
- `repository`: feat(statement-intake-preflight): validate reviewed period contracts
- `statement-intake-preflight`: chore(statement-intake-preflight): bump to 1.4.6
- `fbar-threshold-check`: fix(fbar-threshold-check): preserve compact period evidence and bump to 1.8.7
- `statement-intake-preflight`: chore(statement-intake-preflight): align package version metadata
- `statements-to-interest`: Bump statements-to-interest to v1.3.4
- `fbar-threshold-check`: fix(fbar-threshold-check): sync manifest version to 1.8.7
- `repository`: docs: document reviewed statement handoffs

## [1.3.6] - 2026-07-13

### Changed

- `statement-intake-preflight`: classify source-labelled generated-on dates as document metadata, require source-bound review for an out-of-period date, and preserve them outside statement-period coverage.
- `fbar-threshold-check`: validate exact generated-on reviewed resolutions before account extraction.
- `statements-to-interest`: validate the same generated-on reviewed-resolution contract and reject missing or tampered date anchors.
- `repository`: add real-PDF regression coverage and align reviewed-handoff workflow documentation and package metadata.

## [1.3.5] - 2026-07-13

### Changed

- `statement-intake-preflight`: feat(statement-intake-preflight): add reviewed handoffs
- `fbar-threshold-check`: feat(fbar-threshold-check): enforce reviewed preflight handoffs
- `statement-intake-preflight`: feat(statement-intake-preflight): bind statement files
- `fbar-threshold-check`: feat(fbar-threshold-check): verify preflight file identity
- `fbar-threshold-check`: test(fbar-threshold-check): add preflight integration coverage
- `statement-intake-preflight`: docs(statement-intake-preflight): clarify handoffs
- `fbar-threshold-check`: docs(fbar-threshold-check): clarify preflight handoffs
- `statements-to-interest`: feat(statements-to-interest): verify preflight statement files
- `statements-to-interest`: feat(statements-to-interest): add preflight identity integration tests
- `statement-intake-preflight`: feat(statement-intake-preflight): add source-aware period coverage
- `repository`: feat(statement-intake-preflight): record reviewed input resolutions
- `fbar-threshold-check`: feat(fbar-threshold-check): consume reviewed resolutions
- `repository`: docs(statement-workflows): clarify conditional review prompts
- `fbar-threshold-check`: feat(fbar-threshold-check): improve review UX
- `repository`: Merge pull request #16 from zztimur/codex/fbar-ux-polish
- `statement-intake-preflight`: Fix statement preflight boundary and institution handoffs
- `fbar-threshold-check`: Support preflight handoff schema 1.1
- `statements-to-interest`: Accept verified reviewed interest handoffs
- `statement-intake-preflight`: chore(statement-intake-preflight): bump to 1.3.6
- `fbar-threshold-check`: chore(fbar-threshold-check): bump to 1.5.6
- `statements-to-interest`: chore(statements-to-interest): bump to 1.3.1
- `statements-to-interest`: fix(statements-to-interest): validate reviewed year handoffs
- `statements-to-interest`: fix(statements-to-interest): harden FX confirmation checks
- `statements-to-interest`: docs(statements-to-interest): clarify dependency lookup
- `fbar-threshold-check`: Fix source-bound short statement dates
- `statement-intake-preflight`: statement-intake-preflight: accept Spanish account header
- `fbar-threshold-check`: fbar-threshold-check: trust validated account hints
- `statement-intake-preflight`: statement-intake-preflight: require institution confirmation
- `fbar-threshold-check`: fbar-threshold-check: use reviewed institution handoff
- `fbar-threshold-check`: fbar-threshold-check: add source-bound statement fixture
- `statement-intake-preflight`: statement-intake-preflight: require institution confirmation
- `fbar-threshold-check`: Clarify FBAR source notes
- `statement-intake-preflight`: Release statement-intake-preflight 1.3.8
- `fbar-threshold-check`: fbar-threshold-check: add same-day review card
- `statement-intake-preflight`: statement-intake-preflight: tighten issuer evidence rules
- `statement-intake-preflight`: statement-intake-preflight: parse split Spanish periods
- `fbar-threshold-check`: fbar-threshold-check: validate split period refs
- `fbar-threshold-check`: fbar-threshold-check: add source-bound period-end summaries
- `fbar-threshold-check`: fbar-threshold-check: harden COP table parsing
- `repository`: chore: bump skill docs and metadata
- `repository`: chore: scrub bank-name mentions
- `statement-intake-preflight`: statement-intake-preflight: avoid boilerplate issuer detection
- `fbar-threshold-check`: Add fractional balance tokenization regression
- `fbar-threshold-check`: Handle unpadded decimal balance tokens
- `fbar-threshold-check`: Harden FBAR compact COP extraction
- `fbar-threshold-check`: Harden FBAR threshold review outputs
- `fbar-threshold-check`: Add period-end-only FBAR regression
- `fbar-threshold-check`: Release fbar-threshold-check 1.8.4

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
