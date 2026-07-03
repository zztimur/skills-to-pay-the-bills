---
name: statements-to-interest
description: Use for tax/Schedule B/FBAR support when analyzing one bank's text PDF statements for one tax year to extract interest income, apply FX if needed, and create an IRS-oriented packet.
---
# Statements To Interest

Analyze one institution's machine-readable statement PDFs for one tax year, extract interest income, infer account/institution metadata, convert to USD only when needed, and generate a tax-support packet. Produce support documentation, not an official IRS form and not tax advice.

## Scope Gate

Proceed only when the request is for:

- One institution or account provider.
- One tax year.
- Machine-readable statement PDFs.
- Interest income extraction or tax/Schedule B/FBAR support documentation.

Stop and ask the user to split the work if statements span multiple institutions or tax years. If PDFs are scanned/image-only, tell the user this version needs text PDFs. CSV or pasted rows require manual review outside the deterministic PDF workflow.

## Required Workflow

Read `references/workflow.md` before running analysis. It contains the operational checklist, commands, review gates, FX decision rules, troubleshooting, and final-response template.

Dependency: for non-USD published yearly-average FX, use the separate `get-yearly-fx-rate` skill to create a retained FX proof workpaper. `statements-to-interest` consumes that skill's `workpaper.json` and passes its proof documents through in the final output. If `get-yearly-fx-rate` is unavailable, stop before the PDF and ask the user to install/run it or provide a confirmed user/preparer custom rate; do not recreate annual-rate source search inside this skill. Use `dependency-check` when you need a quick installed-dependency preflight.

Use `scripts/statements_to_interest.py` for the deterministic work:

```bash
python "<package-root>/scripts/statements_to_interest.py" extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --account-currency COP \
  --out work/interest-analysis.json
```

Review the JSON and review CSV before reporting. Treat the CSV as an internal row-review artifact, not the user-facing deliverable unless the user asks for it. Confirm the `institution_profile`, account currency, statement titles, and statement periods. Do not invent missing rows. Ask for confirmation when rows are low confidence, ambiguous, out of scope, when the institution label was found only in a file path, or when account currency cannot be inferred.

For non-USD rows, default to `get-yearly-fx-rate` for the published yearly average exchange rate and retained proof. Do not calculate the yearly average yourself from daily/monthly data. If `get-yearly-fx-rate` cannot produce a published annual workpaper, ask the user for a custom rate, with an optional source, instead of deriving one. Prompt the user with the workpaper rate, source, proof documents, direction, and resulting USD total; ask them to confirm that rate or provide a custom rate before generating the PDF. Use the `fx-prompt` command to produce the exact user-facing confirmation question when a candidate workpaper is available:

```bash
python "<package-root>/scripts/statements_to_interest.py" fx-prompt \
  --input work/interest-analysis.json \
  --fx-workpaper-json work/fx-rate-proof/cop-2025-source/workpaper.json
```

When the FX gate is reached, do not present JSON/CSV as the completed result. Ask the confirmation/custom-rate question and make clear that the PDF is pending until the user answers. Do not generate a non-USD PDF until the user confirms or supplies a custom rate. Pass `--fx-rate-confirmed` only after that confirmation:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input work/interest-analysis.json \
  --fx-workpaper-json work/fx-rate-proof/cop-2025-source/workpaper.json \
  --fx-rate-confirmed \
  --fx-confirmation-note "User confirmed the published yearly average rate" \
  --out outputs/interest-support-packet.pdf
```

If the user/preparer supplies a custom rate instead of a `get-yearly-fx-rate` workpaper, use `--fx-method user-rate` with `--fx-rate`, `--rate-direction`, and `--fx-rate-confirmed`. Add `--fx-source` only when the user/preparer supplies one. If no source is supplied, the PDF must disclose that no independent source was provided and add a preparer-review warning.

Use item-date spot rates only if the user or preparer explicitly asks for that method. In that case, still prompt for confirmation before reporting and pass a date-keyed `--fx-rates-json` file:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input work/interest-analysis.json \
  --fx-method posted-daily-spot \
  --fx-rates-json work/fx-rates.json \
  --fx-source "Posted daily spot source, currency, retrieval date" \
  --fx-rate-confirmed \
  --fx-confirmation-note "User requested and confirmed daily spot rates" \
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

The final answer after a completed run should lead with the PDF path, then include the JSON path, row count, source-currency total, USD total, FX proof workpaper/proof-document paths from `get-yearly-fx-rate` when used, and any warnings or manual-review flags. Mention the CSV only as an internal review artifact unless the user asks for it.

## Runtime

Use a Python environment with `pdfplumber`, `reportlab`, and `pypdf`. In Codex desktop, call `load_workspace_dependencies` and prefer the bundled workspace Python when system Python lacks these packages. If no compatible runtime is available, stop before analysis and report the missing dependency.

After changing the parser, run:

```bash
python "<package-root>/scripts/statements_to_interest.py" self-test
```

To preflight the FX dependency, run:

```bash
python "<package-root>/scripts/statements_to_interest.py" dependency-check
```

## Package Compatibility

This is a single dual-runtime package. Codex/OpenAI Agent Skills use this root `SKILL.md`, `agents/openai.yaml`, `scripts/`, and `references/`. Claude Code uses `.claude-plugin/plugin.json` plus `commands/statements-to-interest.md`, while reusing the same scripts and references. Do not maintain separate workflow copies.
