---
name: statement-intake-preflight
description: Preflight machine-readable bank statement PDFs before FBAR or interest workflows. Use for shared text, scope, account, institution, currency, and review-gate JSON checks.
---

# Statement Intake Preflight

Preflight one bank statement set before a downstream statement workflow. Produce a review JSON and CSV that `fbar-threshold-check` and `statements-to-interest` can consume; do not extract interest totals, build FBAR daily ledgers, choose FX rates, prepare forms, or give tax/legal advice.

## Required Workflow

Read `references/workflow.md` before analysis. It defines the intake gates, command sequence, JSON handoff contract, and final-response shape.

Run the deterministic preflight:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --out work/statement-preflight.json
```

Use `--scope one-account` before `fbar-threshold-check`. Use `--scope one-institution` before `statements-to-interest`.

The command writes:

- JSON handoff at `--out`.
- Review CSV beside the JSON unless `--csv` is supplied.

Review the JSON and CSV before passing the JSON to downstream tools. Stop for user review when the output reports non-PDF files, missing files, scanned/image-only or low-text PDFs, duplicate inputs, mixed years, mixed currencies, ambiguous `$`, possible mixed accounts, possible mixed institutions, or unknown currency/account context required by the requested downstream workflow. `references/workflow.md` has the full gate catalog with severities.

## Downstream Handoff

Pass the preflight JSON into downstream extraction after review:

```bash
python3 "<fbar-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

```bash
python3 "<interest-root>/scripts/statements_to_interest.py" extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --preflight-json work/statement-preflight.json \
  --out work/interest-analysis.json
```

Downstream skills must reject preflight JSON when its skill name, tax year, scope, or PDF set does not match the extraction run. Preflight warnings remain review hints; the downstream skill still owns its domain-specific review gates.

## Runtime

Requires Python 3.11 or newer. `preflight` also requires machine-readable PDFs and `pdfplumber`. Check availability with:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" dependency-check
```

In Codex Desktop, if the default `python3` reports `pdfplumber missing`, call `load_workspace_dependencies` and rerun with the bundled Python executable before treating the dependency as unavailable.

`self-test` uses only Python standard-library modules. `smoke-test` uses `pdfplumber` and `reportlab` to exercise the real PDF extraction path.

## Maintainer Checks

After changing this skill, run:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" dependency-check
python3 "<package-root>/scripts/statement_intake_preflight.py" self-test
python3 "<package-root>/scripts/statement_intake_preflight.py" smoke-test
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
claude plugin validate --strict "<package-root>"  # when Claude tooling is available
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/statement-intake-preflight.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
