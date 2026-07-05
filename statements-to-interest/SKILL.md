---
name: statements-to-interest
description: "Use after statement-intake-preflight for tax/Schedule B/FBAR support: extract interest income, apply FX if needed, and create an IRS-oriented packet."
---
# Statements To Interest

Analyze a preflighted statement set, extract interest income, convert to USD only when needed, and generate a tax-support packet. Produce support documentation, not an official IRS form and not tax advice.

## Scope Handoff

Proceed only when the request is for interest-income extraction or tax/Schedule B/FBAR support documentation. Shared statement intake belongs to `statement-intake-preflight`; use its reviewed `--scope one-institution` JSON before this skill extracts interest rows.

If preflight reports mixed institutions, mixed years, unreadable/scanned PDFs, mixed currencies, or ambiguous `$`, resolve that in `statement-intake-preflight` before continuing here. CSV or pasted rows require manual review outside this deterministic PDF workflow.

## Skill Dependencies

Required companion skill: `statement-intake-preflight`. Use it first for shared PDF intake with `--scope one-institution`; this skill consumes the reviewed preflight JSON through `--preflight-json` and keeps only interest-row extraction, FX confirmation, and packet generation here.

Conditional FX dependency: `get-yearly-fx-rate`. Use it for non-USD published yearly-average FX workpapers; if it is unavailable or cannot produce a published annual workpaper, stop before PDF generation and ask the user/preparer for a confirmed custom rate instead of sourcing or calculating annual FX here.

## Required Workflow

Read `references/workflow.md` before running analysis. It contains the operational checklist, commands, review gates, FX decision rules, troubleshooting, and final-response template.

Dependency: for non-USD published yearly-average FX, use the separate `get-yearly-fx-rate` skill to create a retained FX proof workpaper. `statements-to-interest` consumes that skill's `workpaper.json` and passes its proof documents through in the final output. If `get-yearly-fx-rate` is unavailable, stop before the PDF and ask the user to install/run it or provide a confirmed user/preparer custom rate; do not recreate annual-rate source search inside this skill. Use `dependency-check` when you need a quick installed-dependency check.

First use the separate `statement-intake-preflight` skill with `--scope one-institution` and review its JSON/CSV:

```bash
python3 "<preflight-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-institution \
  --out work/statement-preflight.json
```

Then use `scripts/statements_to_interest.py` for the deterministic extraction and pass the reviewed preflight JSON:

```bash
python "<package-root>/scripts/statements_to_interest.py" extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --account-currency COP \
  --preflight-json work/statement-preflight.json \
  --out work/interest-analysis.json
```

Review the JSON and review CSV before reporting. Treat the CSV as an internal row-review artifact, not the user-facing deliverable unless the user asks for it. Confirm the reviewed `preflight` summary is present, then focus this skill's review on counted interest rows, excluded interest-like candidates, totals, and FX readiness. Do not invent missing rows. Ask for confirmation when rows are low confidence, ambiguous, out of scope, interest-like candidates might be real income, counted rows contain unexpected currencies, or totals visibly conflict with statement summaries.

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

To check the FX dependency, run:

```bash
python "<package-root>/scripts/statements_to_interest.py" dependency-check
```

## Package Compatibility

This is a single dual-runtime package. Codex/OpenAI Agent Skills use this root `SKILL.md`, `agents/openai.yaml`, `scripts/`, and `references/`. Claude Code uses `.claude-plugin/plugin.json` plus `commands/statements-to-interest.md`, while reusing the same scripts and references. Do not maintain separate workflow copies.
