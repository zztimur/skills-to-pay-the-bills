# Statements To Interest Workflow

Use this workflow whenever the skill is triggered. Keep all work local unless the user explicitly asks for otherwise.

## 1. Intake

Collect or infer these inputs:

- Statement PDF paths.
- Tax year.
- Institution name visible in the PDFs or clearly represented by the file paths.
- Account currency, inferred from statement text such as `Movimientos de cuenta en COP` when available.
- Output folder, defaulting to `outputs/`.
- Work folder for JSON/CSV intermediates, defaulting to `work/`.

Before running the script, verify scope:

- All PDFs belong to one institution.
- All PDFs belong to one tax year.
- All PDFs belong to one account currency bucket.
- Files are PDFs, not screenshots/images.
- The user wants an interest-income worksheet or tax-support packet, not official tax filing.

If the request mixes banks, accounts from unrelated providers, or tax years, stop and ask the user to split the job.

## 2. Runtime Setup

Use a Python runtime with `pdfplumber`, `reportlab`, and `pypdf`.

In Codex desktop:

1. Call `load_workspace_dependencies`.
2. Use the bundled Python path it returns when system Python is missing dependencies.
3. Check dependencies before analysis:

```bash
python -c 'import pdfplumber, reportlab, pypdf; print("deps ok")'
```

If dependencies are unavailable, stop before analysis and tell the user which package is missing.

## 3. Extraction

Create the work/output folders before running commands:

```bash
mkdir -p work outputs
```

Run extraction:

```bash
python "<package-root>/scripts/statements_to_interest.py" extract \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --institution "Example Bank" \
  --account-currency COP \
  --out "work/example-bank-2025-interest-analysis.json"
```

The script writes:

- Analysis JSON at `--out`.
- CSV beside the JSON unless `--csv` is supplied.
- `institution_profile` with institution name, account currency, statement titles, detected periods, institution-label source, and statement count.

Use `--account-currency` only when the account currency is known from file organization or statement review and the script cannot infer it. This is especially important for statements that use `$` for local currency, such as Colombian peso statements. Do not treat `$` alone as proof of USD.

If extraction says the PDF has little machine-readable text, ask for text PDFs. Do not OCR or hand-transcribe unless the user explicitly changes the scope.

For large or messy input sets, triage before extraction:

- If the user provides many PDFs, confirm they are all for one institution, one tax year, and one currency bucket before running the whole set.
- If filenames, folders, or visible statement periods suggest mixed institutions, accounts from unrelated providers, currencies, or tax years, stop and ask the user to split the job.
- If the PDFs are unusually large or the text layer looks noisy, run a small extraction first and report what coverage was verified before continuing.

## 4. Review The Extracted Results

Open the JSON and CSV before generating a report. Review these JSON fields:

- `statement_files`: files reviewed, page counts, detected periods, currency markers.
- `institution_profile`: institution, account currency, title/period coverage, and institution-label source.
- `rows`: counted interest rows.
- `totals.row_count`: counted row count.
- `totals.foreign_total_by_currency`: source-currency totals.
- `warnings`: script-level review flags.
- `excluded_candidates`: interest-like lines excluded from totals.

For each counted row, check:

- Date is inside the requested tax year.
- Description is genuinely interest income or credited interest.
- `amount_foreign` is the interest amount, not the balance.
- Currency is expected and not `UNKNOWN`.
- Rows with `$` symbols use the account currency when statement text identifies the account currency.
- Confidence and notes do not require user confirmation.

Ask the user to confirm before reporting when:

- Any row has `confidence` set to `low`.
- The notes say the transaction date is missing.
- The institution label appears only in the file path.
- Account currency was inferred only from folder/file naming, cannot be inferred, or conflicts across PDFs.
- There are excluded interest-like candidates that could be real interest.
- The account has multiple currency markers.
- Counted rows include unexpected currencies.
- The extracted total looks inconsistent with statement summaries.

Do not add, remove, or edit rows by guess. If a row is missing or wrong, explain the evidence and ask for confirmation or better source data.

## 5. FX Decision

If all counted rows are USD:

- Do not ask for an FX rate.
- Generate the report without `--fx-method`, `--fx-rate`, or `--fx-source`.

If counted rows are not USD:

1. Use an official or published yearly average exchange rate for the tax year as the default candidate.
2. Prefer the IRS yearly-average table when the currency appears there for the tax year.
3. If the IRS table does not list the currency, use a yearly average published by a bank, central bank, tax authority, or reputable FX provider. Record the source, currency, tax year, retrieval date, and whether the source labels it as annual/yearly average.
4. Do not calculate the yearly average yourself from daily, weekly, monthly, or intraday rates. If no official/published annual average is found, stop before the PDF and ask the user/preparer for a custom rate and source.
5. For Colombian peso (COP), the IRS yearly-average page may not list COP. Look for a published annual average from a bank, Banco de la Republica/Superintendencia source, Treasury/FiscalData, Oanda, XE, or another reputable source. If only daily TRM data is available, do not average it yourself.
6. Show the user the proposed yearly average rate, source, direction, and resulting USD total before generating the PDF. Ask: "Confirm this published yearly average rate, or send a custom rate/source to use instead."
7. Accept a user-provided custom rate and source when the user or preparer prefers it. Use `--fx-method user-rate` for custom rates.
8. Generate the non-USD PDF only after the user confirms the proposed yearly average or provides a custom rate/source. Pass `--fx-rate-confirmed` and a concise `--fx-confirmation-note`.
9. Confirm rate direction:
   - Default is `foreign-per-usd`, matching IRS yearly average tables.
   - Use `--rate-direction usd-per-foreign` only when the supplied rate is USD per one foreign currency unit.
