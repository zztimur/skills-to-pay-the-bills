# FBAR Threshold Check

This skill exists for one specific question:

```text
Did my foreign accounts cross the FBAR threshold, and can we show the work?
```

The point is not to file FinCEN Form 114 or give legal advice. The point is to turn statement PDFs and confirmed account ledgers into a support packet that separates what the records show from what still needs review.

Current package version: 1.8.7.

## The Rule

One calendar year. One account at a time. Confirm the ledger before aggregation.

For each account, preflight the machine-readable statement PDFs with `statement-intake-preflight --scope one-account --require-institution`. A clean preflight can go straight to extraction; a review-required one needs a separate reviewed-handoff after the user accepts every non-structural gate. The preflight does not pester users about corroborated currency, clearly contextual prior-year dates with clear coverage (including an exact prior December 31 opening boundary), an account it can source-link across every PDF, or an issuer it already corroborated. For weak currency evidence it asks for an ISO code; where account identity cannot be extracted or cannot link every supplied PDF, it asks only for confirmation that the PDFs are one account—not for the account number; and where issuer evidence is absent or conflicts, it asks for the institution name. Ambiguous/unknown currency, account, year, and required-institution gates use structured, source-bound user resolutions. Extraction uses a reviewed ISO currency rather than guessing from a bare `$`, a reviewed institution rather than a conflicting CLI label, and only the same PDFs in the same order, with the byte size and SHA-256 fingerprints preflight recorded. It verifies those fingerprints before parsing and again after every parser pass, rejecting a changed file before it can write ledger artifacts. A short day/month balance-table date is accepted only when the same page carries one verified statement period that resolves it; the generated ledger marks that row medium confidence for review. Then extract daily balances, review the generated JSON/CSV, and confirm the account only after the user has looked at the coverage and balance assumptions. For non-USD accounts, use a retained FX workpaper from `get-year-end-fx-rate`.

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

The extraction JSON also carries `review_summary.same_day_balance_candidates` and a `data_sufficiency` card. The card distinguishes `transaction-rows`, `period-end-only`, mixed evidence, and no observations; it reports `daily_threshold: insufficient-records` and `maximum_account_value: not-determinable` when missing opening coverage or long carry-forward gaps prevent a reliable answer. It does not replace account confirmation. Every JSON/CSV ledger row has an `evidence_class`: `transaction`, `period-end-summary`, `carried-forward`, or `missing-opening`. When a labelled closing summary is source-bound to a verified statement period, it records that separately in `review_summary.period_end_summaries`, including the summary and exact period-end references. A period-end summary is an exact-date observation only: it never fills intervening days or proves the annual maximum. For a user-confirmed COP account, a re-verified page period plus either the exact transaction-table header or the compact `Fecha | Descripción | Saldo` PDF-column layout can establish that comma groups are whole COP only in the final `Saldo` column; bare grouped amounts elsewhere stay flagged for review. The compact form also needs a left-column `DD/MM` date and exactly one final monetary cell per visual row. The console only reports how many dates need review; inspect the retained artifacts for the actual candidate values and source references.

## Use It In Codex

Ask for the skill directly:

```text
Use $fbar-threshold-check to check my 2025 foreign-account statements against the $10,000 FBAR threshold; start with one account and tell me exactly what you need.
```

Codex should read the root `SKILL.md`, then `references/workflow.md`, run `statement-intake-preflight` for shared intake, and use this script for extraction, confirmation, and aggregation. It must stop on preflight review gates, create a reviewed handoff only after explicit user confirmation, and pause again for FBAR ledger review when the script reports coverage gaps, ambiguous balance amounts, conflicting balance candidates, or low-confidence balance rows. An out-of-period generated-on date remains source-bound document metadata in that handoff; it is never used as statement-period coverage.

Have the PDFs for one account ready. Codex asks only for the calendar year and files that are still missing, then summarizes coverage and any flags in plain language before asking you to confirm a ledger.

## Use It In Claude Code

The same package carries a Claude plugin command:

```text
/fbar-threshold-check:fbar-threshold-check
```

The command file is only an adapter. The root `SKILL.md`, `references/`, and script are the source of truth.

## Run The Script Manually

This workflow requires Python 3.11 or newer with `pdfplumber`. Before balance
extraction, check the parser runtime:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py dependency-check
```

If it reports `pdfplumber missing` in Codex Desktop, use the bundled Python runtime after `load_workspace_dependencies`; otherwise use a Python runtime that includes `pdfplumber`.

Preflight one account:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --require-institution \
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
  --confirm-currency COP \
  --user-review-confirmed \
  --out work/statement-preflight-reviewed.json
```

Repeat `--accept-gate` for every listed gate, then add only the typed resolutions that those gates request: `--confirm-period-year PERIOD_ID=2025` once for every unresolved source-labelled period, `--confirm-currency COP` for ambiguous or unknown currency, `--confirm-one-account` when the PDFs cannot be source-linked as one account, `--confirm-institution "Example Bank"` for an allowed opt-in issuer gate, `--confirm-statement-year 2025` with any required `--classify-contextual-year 2024` values for separate year evidence, and `--confirm-generated-on-date YYYY-MM-DD` once for every extracted source-labelled date. An inferred-period-year gate takes only its exact `--accept-gate`; it does not accept a replacement year or interval. `--confirm-account-opened-on YYYY-MM-DD` is allowed only for the one continuous leading-gap case described by preflight; it cannot excuse a missing middle or year-end statement. Then replace the extraction command’s `--preflight-json` value with `work/statement-preflight-reviewed.json`.

Extraction rejects a missing, duplicated, reordered, or modified statement PDF, as well as an incomplete or source-mismatched reviewed resolution. If any PDF changed since preflight, rerun preflight and recreate the reviewed handoff when one is required.

Confirm the reviewed account:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --out work/account-1-confirmed.json
```

If confirmation stops on a carry-forward gap longer than 40 days, review
`coverage.carry_gaps` and ask for the missing statements first. Only after the
user explicitly accepts those carried balances, rerun with
`--accept-carry-forward`. That acceptance does not make the evidence complete:
the final daily-threshold answer remains `insufficient-records`.

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
- A statement PDF differs from the preflight byte-size or SHA-256 fingerprint.
- Opening coverage is missing or carry-forward gaps are too large.
- Preflight identifies possible missing statement periods, including omitted mid-year or year-end coverage.
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

Before shipping a preflight-handoff change, also run the generated-PDF integration
suite from this repository. It exercises the `statement-intake-preflight` and
FBAR CLIs together, including reviewed handoffs, resolution consumption, coverage gaps, and file-identity refusals
(needs `reportlab` + `pdfplumber`; it skips cleanly when they are absent):

```bash
python3 fbar-threshold-check/tests/run_preflight_integration.py
```

If Claude Code is available locally:

```bash
claude plugin validate --strict fbar-threshold-check
```

`privacy-gate` and `skill-forge` are the repo gatekeepers. If either complains, fix the package before shipping.
