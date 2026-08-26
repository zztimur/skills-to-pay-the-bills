# FBAR Threshold Check Workflow

Use this workflow whenever the skill is triggered. Keep all work local unless the user explicitly asks otherwise.

## 1. Intake

Collect or infer:

- Calendar year.
- Statement PDF paths to hand to `statement-intake-preflight`.
- Work folder, defaulting to `work/`.
- Output folder, defaulting to `outputs/`.

Start with a plain-language contract. If the year or PDF paths are missing, say:

```text
I can prepare an FBAR threshold support check, not determine whether a filing is required. Please upload the statement PDFs for one foreign account and confirm the calendar year. We will review one account at a time.
```

When the year and PDF paths are already supplied, acknowledge them and start preflight. Do not ask for a currency, account number, or institution before the statement evidence requires it.

Before running extraction:

- Use `statement-intake-preflight` with `--scope one-account --require-institution` to verify shared statement intake scope: one account, one calendar year, one currency bucket, one issuer, readable PDFs, and account/currency hints.
- Treat `statement-intake-preflight` as a required companion skill, not optional setup. If it is not installed or discoverable, stop before extraction and ask the user to install or run it.
- Verify only the FBAR-specific intent here: the user wants an FBAR threshold support check, not an official filing.

If preflight reports multiple accounts, incomplete account linkage, multiple currencies, mixed years, low/no text, ambiguous currency, or unresolved issuer evidence, resolve that in the preflight step before this skill extracts balances. The preflight owns the user questions: proceed silently when it corroborates a currency, explain a clearly contextual prior-year opening balance (including an exact prior December 31 opening boundary) without asking, ask for an ISO code only for weak/unknown currency evidence, and ask for one-account or institution confirmation only when the matching evidence is unresolved. Do not request a full account number by default or ask again when `user_resolutions` already records the answer.

## 2. Source Anchors

Read `references/fbar-source-notes.md` when explaining the threshold, maximum account value, source limitations, or FX dependency behavior.

Use careful language:

- "FBAR threshold support check" rather than "FBAR filing prepared."
- "Daily threshold exceeded/not exceeded in reviewed records" rather than a legal filing opinion.
- "FinCEN maximum-value view" for the aggregate account-maximum calculation.
- "Records insufficient for a confident no" when coverage is incomplete.

## 3. Runtime Setup

For balance extraction, use a Python runtime with `pdfplumber`:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" dependency-check
```

If `pdfplumber` is unavailable in Codex Desktop, call `load_workspace_dependencies` and rerun with the bundled Python executable before treating it as unavailable. Outside Codex Desktop, stop before balance extraction, explain that it needs a Python runtime with `pdfplumber`, and give one next step. The shared text-layer readiness check is owned by `statement-intake-preflight`; `confirm-account`, `aggregate`, and `self-test` do not require `pdfplumber`.

## 4. Preflight One Account

Before extracting balances, run the shared statement preflight with `--scope one-account --require-institution`:

```bash
python3 -B "<preflight-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --scope one-account \
  --require-institution \
  --out "work/statement-preflight.json"
```

Review the preflight JSON and CSV. A `ready-for-domain-extraction` JSON can proceed. For `review-required`, stop here: structural `stop` gates require corrected input PDFs and a new preflight; review gates require explicit user confirmation of every listed code and a separate reviewed handoff. Do not duplicate those shared checks in this skill; preflight owns PDF readability, year/account/currency scope, ambiguous `$`, and account/institution hints. Preflight does not replace FBAR balance extraction; it only standardizes the intake handoff.

After the user reviews every non-structural gate, run:

```bash
python3 -B "<preflight-root>/scripts/statement_intake_preflight.py" review-handoff \
  --input "work/statement-preflight.json" \
  --accept-gate "ambiguous-dollar" \
  --user-review-confirmed \
  --out "work/statement-preflight-reviewed.json"
