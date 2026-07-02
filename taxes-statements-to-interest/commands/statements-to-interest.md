---
description: Analyze one bank statement set for one tax year and render an interest support packet
argument-hint: "<tax-year> <institution> <statement PDF paths...>"
---

You are running `/taxes:statements-to-interest $ARGUMENTS`.

Use the model-agnostic workflow in the package root `SKILL.md`. The command is a Claude plugin entrypoint only; the root `SKILL.md` is the single portable Agent Skill entrypoint.

## Hard Scope

- Use this only for statements from one institution.
- Use this only for one tax year at a time.
- If the user provides multiple banks or multiple years, stop and ask them to split the files.
- Use machine-readable text PDFs. If extraction finds little or no text, tell the user v1 does not handle scanned/image-only statements and ask for text PDFs. CSV or pasted rows need manual review outside the deterministic PDF script.
- Produce a tax-support worksheet, not an official IRS form and not tax advice.

## Workflow

1. Identify the tax year, institution name, and statement PDF paths from `$ARGUMENTS` or ask for missing values.
2. Resolve the package root:
   - In this repo source: `/Users/timur/Documents/Skills to Pay The Bills/taxes-statements-to-interest`.
   - After Claude user-scope install: the installed plugin folder, typically `~/.claude/skills/taxes`.
3. Run extraction with the shared deterministic script:

   ```bash
   python "<package-root>/scripts/statements_to_interest.py" extract \
     --pdf statement-01.pdf statement-02.pdf \
     --tax-year 2025 \
     --institution "Example Bank" \
     --out work/interest-analysis.json
   ```

4. Review `interest-analysis.json`, `interest-items.csv`, warnings, and excluded candidates.
   - If the institution label was found only in the file path, verify the PDFs are all from the intended institution before relying on the output.
5. If the counted interest rows are not already USD, ask the user to choose the FX treatment before generating the final PDF:
   - IRS yearly average exchange rate.
   - User-provided rate and source.
   - The script expects `--fx-rate` as foreign currency units per 1 U.S. dollar unless `--rate-direction usd-per-foreign` is supplied.
   - If counted rows are already USD, no FX rate is needed.
6. Generate the support packet:

   ```bash
   python "<package-root>/scripts/statements_to_interest.py" report \
     --input work/interest-analysis.json \
     --fx-method irs-yearly-average \
     --fx-rate 0.886 \
     --fx-source "IRS yearly average exchange rate table, Euro Zone Euro, 2025" \
     --out outputs/interest-support-packet.pdf
   ```

   For USD-denominated rows, omit `--fx-method` and `--fx-rate`; the report will carry the USD total through without conversion:

   ```bash
   python "<package-root>/scripts/statements_to_interest.py" report \
     --input work/interest-analysis.json \
     --out outputs/interest-support-packet.pdf
   ```

7. Return the JSON, CSV, and PDF paths plus any review flags.
