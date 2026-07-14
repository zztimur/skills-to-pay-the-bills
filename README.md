# Skills To Pay The Bills

[![Skill CI](https://img.shields.io/badge/Skill%20CI-configured-16a34a)](https://github.com/zztimur/skills-to-pay-the-bills/actions/workflows/skill-ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/)
[![Codex/OpenAI Agent Skills](https://img.shields.io/badge/Codex-Agent%20Skills-111827)](https://github.com/zztimur/skills-to-pay-the-bills)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-compatible-5A3E2B)](https://github.com/zztimur/skills-to-pay-the-bills)

Small, practical agent skills for proof-heavy work where guessing is expensive.

This is a collection of local-first skills for two kinds of work:

- shipping safer agent skills without accidentally bundling private files, broken packages, or vague instructions;
- producing tax-support workpapers where the rate, source, artifact, and caveat need to survive past the chat window.

The pattern is boring on purpose: one clear `SKILL.md`, thin platform adapters, deterministic scripts where determinism matters, and enough proof left behind that Future Me can tell what happened.

## What Is Here

### Ship Safer Skills

| Skill | Use it when | Output |
| --- | --- | --- |
| [`privacy-gate/`](privacy-gate/) | You are about to commit, package, sync, or share repo content and want to catch secrets, private data, generated artifacts, and unsafe binary exports. | Staged-file or path scan with block/warn findings, optional text sanitization, and an installable Git hook. |
| [`skill-forge/`](skill-forge/) | You need to audit, pressure test, validate, or grade an agent skill before installing or publishing it. | Structural inspection, qualitative review workflow, release-gate rubric, and regression-tested inspector. |

### Build Tax-Support Proof Packets

| Skill | Use it when | Output |
| --- | --- | --- |
| [`get-yearly-fx-rate/`](get-yearly-fx-rate/) | You need a published yearly average FX rate for one currency and one year. | Cited rate, reciprocal, saved source proof, `workpaper.json`, Markdown, and PDF. |
| [`get-year-end-fx-rate/`](get-year-end-fx-rate/) | You need a year-end or `YYYY-12-31` FX rate for FBAR-style conversion proof. | Treasury/Fiscal Data first for completed year-ends, then a verified manual fallback: retained source JSON/provenance or an explicit no-proof caveat, plus `workpaper.json`, Markdown, and PDF. |
| [`statement-intake-preflight/`](statement-intake-preflight/) | You need to preflight machine-readable statement PDFs before FBAR or interest extraction. | Shared intake JSON/CSV with text-layer, scope, source-bound period evidence, currency, account, institution, and review-gate checks. |
| [`fbar-threshold-check/`](fbar-threshold-check/) | You need to check whether foreign accounts crossed the FBAR threshold for a calendar year. | Account ledgers, daily aggregate threshold view, FinCEN maximum-value view, CSV, JSON, and PDF summary. |
| [`statements-to-interest/`](statements-to-interest/) | You need to extract interest income from one institution's text PDF statements for one tax year. | IRS-oriented interest support packet with JSON/CSV review artifacts and FX confirmation gates. |

### Shared Internals

The two FX skills share their workpaper/proof-packet engine — folder naming, the `workpaper.md` / `workpaper.json` / `workpaper.pdf` renderers, and the copied-and-hashed source-proof schema — through [`workpaper-kit/`](workpaper-kit/). `statements-to-interest` also uses the kit's optional shared ReportLab packet layout. It is internal plumbing, not a skill: no `SKILL.md`, no agent command or trigger, nothing an agent invokes. Its shell commands are maintainer tools for syncing and testing the kit. Its `.claude-plugin/plugin.json` is package metadata, not a separate release line. You edit one canonical file, `workpaper-kit/workpaper.py`; each participating skill carries a vendored, auto-synced copy (`scripts/_workpaper.py`) so it still installs standalone. Details in [`workpaper-kit/README.md`](workpaper-kit/README.md).

The FX skills deliberately do different jobs. `get-yearly-fx-rate` documents a published yearly average for income-tax support. `get-year-end-fx-rate` documents a `YYYY-12-31` rate for FBAR-style conversion. They share proof mechanics; they do not swap sources or quietly relabel one rate as the other.

## Which Skill Do I Need?

| If you are asking... | Start with |
| --- | --- |
| "Am I about to commit something private?" | `privacy-gate` |
| "Is this skill package actually ready to ship?" | `skill-forge` |
| "What yearly average FX rate did we use, and can we prove it?" | `get-yearly-fx-rate` |
| "What year-end FBAR conversion rate did we use?" | `get-year-end-fx-rate` |
| "Are these statement PDFs ready for FBAR or interest extraction?" | `statement-intake-preflight` |
| "Did my foreign accounts exceed the FBAR threshold?" | `fbar-threshold-check` |
| "How much interest income is in these statement PDFs?" | `statements-to-interest` |

## Statement Workflow

For statement-based work, begin with `statement-intake-preflight`. It records
the statement scope, source-bound period evidence, and any reviewer decisions
in a handoff the downstream skills can verify. A ready preflight can go straight
to the next skill. A review-required preflight needs a separate reviewed
handoff with every non-structural gate accepted and every required typed
resolution recorded. Then choose the outcome you need: pass that ready JSON or
reviewed handoff to `fbar-threshold-check` for FBAR threshold analysis, or to
`statements-to-interest` for interest-income support. Both downstream skills
validate the handoff and the bound statement files before extracting results.

## What This Is Not

This repo does not prepare tax forms, file FinCEN Form 114, give legal advice, or turn a source into an official IRS, FinCEN, or Treasury blessing. The goal is support documentation: clear workflow, retained proof, reviewer-friendly artifacts, and honest caveats.

## Clone Setup

This collection links `skill-forge` as a git submodule. After cloning, initialize linked skills with:

```bash
git submodule update --init --recursive
```

Then enable the repo's tracked Git hooks once per clone (Git will not run a tracked hook otherwise):

```bash
git config core.hooksPath .githooks
```

The pre-commit hook runs the `privacy-gate` scan and auto-syncs the vendored `workpaper-kit` copies (`scripts/_workpaper.py` in each participating skill) from the canonical `workpaper-kit/workpaper.py`, staging them so they ride the same commit. Edit only the canonical source; CI runs `workpaper-kit/sync.sh --check` as a backstop. See [`workpaper-kit/README.md`](workpaper-kit/README.md) for the kit interface and sync model.

## Using A Skill

Each top-level skill folder is its own agent package. Install or copy the package you need into your agent's skill location, then invoke it by name. `workpaper-kit/` is the internal-library exception; use its README only when changing shared workpaper code. `skill-forge/` is the linked-package exception: it is a git submodule with its own upstream release history, so package it from a release archive or a clean initialized submodule rather than copying the submodule's `.git` control file into an install.

Codex/OpenAI-style prompts look like:

```text
Use $get-year-end-fx-rate to find the 2025 year-end exchange rate for COP to USD with proof.
```

Claude Code command adapters look like:

```text
/get-year-end-fx-rate:get-year-end-fx-rate COP 2025
```

The root `SKILL.md`, `references/`, and `scripts/` inside each package are the source of truth. `agents/openai.yaml`, `.claude-plugin/plugin.json`, and `commands/` are discovery or command adapters.

## Gatekeeper Workflow

Run `privacy-gate` before committing or publishing anything from this repo:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --staged --strict
```

To install it as a Git pre-commit hook, run `privacy_gate.py install-hook`. The default hook embeds the installing script's absolute path for solo convenience; committing that to a shared repo discloses a local path and won't resolve on teammates' machines, so use `install-hook --portable` there and pair it with a vendored `privacy-gate/` or the `PRIVACY_GATE_SCRIPT` override.

Use `skill-forge` before shipping any skill change. Validate the changed package, not the repository root:

```bash
python3 -S skill-forge/scripts/inspect_skill_package.py <skill-folder> --json --strict
```

Run the package's own self-test or regression entrypoint when one exists. Most
behavioral skill scripts expose `self-test`; the two repo-tooling packages use
dedicated runners:

```bash
python3 <skill-folder>/scripts/<script-name>.py self-test
python3 privacy-gate/scripts/test_privacy_gate.py
python3 -S skill-forge/scripts/run_self_tests.py
```

If the package has a Claude plugin manifest and Claude Code is available locally, also run:

```bash
claude plugin validate --strict <skill-folder>
```

## Releasing The Repository

This collection repository has one public release stream. A change to one skill becomes a collection release only when you explicitly release the repository; normal edits and commits do not bump [`VERSION`](VERSION), create tags, or publish GitHub Releases.

`VERSION` and [`CHANGELOG.md`](CHANGELOG.md) are the canonical record for every published collection release. Each release gets one `vX.Y.Z` tag and one GitHub Release. The release version is independent of the package metadata in individual `.claude-plugin/plugin.json` files. The linked `skill-forge` submodule is the deliberate exception: it has its own upstream tags and releases; this repository records and releases only the pinned submodule commit.

Review the next global release before publishing:

```bash
scripts/release-diff.sh
scripts/release-repo.sh --dry-run
```

Publish with a patch bump by default, or choose the SemVer scope explicitly:

```bash
scripts/release-repo.sh
scripts/release-repo.sh minor
scripts/release-repo.sh major
```

The command requires a clean `main` branch that is not behind `origin/main`. It generates the root changelog entry from commits since the latest global tag, runs the deterministic release checks, commits the release metadata, creates and pushes `vX.Y.Z`, and then GitHub Actions creates the GitHub Release. The first global release uses the most recent `VERSION` commit as its baseline because this repository has no prior global tag.

## CI

GitHub Actions runs these deterministic core checks on pull requests and `main`; the release workflow runs the same checks against the tagged commit before creating its GitHub Release. Both workflows initialize the linked `skill-forge` submodule first:

- strict `skill-forge` inspection for every skill package;
- a strict `privacy-gate` scan of the repo tree, so a stray secret or private file fails the build the same way the pre-commit hook fails a commit;
- a `workpaper-kit/sync.sh --check` backstop, so a vendored `_workpaper.py` copy that drifted from the canonical source fails the build;
- deterministic self-tests and regression runners for the scripts that carry behavior, including the `workpaper-kit` golden test;
- no live IRS/Treasury lookups and no local-only Claude validator assumptions.

The default CI job does not install `pdfplumber`, `reportlab`, or `pypdf`, so it does not run the generated-PDF smoke, pressure, and cross-skill integration suites. Before shipping parser or statement-handoff changes, run the full local PDF pass with a Python 3.11+ environment that has those dependencies:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py smoke-test
python3 statement-intake-preflight/tests/run_pressure_suite.py
python3 fbar-threshold-check/tests/run_preflight_integration.py
python3 statements-to-interest/scripts/statements_to_interest.py smoke-test
python3 statements-to-interest/tests/run_preflight_integration.py
```

Live source checks still belong in release review when the task needs them. A green badge should mean "the deterministic core still holds together," not "every optional PDF suite ran" or "the internet behaved today."

## License

MIT. See [`LICENSE`](LICENSE).
