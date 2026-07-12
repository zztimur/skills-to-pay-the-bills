# FBAR Threshold Check

This skill exists for one specific question:

```text
Did my foreign accounts cross the FBAR threshold, and can we show the work?
```

The point is not to file FinCEN Form 114 or give legal advice. The point is to turn statement PDFs and confirmed account ledgers into a support packet that separates what the records show from what still needs review.

## The Rule

One calendar year. One account at a time. Confirm the ledger before aggregation.

For each account, preflight the machine-readable statement PDFs with `statement-intake-preflight`. A clean preflight can go straight to extraction; a review-required one needs a separate reviewed-handoff after the user accepts every non-structural gate. Then extract daily balances, review the generated JSON/CSV, and confirm the account only after the user has looked at the coverage and balance assumptions. For non-USD accounts, use a retained FX workpaper from `get-year-end-fx-rate`.

Required companion skill: `statement-intake-preflight`. This skill assumes the intake JSON/CSV has already been reviewed before FBAR balance extraction starts.

FX workpaper dependency for non-USD accounts: `get-year-end-fx-rate`. The normal yearly-average output from `get-yearly-fx-rate` belongs to tax-support workflows, not FBAR conversion.

Do not source, calculate, or override FX rates inside this skill. Do not silently carry balances across suspicious coverage gaps. Do not return a confident "no" when the reviewed records are incomplete.

## What It Produces

The final aggregate command writes:

- a JSON decision file;
- a CSV for day-by-day review;
- a concise PDF summary for human review.

The result reports two views separately:

- daily threshold: whether combined USD account values exceeded `$10,000` on any day;
- FinCEN maximum-value view: whether aggregate converted account maximums exceeded `$10,000`.

Those are related but not identical. The skill keeps them separate so the support packet does not blur a daily reconstruction into a maximum-value filing concept.

## Use It In Codex

Ask for the skill directly:

```text
Use $fbar-threshold-check to check whether my foreign accounts crossed the FBAR threshold for 2025.
```

Codex should read the root `SKILL.md`, then `references/workflow.md`, run `statement-intake-preflight` for shared intake, and use this script for extraction, confirmation, and aggregation. It must stop on preflight review gates, create a reviewed handoff only after explicit user confirmation, and pause again for FBAR ledger review when the script reports coverage gaps, ambiguous balance amounts, conflicting balance candidates, or low-confidence balance rows.

## Use It In Claude Code

The same package carries a Claude plugin command:

```text
/fbar-threshold-check:fbar-threshold-check
```

The command file is only an adapter. The root `SKILL.md`, `references/`, and script are the source of truth.

## Run The Script Manually

Preflight one account:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --out work/statement-preflight.json
```

Extract one account:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

If preflight reports review gates, do not run extraction yet. After the user reviews every non-structural gate, create and use a separate handoff instead:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py review-handoff \
  --input work/statement-preflight.json \
  --accept-gate ambiguous-dollar \
  --user-review-confirmed \
  --out work/statement-preflight-reviewed.json
```

Repeat `--accept-gate` for every listed gate, then replace the extraction command’s `--preflight-json` value with `work/statement-preflight-reviewed.json`.

Confirm the reviewed account:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --out work/account-1-confirmed.json
```

Confirm a non-USD account with retained year-end FX proof:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --fx-workpaper-json work/fbar-fx-rate-proof/cop-2025-source/workpaper.json \
  --out work/account-1-confirmed.json
```

Aggregate all confirmed accounts:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py aggregate \
  --account-ledger work/account-1-confirmed.json work/account-2-confirmed.json \
  --out outputs/fbar-2025-summary.json
```

## Failure Modes

The shared preflight skill owns unreadable PDFs, mixed accounts, mixed years, and ambiguous currency context. After that handoff, this skill should stop or ask for review when:

- Amount separators in balance rows are ambiguous.
- Opening coverage is missing or carry-forward gaps are too large.
- Balance rows are low confidence.
- A non-USD account lacks acceptable year-end FX proof.
- The request turns into legal advice, filing advice, or form preparation.

This is useful friction. An incomplete support packet should say it is incomplete.

## Maintenance

After script changes:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py self-test
python3 -S skill-forge/scripts/inspect_skill_package.py fbar-threshold-check --json --strict
```

If Claude Code is available locally:

```bash
claude plugin validate --strict fbar-threshold-check
```

`privacy-gate` and `skill-forge` are the repo gatekeepers. If either complains, fix the package before shipping.
