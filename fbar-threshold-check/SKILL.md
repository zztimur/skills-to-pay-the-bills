---
name: fbar-threshold-check
description: Check the $10,000 FBAR threshold from foreign account statements. Use for reviewed preflight handoffs, daily ledgers, multi-account aggregation, and year-end FX conversion.
---

# FBAR Threshold Check

Build account-by-account FBAR threshold evidence for one calendar year. Produce support artifacts and a yes/no threshold result; do not prepare FinCEN Form 114, file anything, or provide legal/tax advice.

## Required Workflow

Read `references/workflow.md` before analysis. It contains the preflight handoff, FBAR ledger review points, command sequence, FX dependency contract, and final-response shape. Read `references/fbar-source-notes.md` when explaining FBAR threshold wording, maximum account value, source limitations, or FX caveats. `<package-root>` below means the directory containing this SKILL.md.

## Skill Dependencies

Required companion skill: `statement-intake-preflight`. Use it first for shared PDF intake with `--scope one-account --require-institution`; `extract-account` requires either its clean preflight JSON or a separate reviewed-handoff JSON created after explicit user review, and verifies the ordered PDF byte-size and SHA-256 fingerprints before and after parsing. This skill keeps only FBAR balance, FX, confirmation, and aggregation logic here.

FX workpaper dependency for non-USD accounts: `get-year-end-fx-rate`. Use its retained year-end `workpaper.json` for FBAR-style conversion; do not use yearly-average workpapers from `get-yearly-fx-rate`.

Process one account at a time. First use the separate `statement-intake-preflight` skill with `--scope one-account --require-institution` and review its JSON/CSV:

```bash
python3 "<preflight-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --require-institution \
  --out work/statement-preflight.json
```

If the preflight status is `ready-for-domain-extraction`, extract the account ledger with that JSON. If it is `review-required`, stop for user review. Structural `stop` gates require corrected PDFs; otherwise create a reviewed handoff that acknowledges every review gate after the user confirms it. The preflight workflow asks only when its evidence is weak: it does not ask about a corroborated currency, a clearly contextual prior-year date with clear coverage (including an exact prior December 31 opening boundary), or an extracted account identifier. When the handoff has an ambiguous/unknown currency, unknown account, or opt-in issuer gate, it must contain the preflight skill's structured user resolution; extraction uses confirmed currency and issuer values rather than inferring them from weak text. Do not ask for a full account number by default or repeat a question already resolved in the handoff.

```bash
python3 "<preflight-root>/scripts/statement_intake_preflight.py" review-handoff \
  --input work/statement-preflight.json \
  --accept-gate ambiguous-dollar \
  --user-review-confirmed \
  --out work/statement-preflight-reviewed.json
```

Then pass the ready preflight or reviewed handoff into extraction:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

For a reviewed handoff, replace `work/statement-preflight.json` with `work/statement-preflight-reviewed.json`.

Review the account JSON and CSV before confirming. The CSV is a row-review artifact with one row per calendar day. Each row has `evidence_class`: `transaction`, `period-end-summary`, `carried-forward`, or `missing-opening`; do not treat either a period-end summary or a carried value as daily proof or an annual maximum. The extracted `preflight` object retains reviewed resolution metadata, coverage hints, and ordered fingerprint evidence. `data_sufficiency` classifies the evidence profile and makes limited records explicit: `daily_threshold.answer: insufficient-records` and `maximum_account_value.answer: not-determinable` mean the extraction cannot support a threshold or annual-maximum conclusion. It never replaces confirmation. `review_summary.same_day_balance_candidates` is a compact card for materially different same-day candidates; it records each flagged date, candidate count/range, selected value, and source references, while the CSV mirrors the card in `review_flags`. Shared PDF/account/year/currency intake gates belong to `statement-intake-preflight`; this skill's review starts after that handoff and focuses on balance evidence. Stop for user review when the FBAR extractor reports ambiguous amount separators, missing opening coverage, incomplete daily balance coverage, carry-forward gaps, materially different same-day balance candidates, low-confidence balance rows, or a preflight possible-missing-statement-period warning. `confirm-account` refuses ledgers with carry-forward gaps longer than 40 days unless the user has explicitly reviewed `coverage.carry_gaps` and you pass `--accept-carry-forward`.

When a labelled closing amount appears on a page with exactly one re-verified statement period, the extractor may record it as a `period-end-summary` observation. Its JSON/CSV flag retains both the summary and exact period-end source references. It never fills intervening days, and it does not remove carry-gap review or confirmation gates.

For a user-confirmed COP account with one re-verified page period, either the exact text header `Fecha Descripción Movimiento Tarjeta Débito Abono Saldo` or the compact PDF-column header `Fecha | Descripción | Saldo` permits whole-COP comma grouping only in the final `Saldo` column. The compact form also requires one left-column `DD/MM` date and exactly one monetary `Saldo` cell on the same visual row. This does not relax generic separator handling: a standalone `12,000` remains review-required.

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

Important dependency guardrail: this skill consumes only `workpaper.json` files from `get-year-end-fx-rate` for non-USD accounts. Do not source, calculate, or override FX rates inside this skill. Do not use yearly-average workpapers from `get-yearly-fx-rate` for FBAR conversion.

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

If it reports `pdfplumber missing` in Codex Desktop, call `load_workspace_dependencies` and rerun with the bundled Python executable. Otherwise, explain that extraction needs a Python runtime with `pdfplumber` and give one next step; `confirm-account`, `aggregate`, and `self-test` can still run without it.

`confirm-account`, `aggregate`, and `self-test` use only Python standard-library modules.

## Maintainer Checks

After changing this skill (not needed for normal use), run `self-test` plus the repo gatekeeper:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" self-test
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
claude plugin validate --strict "<package-root>"  # when Claude tooling is available
```

For changes to the `statement-intake-preflight` handoff, run
`tests/run_preflight_integration.py` from a checkout where the required sibling
skill is available; it uses real PDFs and skips cleanly without test-only
`reportlab` and `pdfplumber`.

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/fbar-threshold-check.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
