# Statements to Interest

This skill exists for a very specific tax-season annoyance: I have statement PDFs, I need the interest income, and I do not want an agent inventing a number because a line kind of looked like interest.

It does the boring path on purpose:

1. Read one institution's machine-readable statement PDFs for one tax year.
2. Infer the account currency and statement coverage from the institution's own statement text when possible.
3. Extract credited interest rows.
4. Put the evidence in JSON and a review CSV so the rows can be inspected.
5. Apply FX only when the rows are not already USD, using `get-yearly-fx-rate` for published yearly-average proof.
6. Generate a polished IRS-oriented PDF support packet for Schedule B, FBAR, and Form 8938 review.

This is not tax advice. It is not an official IRS form. It is a support worksheet with an audit trail, which is exactly the kind of boring artifact tax work usually needs.

## The Rule

One institution. One tax year. Text PDFs.

If the input is a pile of mixed banks, mixed years, screenshots, scans, CSVs, or vibes, split the job first. The narrowness is the product. The skill should stop early instead of pretending a messy packet is fine.

## What It Produces

For a clean run, the user-facing deliverable is the PDF packet:

- `outputs/...interest-support-packet.pdf` with the human-facing support packet.
- `work/...interest-analysis.json` with statement metadata, extracted rows, excluded candidates, warnings, and totals.
- `work/...interest-items.csv` for quick internal review in a spreadsheet.

The row review matters. The PDF is only as good as the rows behind it, so the JSON and CSV stay available as audit artifacts, but they should not distract from the PDF unless you ask for them.

The packet should name the institution, account currency, statement periods, files reviewed, source-currency total, FX treatment, and USD reporting total. If a COP statement uses `$`, the skill should treat that as a symbol inside a COP account, not as automatic USD.

## FX Dependency

Published annual FX sourcing belongs to `get-yearly-fx-rate`. Run that skill for the statement currency/year, then pass its `workpaper.json` to this skill:

```bash
python statements-to-interest/scripts/statements_to_interest.py fx-prompt \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-workpaper-json "work/fx-rate-proof/cop-2025-source/workpaper.json"
```

If `get-yearly-fx-rate` is unavailable or cannot produce a proof-backed annual workpaper, stop before the PDF and ask for either the dependency output or a confirmed user/preparer custom rate. Do not quietly rebuild annual FX source search inside this skill.

You can preflight the dependency with:

```bash
python statements-to-interest/scripts/statements_to_interest.py dependency-check
```

## Use It In Codex

Ask Codex to use the skill directly:

```text
Use $statements-to-interest to analyze these 2025 Example Bank USD statement PDFs and generate the support packet.
```

The skill should enforce the scope, run the extractor, review JSON/CSV, generate the packet, and report the paths plus totals.

## Use It In Claude Code

The same package also carries a Claude plugin command:

```text
/statements-to-interest:statements-to-interest 2025 "Example Bank" "/path/to/q1.pdf" "/path/to/q2.pdf" "/path/to/q3.pdf" "/path/to/q4.pdf"
```

Claude should use the same root `SKILL.md`, references, and script. There should not be a second secret workflow hiding in the command file.

## Run The Script Manually

Use a Python environment with `pdfplumber`, `reportlab`, and `pypdf`.

Extract first:

```bash
python statements-to-interest/scripts/statements_to_interest.py extract \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --institution "Example Bank" \
  --out "work/example-bank-2025-interest-analysis.json"
```

Then review the JSON and CSV. If the rows are already USD, generate the packet without FX flags:

```bash
python statements-to-interest/scripts/statements_to_interest.py report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

If the rows are not USD, run `get-yearly-fx-rate` for the currency/year, show the resulting USD total and proof documents, and ask me to confirm that rate or send a custom rate. Do not freestyle the conversion, and do not generate the PDF until the rate is confirmed.

Use `fx-prompt` to make that confirmation step explicit:

```bash
python statements-to-interest/scripts/statements_to_interest.py fx-prompt \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-workpaper-json "work/fx-rate-proof/cop-2025-source/workpaper.json"
```

After I confirm, generate the PDF:

```bash
python statements-to-interest/scripts/statements_to_interest.py report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-workpaper-json "work/fx-rate-proof/cop-2025-source/workpaper.json" \
  --fx-rate-confirmed \
  --fx-confirmation-note "User confirmed the published yearly average rate" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

For Colombian peso statements, use `get-yearly-fx-rate` if it can produce a published yearly-average workpaper. If only daily/monthly rates are available, do not calculate the annual average yourself; ask me for the dependency output or a custom rate.

For custom user/preparer rates, `--fx-source` is optional. If no source is supplied, the PDF labels the rate as user/preparer supplied, notes that no independent source was provided, and adds a preparer-review warning.

Use item-date spot rates only when I explicitly ask for them. When using item-date spot rates, pass a date-keyed `--fx-rates-json` file so each interest row converts with the rate for its own receipt/accrual date instead of flattening the packet into one blended rate.

## Review Flags

The skill should pause for human confirmation when the evidence gets shaky:

- Low-confidence rows.
- Unknown currency.
- Multiple detected currencies.
- Institution name found only in the file path.
- Interest-like candidates excluded from totals.
- Amounts that look like balances instead of interest.
- Anything outside the requested tax year.

This is the important behavior. A confident wrong tax number is worse than an unfinished packet.

## Maintenance

After parser changes:

```bash
python statements-to-interest/scripts/statements_to_interest.py self-test
```

To check the FX dependency:

```bash
python statements-to-interest/scripts/statements_to_interest.py dependency-check
```

Before shipping the skill:

```bash
python -S skill-forge/scripts/inspect_skill_package.py statements-to-interest --json --strict
claude plugin validate --strict statements-to-interest
```

`skill-forge` is the gatekeeper for this repo. If it says the package is not ready, fix the package before pushing.
