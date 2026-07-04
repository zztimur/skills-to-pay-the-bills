---
name: fbar-threshold-check
description: Check whether foreign accounts crossed the $10,000 FBAR (FinCEN Form 114) threshold for a tax year. Use for account statements, daily ledgers, multi-account aggregation, year-end FX conversion.
---

# FBAR Threshold Check

Build account-by-account FBAR threshold evidence for one calendar year. Produce support artifacts and a yes/no threshold result; do not prepare FinCEN Form 114, file anything, or provide legal/tax advice.

## Required Workflow

Read `references/workflow.md` before analysis. It contains the preflight handoff, FBAR ledger review points, command sequence, FX dependency contract, and final-response shape. Read `references/fbar-source-notes.md` when explaining FBAR threshold wording, maximum account value, source limitations, or FX caveats. `<package-root>` below means the directory containing this SKILL.md.

Process one account at a time. First use the separate `statement-intake-preflight` skill with `--scope one-account` and review its JSON/CSV:

```bash
python3 "<preflight-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --out work/statement-preflight.json
```

Then extract the account ledger and pass the reviewed preflight JSON:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

Review the account JSON and CSV before confirming. The CSV is a row-review artifact with one row per calendar day. Shared PDF/account/year/currency intake gates belong to `statement-intake-preflight`; this skill's review starts after that handoff and focuses on balance evidence. Stop for user review when the FBAR extractor reports ambiguous amount separators, missing opening coverage, incomplete daily balance coverage, carry-forward gaps, materially different same-day balance candidates, or low-confidence balance rows. `confirm-account` refuses ledgers with carry-forward gaps longer than 40 days unless the user has explicitly reviewed `coverage.carry_gaps` and you pass `--accept-carry-forward`.

Confirm only after the user has reviewed the account ledger:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --out work/account-1-confirmed.json
```

For non-USD accounts, use the separate `get-year-end-fx-rate` skill (preferred for FBAR-style conversion) to create the required workpaper, then pass its `workpaper.json`:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --fx-workpaper-json work/fx-year-end-proof/cop-2025-source/workpaper.json \
  --out work/account-1-confirmed.json
```

Important dependency guardrail: this skill consumes only `workpaper.json` files from `get-year-end-fx-rate` (preferred) or `get-yearly-fx-rate`. A `get-yearly-fx-rate` workpaper is accepted only when that dependency marks it as FBAR-compatible or year-end appropriate; plain yearly-average workpapers are rejected. Do not source, calculate, or override FX rates inside this skill.

After each confirmed account, ask whether the user has another foreign account for the same year. When the user says there are no more accounts, aggregate:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" aggregate \
  --account-ledger work/account-1-confirmed.json work/account-2-confirmed.json \
  --out outputs/fbar-2025-summary.json
```

The aggregate command writes:

- Final JSON decision file at `--out`.
- Final CSV beside the JSON unless `--csv` is supplied.
- Concise human PDF beside the JSON unless `--pdf` is supplied.

## Result Rules

Report both views separately:

- Daily threshold: whether combined USD account values exceeded `$10,000` on any day.
- FinCEN maximum-value view: whether aggregate converted account maximums exceeded `$10,000`.

If the daily threshold is exceeded, list the dates. If records are incomplete, say the reviewed records are insufficient for a confident daily-threshold answer instead of returning a false `no`.

## Runtime

`extract-account` still needs `pdfplumber` to parse balances from the reviewed PDFs; shared PDF readiness belongs to `statement-intake-preflight`. Check parser availability with:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" dependency-check
```

`confirm-account`, `aggregate`, and `self-test` use only Python standard-library modules.

## Maintainer Checks

After changing this skill (not needed for normal use), run `self-test` plus the repo gatekeeper:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" self-test
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
claude plugin validate --strict "<package-root>"  # when Claude tooling is available
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/fbar-threshold-check.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
