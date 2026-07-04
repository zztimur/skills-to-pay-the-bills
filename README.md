# Skills To Pay The Bills

[![Skill CI](https://github.com/zztimur/skills-to-pay-the-bills/actions/workflows/skill-ci.yml/badge.svg)](https://github.com/zztimur/skills-to-pay-the-bills/actions/workflows/skill-ci.yml)
[![License: MIT](https://img.shields.io/github/license/zztimur/skills-to-pay-the-bills)](LICENSE)
[![Python 3.x](https://img.shields.io/badge/python-3.x-3776AB)](https://www.python.org/)
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
| [`get-year-end-fx-rate/`](get-year-end-fx-rate/) | You need a year-end or `YYYY-12-31` FX rate for FBAR-style conversion proof. | Treasury/Fiscal Data or verified manual source proof, `workpaper.json`, Markdown, and PDF. |
| [`fbar-threshold-check/`](fbar-threshold-check/) | You need to check whether foreign accounts crossed the FBAR threshold for a calendar year. | Account ledgers, daily aggregate threshold view, FinCEN maximum-value view, CSV, JSON, and PDF summary. |
| [`statements-to-interest/`](statements-to-interest/) | You need to extract interest income from one institution's text PDF statements for one tax year. | IRS-oriented interest support packet with JSON/CSV review artifacts and FX confirmation gates. |

## Which Skill Do I Need?

| If you are asking... | Start with |
| --- | --- |
| "Am I about to commit something private?" | `privacy-gate` |
| "Is this skill package actually ready to ship?" | `skill-forge` |
| "What yearly average FX rate did we use, and can we prove it?" | `get-yearly-fx-rate` |
| "What year-end FBAR conversion rate did we use?" | `get-year-end-fx-rate` |
| "Did my foreign accounts exceed the FBAR threshold?" | `fbar-threshold-check` |
| "How much interest income is in these statement PDFs?" | `statements-to-interest` |

## What This Is Not

This repo does not prepare tax forms, file FinCEN Form 114, give legal advice, or turn a source into an official IRS, FinCEN, or Treasury blessing. The goal is support documentation: clear workflow, retained proof, reviewer-friendly artifacts, and honest caveats.

## Clone Setup

This collection links `skill-forge` as a git submodule. After cloning, initialize linked skills with:

```bash
git submodule update --init --recursive
```

## Using A Skill

Each top-level folder is its own skill package. Install or copy the package you need into your agent's skill location, then invoke it by name.

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

Use `skill-forge` before shipping any skill change. Validate the changed package, not the repository root:

```bash
python3 -S skill-forge/scripts/inspect_skill_package.py <skill-folder> --json --strict
```

Run the package self-test when one exists:

```bash
python3 <skill-folder>/scripts/<script-name>.py self-test
```

If the package has a Claude plugin manifest and Claude Code is available locally, also run:

```bash
claude plugin validate --strict <skill-folder>
```

## CI

The root GitHub Actions workflow runs the checks that are stable on a clean runner:

- strict `skill-forge` inspection for every skill package;
- deterministic self-tests for the scripts that carry behavior;
- no live IRS/Treasury lookups and no local-only Claude validator assumptions.

Live source checks still belong in release review when the task needs them. A green badge should mean "the package still holds together," not "the internet behaved today."

## License

MIT. See [`LICENSE`](LICENSE).
