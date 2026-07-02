---
name: taxes-statements-to-interest
description: Use when analyzing one bank's text statement PDFs for one tax year to find interest income, apply FX only when needed, and render an IRS-oriented support packet.
---
# Taxes Statements To Interest

Analyze a set of bank statements for one bank and one tax year, identify interest income, convert the final total to U.S. dollars when the counted rows are not already USD, and generate a support packet PDF.

## Hard Scope

- Use this only for statements from one institution.
- Use this only for one tax year at a time.
- If the user uploads multiple banks or multiple years, stop and ask them to split the files.
- Use machine-readable text PDFs. If extraction finds little or no text, tell the user v1 does not handle scanned/image-only statements and ask for text PDFs. CSV or pasted rows need manual review outside the deterministic PDF script.
- Produce a tax-support worksheet, not an official IRS form and not tax advice.

## Workflow

1. Gather the inputs:
   - Statement PDFs.
   - Tax year.
   - One institution name.
   - Output folder, defaulting to `outputs/`.
2. Resolve the package root, then use `scripts/statements_to_interest.py` inside it. In this repo, the package root is `taxes-statements-to-interest/`.
3. Run extraction:

   ```bash
   python "<package-root>/scripts/statements_to_interest.py" extract \
     --pdf statement-01.pdf statement-02.pdf \
     --tax-year 2025 \
     --institution "Example Bank" \
     --out work/interest-analysis.json
   ```

4. Review `interest-analysis.json` and the adjacent `interest-items.csv`.
   - Check warnings and `excluded_candidates`.
   - If rows are low confidence or ambiguous, ask the user to confirm before continuing.
   - If the institution label was found only in the file path, verify the PDFs are all from the intended institution before relying on the output.
   - Do not invent missing interest rows.
5. If the counted interest rows are not already USD, ask the user to choose one FX treatment before generating the final PDF:
   - IRS yearly average exchange rate, usually reasonable for recurring statement interest in one year.
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

   For USD-denominated rows, omit `--fx-rate`; the report will carry the USD total through without conversion.
   For USD-denominated rows, also omit `--fx-method`:

   ```bash
   python "<package-root>/scripts/statements_to_interest.py" report \
     --input work/interest-analysis.json \
     --out outputs/interest-support-packet.pdf
   ```

7. Give the user the JSON, CSV, and PDF paths. Mention any review flags.

## Output Rules

- Include bank name, tax year, statement files reviewed, rows found, source-currency total, FX method/source/rate when conversion was needed, USD total, ambiguous/excluded items, and IRS-oriented review notes.
- Flag Schedule B review when there is taxable interest or a foreign account.
- Flag FBAR/Form 8938 review only as a prompt to review applicability; do not decide the user's filing obligation.
- If no interest is found, still generate a support packet when the user wants documentation.

## References

Read `references/irs-interest-reporting.md` when writing report notes, explaining the workflow, or refreshing IRS source links.

## Runtime Dependencies

Use a Python environment with `pdfplumber`, `reportlab`, and `pypdf` installed. In Codex desktop, prefer the bundled workspace Python from `load_workspace_dependencies` when system Python lacks these packages. If no compatible runtime is available, report the missing package and stop before analysis or report generation.

After changing the parser, run:

```bash
python "<package-root>/scripts/statements_to_interest.py" self-test
```

## Package Compatibility

This folder is intentionally a single dual-runtime package:

- ChatGPT/Codex/Open Agent Skills use this root `SKILL.md`, `agents/openai.yaml`, `scripts/`, and `references/`.
- Claude Code uses `.claude-plugin/plugin.json` plus `commands/statements-to-interest.md`, while reusing the same `scripts/` and `references/`.
- Claude plugin metadata keeps the plugin name `taxes`, so Claude exposes `/taxes:statements-to-interest` even though the portable package folder is `taxes-statements-to-interest`.
- Do not maintain separate ChatGPT and Claude copies of the workflow.
