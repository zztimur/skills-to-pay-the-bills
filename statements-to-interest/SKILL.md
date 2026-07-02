---
name: statements-to-interest
description: Use for tax/Schedule B/FBAR support when analyzing one bank's text PDF statements for one tax year to extract interest income, apply FX if needed, and create an IRS-oriented packet.
---
# Statements To Interest

Analyze one institution's machine-readable statement PDFs for one tax year, extract interest income, convert to USD only when needed, and generate a tax-support packet. Produce support documentation, not an official IRS form and not tax advice.

## Scope Gate

Proceed only when the request is for:

- One institution or account provider.
- One tax year.
- Machine-readable statement PDFs.
- Interest income extraction or tax/Schedule B/FBAR support documentation.

Stop and ask the user to split the work if statements span multiple institutions or tax years. If PDFs are scanned/image-only, tell the user this version needs text PDFs. CSV or pasted rows require manual review outside the deterministic PDF workflow.

## Required Workflow

Read `references/workflow.md` before running analysis. It contains the operational checklist, commands, review gates, FX decision rules, troubleshooting, and final-response template.

Use `scripts/statements_to_interest.py` for the deterministic work:

```bash
python "<package-root>/scripts/statements_to_interest.py" extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --out work/interest-analysis.json
```

Review the JSON and CSV before reporting. Do not invent missing rows. Ask for confirmation when rows are low confidence, ambiguous, out of scope, or when the institution label was found only in a file path.

For non-USD rows, ask the user to choose and source an FX treatment before generating the PDF:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input work/interest-analysis.json \
  --fx-method irs-yearly-average \
  --fx-rate 0.886 \
  --fx-source "IRS yearly average exchange rate table, Euro Zone Euro, 2025" \
  --out outputs/interest-support-packet.pdf
```

For USD rows, omit FX flags:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input work/interest-analysis.json \
  --out outputs/interest-support-packet.pdf
```

## Report Notes

Read `references/irs-interest-reporting.md` when writing IRS-oriented notes, explaining Schedule B/FBAR/Form 8938 review flags, or refreshing source-link wording.

The final answer should include the JSON, CSV, and PDF paths; the row count and source-currency total; the USD total; and any warnings or manual-review flags.

## Runtime

Use a Python environment with `pdfplumber`, `reportlab`, and `pypdf`. In Codex desktop, call `load_workspace_dependencies` and prefer the bundled workspace Python when system Python lacks these packages. If no compatible runtime is available, stop before analysis and report the missing dependency.

After changing the parser, run:

```bash
python "<package-root>/scripts/statements_to_interest.py" self-test
```

## Package Compatibility

This is a single dual-runtime package. Codex/OpenAI Agent Skills use this root `SKILL.md`, `agents/openai.yaml`, `scripts/`, and `references/`. Claude Code uses `.claude-plugin/plugin.json` plus `commands/statements-to-interest.md`, while reusing the same scripts and references. Do not maintain separate workflow copies.