10. Use item-date spot rates only if the user or preparer explicitly asks for that method.

Yearly average confirmation prompt shape:

```text
I found/propose this published yearly average FX rate for <currency>/<tax year>:

Rate: <rate> <foreign currency> per 1 USD
Source: <source and retrieval date>
Method: <official/published yearly average; do not use agent-calculated averages>
Source-currency total: <amount>
USD total using this rate: <amount>

Confirm this published yearly average rate, or send a custom rate/source to use instead.
```

Yearly average report example:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-method posted-yearly-average \
  --fx-rate 4200.00 \
  --fx-source "Published yearly average exchange rate source, currency, year, retrieved YYYY-MM-DD" \
  --fx-rate-confirmed \
  --fx-confirmation-note "User confirmed the published yearly average rate" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

Daily spot override example:

```json
{
  "method": "posted-daily-spot",
  "source": "Banco de la Republica TRM series 1, retrieved 2026-07-02",
  "rate_direction": "foreign-per-usd",
  "rates": {
    "2025-01-03": "4410.50",
    "2025-09-03": "4016.94"
  }
}
```

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-method posted-daily-spot \
  --fx-rates-json "work/example-bank-2025-fx-rates.json" \
  --fx-source "Posted daily spot exchange rates, source and retrieval date" \
  --fx-rate-confirmed \
  --fx-confirmation-note "User requested and confirmed daily spot rates" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

USD report example:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

## 6. Report Verification

After generating the PDF:

- Confirm the PDF exists and has at least one page.
- Confirm the extracted text includes `Foreign Bank Interest Support Packet`.
- Confirm the PDF text includes the expected USD total.
- Confirm warning/review flags are represented when present.
- Confirm the PDF does not expose full local source paths; statement tables should use filenames.

Use `pypdf` for a quick text check:

```bash
python -c 'from pypdf import PdfReader; p="outputs/example-bank-2025-interest-support-packet.pdf"; text="\n".join((page.extract_text() or "") for page in PdfReader(p).pages); print("Foreign Bank Interest Support Packet" in text, "USD" in text)'
```

## 7. Final Response

Return a concise summary with:

- Analysis JSON path.
- Interest CSV path.
- Support PDF path.
- Institution/profile details used in the packet, especially account currency and statement coverage.
- Row count.
- Source-currency total.
- USD total.
- Warnings, excluded candidates, or confirmation decisions.
- A reminder that this is a support worksheet, not an official IRS form or tax advice.

Example:

```text
Generated the support packet for Example Bank 2025.

Rows counted: 12
Source total: USD 59.55
USD total: USD 59.55

Files:
- JSON: ...
- CSV: ...
- PDF: ...

Review flags: institution label was found only in the file path; verify the PDFs are all from Example Bank.
```

## 8. Troubleshooting

| Symptom | Action |
|---|---|
| `pdfplumber is required` | Use bundled Codex Python or another environment with `pdfplumber`. |
| `reportlab is required` | Use bundled Codex Python or install/use an environment with `reportlab`. |
| `little machine-readable text` | Ask for text PDFs; scanned/image-only PDFs are out of scope for this script. |
| Institution name not found | Retry with the visible statement title only if that is the best available text label, or use the file-path fallback warning when the file path clearly identifies the institution. |
| Multiple currencies detected | Review rows and split the job if multiple currencies are truly present. |
| `UNKNOWN` currency | Do not report until currency is confirmed. |
| `$` rows from a COP statement are labeled USD | Rerun with the current parser and/or pass `--account-currency COP`; `$` alone is not proof of USD when the statement account currency is COP. |
| Non-USD report asks for FX | Propose a published yearly average, ask the user to confirm it or provide a custom rate/source, then rerun with `--fx-rate-confirmed`. |
| Only daily/monthly rates are available | Do not calculate an annual average yourself; ask the user/preparer for an official annual average or custom rate/source. |
| Different interest dates need different FX rates | Use `--fx-rates-json` only when the user or preparer explicitly requests daily spot rates. |
| Extracted amount looks like a balance | Do not report blindly; inspect evidence text and ask the user to confirm before editing source data or relying on the row. |

## 9. Maintenance Checks

After editing `scripts/statements_to_interest.py`, run:

```bash
python "<package-root>/scripts/statements_to_interest.py" self-test
```

Before shipping or reinstalling the skill, validate the package:

```bash
python -S /path/to/skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
```

If Claude plugin compatibility matters, also run:

```bash
claude plugin validate --strict "<package-root>"
```
