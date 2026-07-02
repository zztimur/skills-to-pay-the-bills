# Statements to Interest

This skill exists for a very specific tax-season annoyance: I have statement PDFs, I need the interest income, and I do not want an agent inventing a number because a line kind of looked like interest.

It does the boring path on purpose:

1. Read one institution's machine-readable statement PDFs for one tax year.
2. Extract credited interest rows.
3. Put the evidence in JSON and CSV so the rows can be reviewed.
4. Apply FX only when the rows are not already USD.
5. Generate a small IRS-oriented support packet for Schedule B, FBAR, and Form 8938 review.

This is not tax advice. It is not an official IRS form. It is a support worksheet with an audit trail, which is exactly the kind of boring artifact tax work usually needs.

## The Rule

One institution. One tax year. Text PDFs.

If the input is a pile of mixed banks, mixed years, screenshots, scans, CSVs, or vibes, split the job first. The narrowness is the product. The skill should stop early instead of pretending a messy packet is fine.

## What It Produces

For a clean run, expect three useful artifacts:

- `work/...interest-analysis.json` with statement metadata, extracted rows, excluded candidates, warnings, and totals.
- `work/...interest-items.csv` for quick review in a spreadsheet.
- `outputs/...interest-support-packet.pdf` with the human-facing packet.

The row review matters. The PDF is only as good as the rows behind it, so the JSON and CSV are part of the output, not temporary junk.

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

If the rows are not USD, pick an FX method, rate, source, and direction before generating the report. Do not freestyle the conversion.

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

Before shipping the skill:

```bash
python -S skill-forge/scripts/inspect_skill_package.py statements-to-interest --json --strict
claude plugin validate --strict statements-to-interest
```

`skill-forge` is the gatekeeper for this repo. If it says the package is not ready, fix the package before pushing.