```

Repeat `--accept-gate` for every code in `review_gates`. Schema 1.3 handoffs can also retain exact certificate-issued dates, a separately labelled migration/reissue attestation, and a month-resolution opening attestation. Use `--confirm-certificate-issued-date YYYY-MM-DD` for every source certificate date; add `--confirm-account-migrated-or-reissued-on` with a specific `--migration-note` only when the user attests that event. For one leading gap, choose either exact `--confirm-account-opened-on` or `--confirm-account-opened-month`, never both. Generated-on and certificate dates remain metadata, never statement-period coverage. `extract-account` rejects a raw review JSON, incomplete/mismatched resolutions, structural gates, or changed source evidence.

The preflight also pins the exact statement sequence with a positive byte size and SHA-256 fingerprint for each PDF. `extract-account` refuses a missing, legacy-unfingerprinted, duplicated, reordered, or changed file. It checks the fingerprint both before parsing and immediately after parsing; a source change requires a fresh preflight and, if applicable, a new reviewed handoff.

## 5. Extract One Account

Run:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --preflight-json "work/statement-preflight.json" \
  --out "work/account-1.json"
```

For a reviewed handoff, replace `work/statement-preflight.json` with `work/statement-preflight-reviewed.json`.

Optional flags:

- `--account-id`: stable local identifier when the user has one.
- `--institution`: optional institution label. When the reviewed preflight contains a typed issuer resolution, it must match that resolution; otherwise extraction rejects the override.
- `--account-currency`: ISO code when a clean statement set cannot safely infer it. It cannot override a user-confirmed currency from a reviewed handoff; that handoff code is used instead of inferring a bare `$`.
- `--preflight-json`: required ready JSON or reviewed-handoff JSON from `statement-intake-preflight`; the script rejects absent, review-required, stale, mismatched, incomplete, resolution-invalid, or file-identity-invalid handoffs.
- `--mode auto|transaction-balances|reconcile-movements`: default `auto` selects a final Balance/Saldo column when present or the opening-plus-movements reconciliation lane otherwise. Use an explicit mode only to constrain a known source layout.
- `--csv`: review CSV output path.

The script writes:

- Account JSON at `--out`.
- Review CSV beside the JSON unless `--csv` is supplied.

The review CSV has one row per day with native balance, USD placeholder, evidence class, confidence, source references, notes, and review flags. The JSON separates `evidence_status`, `integrity_status`, `value_model`, `time_alignment`, and `parser_coverage`. The shared geometry parser identifies column roles before parsing amounts, treats ISO timestamps and source-resolved full dates consistently across currencies, ignores numbers in descriptions, and uses only the final Balance/Saldo column. A debit/credit table may reconstruct balances only from a labelled opening balance through signed movements to a matching labelled close; it remains `diagnostic-reconstructed`. If a visually dated row is omitted or reconciliation fails, `status` is `parser-coverage-defect` and the named source profile must be reviewed or replaced by supported attested evidence.

## 6. Review Gate

Open the JSON and CSV before confirming. Confirm these fields:

- `preflight` summary, if present, matches the reviewed intake artifact. For reviewed handoffs, confirm its `user_resolutions`, `coverage_hints`, and `verified_statement_files` remain present and source-bound; an opt-in issuer gate must have its typed institution resolution.
- `parser_coverage.status` is `covered`; stop on `parser-coverage-defect`.
- `evidence_status.class` accurately says `formal-extracted`, `diagnostic-reconstructed`, or `user-attested-*`, and `integrity_status.reconciliation` is acceptable for that evidence class.
- `account.account_id`, `institution`, and `account.currency` are usable for the confirmed ledger; if not, return to preflight or rerun extraction with an explicit override instead of adjudicating intake ad hoc here.
- `statement_files` are the expected PDFs; the `preflight.verified_statement_files` summary records the matched ordered paths, byte sizes, and SHA-256 fingerprints.
- `coverage.complete_year` is true.
- `coverage.carry_gaps` is empty and `coverage.trailing_carry_days` is small; carried balances near year-end are evidence gaps, not observations.
- `coverage.carried_forward_days` is plausible for the statement cycle. Carried spans under the 40-day gap threshold (for example one missing monthly statement) pass the automated gates, so a high carried-to-observed ratio still needs the user's explicit acceptance.
- `data_sufficiency.daily_threshold.answer` is not `insufficient-records` and `data_sufficiency.maximum_account_value.answer` is not `not-determinable`. If either appears, say the records cannot support that conclusion; do not confirm or infer a maximum from period-end summaries.
- Every day in the year has a native balance.
- Rows with ambiguous-separator notes match the magnitudes printed on the statement.
- `warnings` are either resolved or explicitly accepted by the user.

