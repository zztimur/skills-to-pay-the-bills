# Statements To Interest Workflow

Use this workflow whenever the skill is triggered. Keep all work local unless the user explicitly asks for otherwise.

## 1. Intake

Collect or infer these inputs:

- Statement PDF paths.
- Tax year.
- Institution name visible in the PDFs or clearly represented by the file paths.
- Output folder, defaulting to `outputs/`.
- Work folder for JSON/CSV intermediates, defaulting to `work/`.

Before running the script, verify scope:

- All PDFs belong to one institution.
- All PDFs belong to one tax year.
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
  --out "work/example-bank-2025-interest-analysis.json"
```

The script writes:

- Analysis JSON at `--out`.
- CSV beside the JSON unless `--csv` is supplied.

If extraction says the PDF has little machine-readable text, ask for text PDFs. Do not OCR or hand-transcribe unless the user explicitly changes the scope.

For large or messy input sets, triage before extraction:

- If the user provides many PDFs, confirm they are all for one institution, one tax year, and one currency bucket before running the whole set.
- If filenames, folders, or visible statement periods suggest mixed institutions, accounts from unrelated providers, currencies, or tax years, stop and ask the user to split the job.
- If the PDFs are unusually large or the text layer looks noisy, run a small extraction first and report what coverage was verified before continuing.

## 4. Review The Extracted Results

Open the JSON and CSV before generating a report. Review these JSON fields:

- `statement_files`: files reviewed, page counts, detected periods, currency markers.
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
- Confidence and notes do not require user confirmation.

Ask the user to confirm before reporting when:

- Any row has `confidence` set to `low`.
- The notes say the transaction date is missing.
- The institution label appears only in the file path.
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

1. Ask the user to choose an FX treatment.
2. Recommend IRS yearly average exchange rate for recurring statement interest in one tax year when appropriate.
3. Accept a user-provided rate and source when the user or preparer prefers it.
4. Confirm rate direction:
   - Default is `foreign-per-usd`, matching IRS yearly average tables.
   - Use `--rate-direction usd-per-foreign` only when the supplied rate is USD per one foreign currency unit.

Non-USD report example:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-method irs-yearly-average \
  --fx-rate 0.886 \
  --fx-source "IRS yearly average exchange rate table, Euro Zone Euro, 2025" \
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
| Non-USD report asks for FX | Ask the user for FX method, rate, source, and direction. |
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
