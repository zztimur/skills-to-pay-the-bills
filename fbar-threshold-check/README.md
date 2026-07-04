# FBAR Threshold Check

This skill exists for one specific question:

```text
Did my foreign accounts cross the FBAR threshold, and can we show the work?
```

The point is not to file FinCEN Form 114 or give legal advice. The point is to turn statement PDFs and confirmed account ledgers into a support packet that separates what the records show from what still needs review.

## The Rule

One calendar year. One account at a time. Confirm the ledger before aggregation.

For each account, preflight the machine-readable statement PDFs with `statement-intake-preflight`, extract daily balances with the reviewed preflight JSON, review the generated JSON/CSV, and confirm the account only after the user has looked at the coverage and balance assumptions. For non-USD accounts, use a retained FX workpaper from `get-year-end-fx-rate` whenever possible. A plain yearly-average rate is not enough for FBAR-style conversion unless the dependency explicitly marks it as FBAR-compatible or year-end appropriate.

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

Codex should read the root `SKILL.md`, then `references/workflow.md`, and use the script for extraction, confirmation, and aggregation. It should pause for ledger review when the script reports coverage gaps, mixed accounts, mixed years, ambiguous currencies, scanned PDFs, or low-confidence balance rows.

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

The skill should stop or ask for review when:

- PDFs are scanned/image-only or missing machine-readable text.
- A statement appears to contain mixed accounts, mixed years, or ambiguous currencies.
- Amount separators or `$` symbols are ambiguous.
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
