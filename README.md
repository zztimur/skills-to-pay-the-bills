# FBAR Proof Kit by Skills To Pay The Bills

[![Skill CI](https://github.com/zztimur/skills-to-pay-the-bills/actions/workflows/skill-ci.yml/badge.svg)](https://github.com/zztimur/skills-to-pay-the-bills/actions/workflows/skill-ci.yml)
[![Install with skills.sh](https://img.shields.io/badge/install-skills.sh-111111)](#install-the-kit)
[![GitHub release](https://img.shields.io/github/v/release/zztimur/skills-to-pay-the-bills)](https://github.com/zztimur/skills-to-pay-the-bills/releases/latest)
[![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> Turn machine-readable foreign-bank statements into a local, source-linked,
> review-ready FBAR threshold support packet.

**[Run the demo](#run-the-demo)** · **[Install the kit](#install-the-kit)** ·
**[See the sample packet](docs/assets/demo/fbar-proof-summary.pdf)**

![A 36-second terminal recording of the synthetic happy path and missing-quarter refusal path](docs/assets/demo/fbar-proof-demo-terminal.gif)

The kit chains three focused Agent Skills: statement intake preflight, retained
year-end Treasury FX proof, and an FBAR threshold check. Statement parsing and
packet generation stay on your machine. The included demo uses only invented
statements and a frozen public Treasury response, so you can inspect the whole
workflow without sharing data or making a network request.

This is support software, not tax or legal advice. It does not prepare or file
FinCEN Form 114 and does not decide every filing obligation. See
[DISCLAIMER.md](DISCLAIMER.md).

## See the proof, including the refusal

| Review artifact | What it establishes |
| --- | --- |
| <img src="docs/assets/demo/fbar-proof-summary-page-1.png" alt="Synthetic FBAR threshold summary showing no threshold crossing" width="430"> | A one-page JSON/CSV/PDF-backed threshold summary from a reviewed 365-day ledger. |
| <img src="docs/assets/demo/fbar-fx-proof-page-1.png" alt="Retained Treasury year-end COP to USD rate workpaper" width="430"> | The exact year-end rate, source URL, date, reciprocal, retained response, and provenance. |

The happy path processes four fictional quarterly COP statements and retains a
reviewed packet. The refusal path omits Q2 and proves the workflow returns
`insufficient-records` / `not-determinable` and refuses ledger confirmation. It
does not manufacture a confident annual answer from incomplete evidence.

## Install the kit

### Universal Agent Skills CLI

```bash
npx skills add zztimur/skills-to-pay-the-bills \
  --skill statement-intake-preflight \
  --skill get-year-end-fx-rate \
  --skill fbar-threshold-check
```

The [`skills` CLI](https://www.skills.sh/docs/cli) discovers all six public root
skills in this repository. By default, that third-party CLI sends anonymous
install telemetry used by the skills.sh leaderboard. This project receives no
statement data or first-party telemetry. Opt out before installing with:

```bash
DISABLE_TELEMETRY=1 npx skills add zztimur/skills-to-pay-the-bills \
  --skill statement-intake-preflight \
  --skill get-year-end-fx-rate \
  --skill fbar-threshold-check
```

For a telemetry-free, inspect-before-install path, download the versioned skill
or `fbar-proof-kit` ZIP and `SHA256SUMS` from the
[latest GitHub Release](https://github.com/zztimur/skills-to-pay-the-bills/releases/latest).

### Native Codex plugin

```bash
codex plugin marketplace add zztimur/skills-to-pay-the-bills
codex plugin add fbar-proof-kit@skills-to-pay-the-bills
```

### Native Claude Code plugin

```text
/plugin marketplace add zztimur/skills-to-pay-the-bills
/plugin install fbar-proof-kit@skills-to-pay-the-bills
```

The native `fbar-proof-kit` bundle contains exactly the same three canonical
skills named above. The top-level skill folders remain the source of truth; a
deterministic sync check prevents the bundled copies from drifting.

## Run the demo

From a clone of this repository:

```bash
git submodule update --init --recursive
python3 -m pip install pdfplumber reportlab pillow
examples/fbar-proof-demo/run_demo.py
```

The run produces:

- four conspicuously synthetic, machine-readable quarterly statement PDFs;
- preflight JSON/CSV and a source-bound reviewed handoff;
- a 365-row account ledger plus explicit review record;
- a frozen Treasury/Fiscal Data response and year-end FX workpaper;
- final threshold JSON, CSV, and PDF;
- a missing-quarter refusal record, transcript, and SHA-256 manifest.

Read the [demo guide](examples/fbar-proof-demo/README.md), inspect the
[retained artifacts](examples/fbar-proof-demo/artifacts/), or run the CI-safe
scratch check:

```bash
examples/fbar-proof-demo/run_demo.py --check
```

## How the proof workflow works

1. `statement-intake-preflight` checks text layers, year and statement-period
   coverage, currency, account grouping, institution evidence, and review gates.
2. A human reviews the flagged assumptions and creates a source-bound handoff.
3. `fbar-threshold-check` extracts and reviews daily account ledgers; incomplete
   coverage remains insufficient rather than silently carried through.
4. `get-year-end-fx-rate` retains the applicable public Treasury/Fiscal Data
   response, rate interpretation, reciprocal, source hash, and PDF workpaper.
5. The reviewed ledgers and retained FX proof produce JSON, CSV, and PDF support
   artifacts for both daily aggregate and FinCEN maximum-value views.

No parser JSON contract or tax-calculation behavior is changed by the packaging,
demo, or public documentation in this release.

## Data boundary

- Statement parsing, review files, ledgers, and summary generation run locally.
- A live year-end FX lookup contacts only public government sources. The
  committed demo uses a frozen response and makes no network request.
- The project has no hosted uploader and collects no first-party telemetry.
- The optional `skills` installer has its own disclosed anonymous install
  telemetry; `DISABLE_TELEMETRY=1` or release ZIPs avoid it.
- Never attach, email, DM, or paste raw bank statements into an issue,
  Discussion, support request, demo, or contribution. Use only synthetic data.

See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) for the complete
reporting boundary.

## What is included

This repository includes six public, installable root skills:

| Skill | Purpose |
| --- | --- |
| [`statement-intake-preflight`](statement-intake-preflight/) | Gate machine-readable statement sets before downstream extraction. |
| [`get-year-end-fx-rate`](get-year-end-fx-rate/) | Retain a source-linked year-end USD FX workpaper for FBAR-style conversion. |
| [`fbar-threshold-check`](fbar-threshold-check/) | Build reviewed account ledgers and threshold support artifacts. |
| [`get-yearly-fx-rate`](get-yearly-fx-rate/) | Retain a published yearly-average FX workpaper for income-tax support. |
| [`statements-to-interest`](statements-to-interest/) | Extract reviewed foreign-bank interest support after preflight. |
| [`privacy-gate`](privacy-gate/) | Block secrets, private data, and unsafe exports before publishing. |

[`skill-forge`](https://github.com/zztimur/skill-forge) is a separate companion
project linked here as a submodule. It audits and pressure-tests Agent Skills;
it is not one of the six root skills and is not inside the FBAR Proof Kit bundle.

[`workpaper-kit`](workpaper-kit/) is internal shared rendering code, not an
invokable skill. Its vendored copies are synchronized and hash-checked so every
standalone package remains portable.

## Scope guardrails

The project does not provide a hosted statement uploader, OCR service, Form 114
filing flow, delinquent-filing guidance, or Form 8938 determination. It never
claims IRS approval, guaranteed compliance, penalty avoidance, or an
"audit-proof" result. Public examples use fictional institutions and synthetic
identifiers only.

Official filing scope, thresholds, and deadlines can change. Check current
[IRS FBAR guidance](https://www.irs.gov/businesses/small-businesses-self-employed/report-of-foreign-bank-and-financial-accounts-fbar)
and obtain qualified advice for your facts.

## Roadmap and contributing

Near-term work stays focused on:

- making installation and the fictional demo easier to complete unaided;
- documenting compatibility across supported agent hosts and Python 3.11–3.13;
- adding wholly synthetic statement layouts when they expose a reproducible gap;
- incorporating practitioner review without collecting private financial records.

Contributions must use synthetic fixtures. See [CONTRIBUTING.md](CONTRIBUTING.md)
before opening an issue or pull request, and never submit raw statements.
Security problems belong in the private process described in
[SECURITY.md](SECURITY.md).

## Maintainer release gate

CI tests Python 3.11, 3.12, and 3.13. It runs the official Agent Skills
reference validator, strict Skill Forge package inspection, plugin-bundle drift
checks, privacy scans, deterministic unit/integration suites, happy/refusal demo
assertions, and PDF generation tests.

Install the release-gate dependencies in an isolated Python environment:

```bash
python3 -m pip install pdfplumber reportlab pypdf pillow \
  'git+https://github.com/agentskills/agentskills.git@38a2ff82958afee88dadf4831509e6f7e9d8ef4e#subdirectory=skills-ref'
```

The repository has one release stream. `VERSION` and [CHANGELOG.md](CHANGELOG.md)
define each `vX.Y.Z` release. Tags publish deterministic ZIPs for every public
skill and the native plugin plus `SHA256SUMS`. Before a release:

```bash
scripts/release-diff.sh
scripts/release-repo.sh --dry-run minor
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for dependency setup and exact validation
commands.

## License

MIT. See [LICENSE](LICENSE).
