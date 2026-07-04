---
name: fbar-threshold-check
description: Check FBAR thresholds for one tax year from foreign account statements. Use for daily ledgers, multi-account aggregation, and get-yearly-fx-rate workpaper conversion.
---

# FBAR Threshold Check

Build account-by-account FBAR threshold evidence for one calendar year. Produce support artifacts and a yes/no threshold result; do not prepare FinCEN Form 114, file anything, or provide legal/tax advice.

## Required Workflow

Read `references/workflow.md` before analysis. It contains the intake gates, review points, command sequence, FX dependency contract, and final-response shape. Read `references/fbar-source-notes.md` when explaining FBAR threshold wording, maximum account value, source limitations, or FX caveats.

Process one account at a time:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --out work/account-1.json
```

Review the JSON and CSV before confirming. The CSV is a row-review artifact with one row per calendar day. Stop for user review when the script reports mixed accounts, mixed years, mixed currencies, ambiguous `$`, missing opening coverage, scanned/image-only PDFs, incomplete coverage, or low-confidence balance rows.

Confirm only after the user has reviewed the account ledger:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --out work/account-1-confirmed.json
```

For non-USD accounts, use the separate `get-yearly-fx-rate` skill to create the required workpaper, then pass its `workpaper.json`:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --fx-workpaper-json work/fx-rate-proof/cop-2025-source/workpaper.json \
  --out work/account-1-confirmed.json
```

Important dependency guardrail: this skill consumes only `get-yearly-fx-rate` `workpaper.json` files. It rejects ordinary yearly-average workpapers unless that dependency marks the workpaper as FBAR-compatible or year-end appropriate. Do not source, calculate, or override FX rates inside this skill.

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

`extract-account` requires machine-readable PDFs and `pdfplumber`. `confirm-account`, `aggregate`, and `self-test` use only Python standard-library modules.

After changing this skill, run:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" self-test
python3 /Users/timur/.codex/skills/.system/skill-creator/scripts/quick_validate.py "<package-root>"
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
```

If Claude tooling is available, also run:

```bash
claude plugin validate --strict "<package-root>"
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/fbar-threshold-check.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
