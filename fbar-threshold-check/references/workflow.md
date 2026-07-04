# FBAR Threshold Check Workflow

Use this workflow whenever the skill is triggered. Keep all work local unless the user explicitly asks otherwise.

## 1. Intake

Collect or infer:

- Calendar year.
- Statement PDF paths for exactly one account.
- Account currency, if the statement does not clearly identify it.
- Work folder, defaulting to `work/`.
- Output folder, defaulting to `outputs/`.

Before running extraction, verify scope:

- One account per pass.
- One calendar year.
- One currency bucket.
- Machine-readable PDFs, not screenshots or image-only scans.
- The user wants an FBAR threshold check, not an official filing.

If the user provides multiple accounts, split the work. If an account has multiple currencies, split by currency bucket or stop for manual review.

## 2. Source Anchors

Read `references/fbar-source-notes.md` when explaining the threshold, maximum account value, source limitations, or FX dependency behavior.

Use careful language:

- "FBAR threshold support check" rather than "FBAR filing prepared."
- "Daily threshold exceeded/not exceeded in reviewed records" rather than a legal filing opinion.
- "FinCEN maximum-value view" for the aggregate account-maximum calculation.
- "Records insufficient for a confident no" when coverage is incomplete.

## 3. Runtime Setup

For extraction, use a Python runtime with `pdfplumber`:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" dependency-check
```

If `pdfplumber` is unavailable, stop before extraction and report the missing dependency. `confirm-account`, `aggregate`, and `self-test` do not require `pdfplumber`.

## 4. Extract One Account

Run:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --out "work/account-1.json"
```

Optional flags:

- `--account-id`: stable local identifier when the user has one.
- `--institution`: institution label when visible in the statement set or known from the user.
- `--account-currency`: ISO code when the statement text cannot safely infer it.
- `--csv`: review CSV output path.

The script writes:

- Account JSON at `--out`.
- Review CSV beside the JSON unless `--csv` is supplied.

The review CSV has one row per day with native balance, USD balance placeholder, confidence, source references, and notes.

## 5. Review Gate

Open the JSON and CSV before confirming. Confirm these fields:

- `account.account_id`, `institution`, and account hints represent one account.
- `account.currency` is correct and not ambiguous.
- `statement_files` are the expected PDFs.
- `coverage.complete_year` is true.
- `coverage.carry_gaps` is empty and `coverage.trailing_carry_days` is small; carried balances near year-end are evidence gaps, not observations.
- `coverage.carried_forward_days` is plausible for the statement cycle. Carried spans under the 40-day gap threshold (for example one missing monthly statement) pass the automated gates, so a high carried-to-observed ratio still needs the user's explicit acceptance.
- Every day in the year has a native balance.
- Rows with ambiguous-separator notes match the magnitudes printed on the statement.
- `warnings` are either resolved or explicitly accepted by the user.

Stop for user review when any of these appear:

- Multiple account-number hints or conflicting institution labels.
- Dates outside the requested tax year.
- Missing opening balance or missing days.
- Carry-forward gaps longer than 40 days (`coverage.carry_gaps`), including statements that stop before December 31.
- Unknown or ambiguous currency, including `$` without country/context.
- Ambiguous thousands/decimal separators flagged in row notes or warnings.
- Materially different same-day balance candidates flagged in row notes.
- Scanned/image-only PDFs.
- Low-confidence candidate rows.
- Extracted totals that visibly conflict with statement summaries.

Do not edit rows by guess. If the statement set cannot support a complete daily ledger, say so and ask for better statements or explicit user/preparer review.

## 6. FX Dependency

For USD accounts, confirm without FX.

For non-USD accounts:

1. Prefer the separate `get-year-end-fx-rate` skill to create a retained year-end workpaper; FBAR-style conversion uses the December 31 rate, not a yearly average.
2. A `get-yearly-fx-rate` workpaper is accepted only when it is explicitly FBAR/year-end compatible (see below).
3. Pass the dependency's `workpaper.json` to `confirm-account`.
4. Do not search for FX sources inside this skill.
5. Do not accept a bare rate in chat.

The checker accepts the dependency only when `workpaper.json`:

- Has `skill: "get-year-end-fx-rate"` or `skill: "get-yearly-fx-rate"`.
- Matches the account currency and tax year.
- Has a positive `foreign_per_usd` or `usd_per_foreign` rate.
- Includes source/proof metadata.
- For `get-yearly-fx-rate` workpapers only: carries explicit year-end/FBAR wording (FBAR, FinCEN, year-end, December 31, last day, end of year, or equivalent) in its rate fields (`rate_kind`, `method`, `rate_type`, `conversion_context`, `use_case`, `rate_context`) or source metadata, or sets `fbar_compatible: true`. Source provenance alone (Treasury, Fiscal Data, FMS) does not qualify a rate as year-end - those publishers issue both year-end and yearly-average tables. Any yearly/annual/period-average language anywhere (rate fields, source notes, or caveats) disqualifies the workpaper, even if a year-end word also appears. Caveats never count as positive evidence. `get-year-end-fx-rate` workpapers are year-end by construction and need no extra marking.

In practice `get-year-end-fx-rate` is the supported path: the yearly dependency's normal output describes a yearly-average table and is rejected. If a `get-yearly-fx-rate` workpaper only says yearly average or annual average, stop and ask the user to produce a `get-year-end-fx-rate` workpaper instead.

## 7. Confirm One Account

USD:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input "work/account-1.json" \
  --balances-confirmed \
  --out "work/account-1-confirmed.json"
```

Non-USD:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input "work/account-1.json" \
  --balances-confirmed \
  --fx-workpaper-json "work/fx-year-end-proof/cop-2025-fbar/workpaper.json" \
  --out "work/account-1-confirmed.json"
```

If `confirm-account` reports carry-forward gaps, show the user `coverage.carry_gaps`, ask for the missing statements first, and only re-run with `--accept-carry-forward` after the user explicitly accepts carried balances for those spans. Accepted gaps make the final daily answer `insufficient-records` instead of a confident `no`.

The confirmed JSON and CSV contain USD balances. Negative balances are treated as zero for threshold aggregation and maximum-value calculations.

After confirmation, ask: "Do you have another foreign account for the same year to add?" Continue account-by-account until the user says no.

## 8. Aggregate All Confirmed Accounts

Run:

```bash
python3 "<package-root>/scripts/fbar_threshold_check.py" aggregate \
  --account-ledger "work/account-1-confirmed.json" "work/account-2-confirmed.json" \
  --out "outputs/fbar-2025-summary.json"
```

The aggregate command writes:

- Final JSON decision file.
- Final daily combined CSV.
- Concise human PDF summary.

Use the final JSON/CSV as the data source for the final answer. The PDF is for human reading only.

## 9. Final Response

Lead with the answer, mapped from `daily_threshold.answer` in the final JSON (`yes` / `no` / `insufficient-records`):

```text
Daily threshold: Yes/No/Insufficient records.
FinCEN max-value view: Yes/No.
```

Then list:

- Over-$10,000 dates, if any.
- Final JSON, CSV, and PDF paths.
- Per-account confirmed ledger paths.
- FX workpaper paths used for non-USD accounts.
- Any warnings or unresolved review limits.

Do not call the result legal advice, and do not say an FBAR was filed or prepared.