Present this as a concise review card before asking for confirmation; do not ask the user to interpret raw JSON or a 365-row CSV unaided. Include:

- Account label, institution when known, and currency.
- Coverage: observed days, carried-forward days, and any missing days or carry-forward gaps.
- Evidence profile and any insufficiency result from `data_sufficiency`.
- Evidence classes for any period-end, carried-forward, or missing-opening rows.
- Flagged dates or rows, summarized in plain language, plus each review artifact path.
- When `review_summary.same_day_balance_candidates.requires_user_review` is true, list its count and each flagged date's selected candidate/range and source references; do not ask the user to infer this from the raw ledger.
- When `review_summary.period_end_summaries.requires_user_review` is true, list each exact period end plus its summary and period-end source references, and state that intervening carry gaps remain unobserved.
- The exact next step: upload missing statements, correct an assumption, or confirm the ledger.

Use a direct confirmation prompt such as:

```text
I extracted [account label] for [year]. Coverage: [observed] observed days and [carried] carried-forward days. Flags: [plain-language summary]. Please confirm that the account/currency and flagged rows are correct, or upload the missing statement(s) or corrections. If everything is correct, reply: "I confirm this ledger."
```

Stop for user review when any of these appear:

- Missing opening balance or missing days.
- Carry-forward gaps longer than 40 days (`coverage.carry_gaps`), including statements that stop before December 31.
- A `possible-missing-statement-period` preflight warning, including omitted mid-year or year-end statement periods. The extracted ledger is review-required and does not contain a daily threshold answer; long carry gaps still block confirmation until explicitly accepted.
- Ambiguous thousands/decimal separators flagged in row notes or warnings.
- Materially different same-day balance candidates flagged in row notes.
- Low-confidence candidate rows.
- Extracted totals that visibly conflict with statement summaries.

Do not edit rows by guess. If the statement set cannot support a complete daily ledger, say so and ask for better statements or explicit user/preparer review.

## 7. FX Dependency

For USD accounts, confirm without FX.

For non-USD accounts:

1. Use the separate `get-year-end-fx-rate` skill to create a retained year-end workpaper; FBAR-style conversion uses the December 31 rate, not a yearly average.
2. Pass that dependency's `workpaper.json` to `confirm-account`.
3. Do not search for FX sources inside this skill.
4. Do not accept a bare rate in chat.

The checker accepts the dependency only when `workpaper.json`:

- Has `skill: "get-year-end-fx-rate"`.
- Matches the account currency and tax year.
- Has a positive `foreign_per_usd` or `usd_per_foreign` rate.
- Includes source/proof metadata.

Do not use `get-yearly-fx-rate` workpapers for FBAR conversion; yearly-average rates belong to tax-support workflows, not year-end FBAR support.

## 8. Confirm One Account

USD:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input "work/account-1.json" \
  --balances-confirmed \
  --out "work/account-1-confirmed.json"
```

Non-USD:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input "work/account-1.json" \
  --balances-confirmed \
  --fx-workpaper-json "work/fx-year-end-proof/cop-2025-fbar/workpaper.json" \
  --out "work/account-1-confirmed.json"
```

If `confirm-account` reports carry-forward gaps, show the user `coverage.carry_gaps`, ask for the missing statements first, and only re-run with `--accept-carry-forward` after the user explicitly accepts carried balances for those spans. Accepted gaps make the final daily answer `insufficient-records` instead of a confident `no`.

The confirmed JSON and CSV contain USD balances. Negative balances are treated as zero for threshold aggregation and maximum-value calculations.

After confirmation, ask: "Do you have another foreign account for the same year to add?" Continue account-by-account until the user says no.

If statement extraction cannot support a daily ledger, do not coerce a certificate or reported maximum into the formal lane. After explicit user/preparer review, use `attest-account` with one evidence class:

- `user-attested-daily` requires a `date,native_balance` CSV.
- `user-attested-zero` records an all-year zero assertion.
- `user-attested-maximum` requires an annual maximum and accepts an optional known date.

Pass the source PDFs, preflight JSON, every raw review gate via `--accept-gate`, confirmed identity/currency, and `--user-attestation-confirmed`. Non-USD evidence still requires the year-end FX workpaper. The result is already confirmed, remains non-formal, and can be aggregated. An undated maximum contributes zero to the known daily lower bound and its full converted value to every possible daily upper bound; it is never assigned to an invented date.

## 9. Aggregate All Confirmed Accounts

Run:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" aggregate \
  --account-ledger "work/account-1-confirmed.json" "work/account-2-confirmed.json" \
  --packet-root . \
  --out "outputs/fbar-2025-summary.json"
```

Every account ledger must have been confirmed with schema 1.3 or later; schema 1.4 adds explicit evidence/integrity/value-model metadata and annual-maximum-only accounts. Older ledgers remain readable only where their precision and coverage contracts are safe. Re-extract/reconfirm any ledger the aggregate rejects.

The aggregate command writes:

- Final JSON decision file.
- Final daily combined CSV.
- Concise human PDF summary.
- Portable postflight manifest binding the exact ledger/FX/output set by packet-relative path, byte size, and SHA-256.

`--packet-root` must contain every bound input and output; copy that directory as a unit. Without it, aggregate uses the safe common ancestor and refuses a filesystem-root-only packet. Use the final JSON/CSV as the data source for the final answer. The PDF is for human reading only. Run `verify-packet --summary ... --manifest ...` after any copy or before reuse. Verification requires the exact binding set, validates nested FX proof, and recomputes converted values, daily bounds, maxima, and rounding results. Aggregate reports per-day known and possible totals, lower/upper threshold answers, answer sensitivity, date-only time alignment, exact versus whole-dollar maxima, and rounding-policy comparison. Exactly `$10,000` is not an exceedance.

## 10. Final Response

Lead with the answer, mapped from `daily_threshold.answer` in the final JSON (`yes` / `no` / `review-required` / `insufficient-records`):

```text
Daily threshold: Yes/No/Review required/Insufficient records.
FinCEN max-value view: Yes/No.
```

Then list:

- Over-$10,000 dates, if any.
- Final JSON, CSV, and PDF paths.
- Per-account confirmed ledger paths.
- FX workpaper paths used for non-USD accounts.
- Any warnings or unresolved review limits.
- Evidence class, integrity status, time alignment, and lower/upper answers whenever answer sensitivity is present.

A path (plain or as a Markdown link) is only clickable/downloadable when the chat client has direct filesystem access to this machine, true for a local desktop session but not for a hosted/remote session (for example, Claude Code on the web) where the user's browser cannot reach this container's filesystem. When running in such a session, also deliver the final JSON, CSV, and PDF, plus any per-account ledgers and FX workpapers referenced above, using the host's file-delivery capability (for example, Claude Code's `SendUserFile` tool).

Do not call the result legal advice, and do not say an FBAR was filed or prepared.
