---
name: statement-intake-preflight
description: Preflight machine-readable bank statements and account certificates before FBAR or interest workflows. Use for shared text, scope, account, institution, currency, statement-period/date-role checks, certificate metadata, review gates, and source-bound downstream handoffs.
---

# Statement Intake Preflight

Preflight one bank statement set before a downstream statement workflow. Produce a review JSON and CSV that `fbar-threshold-check` and `statements-to-interest` can consume; do not extract interest totals, build FBAR daily ledgers, choose FX rates, prepare forms, or give tax/legal advice.

## Required Workflow

Read `references/workflow.md` before analysis. It defines the intake gates, command sequence, JSON handoff contract, and final-response shape.

Run the deterministic preflight:

```bash
python3 -B "<package-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --out work/statement-preflight.json
```

Use `--scope one-account` before `fbar-threshold-check`. FBAR adds `--require-institution` so its one-account handoff also proves issuer evidence or requires a typed reviewed confirmation. Use `--scope one-institution` before `statements-to-interest`.

The command writes:

- JSON handoff at `--out`.
- Review CSV beside the JSON unless `--csv` is supplied. Period intervals preserve both start and end source references; the aggregated JSON binds each endpoint to its source file, page, and extracted line. A `direct-anchor` interval also records its period-header and date-anchor references. A `medium` `inferred-chain` interval is derived only from exact source-linked account evidence and directly adjacent resolved periods. `unresolved_periods` holds stable opaque IDs for source-labelled ranges that need one exact year confirmation.

Review the JSON and CSV before passing the JSON to downstream tools. Schema 1.3 recognizes compact month-name headers such as `1 SEP - 30 SEP`, binds a plausible identifier on the immediate next line after an explicit account label, records parser hints, and classifies dates by role: statement period, movement, opening boundary, generated-on, or certificate-issued. Certificate dates are metadata and never manufacture tax-year coverage. Stop for the review gates listed in `references/workflow.md`, including an out-of-period certificate issue date. A graphic logo or filename is never automatic issuer evidence. Never ask for a full account number by default.

## Downstream Handoff

Pass a `ready-for-domain-extraction` JSON directly into downstream extraction. For FBAR, and for interest extraction when its typed reviewed-handoff contract applies, turn a `review-required` preflight with no structural `stop` gates into a separate reviewed handoff after the user confirms every review gate:

```bash
python3 -B "<package-root>/scripts/statement_intake_preflight.py" review-handoff \
  --input work/statement-preflight.json \
  --accept-gate ambiguous-dollar \
  --confirm-currency COP \
  --confirm-one-account \
  --user-review-confirmed \
  --out work/statement-preflight-reviewed.json
```

Repeat `--accept-gate` for every code shown in `review_gates`. Add resolution flags only when the corresponding gate requires them: `--confirm-period-year PERIOD_ID=TAX_YEAR`, `--confirm-currency ISO`, `--confirm-one-account`, `--confirm-institution "Name"`, `--confirm-statement-year TAX_YEAR` with any `--classify-contextual-year PRIOR_YEAR`, `--confirm-generated-on-date YYYY-MM-DD`, or `--confirm-certificate-issued-date YYYY-MM-DD`. An optional `--confirm-account-migrated-or-reissued-on YYYY-MM-DD` must exactly match the confirmed certificate date and include a specific `--migration-note`; it stays `user-confirmed-unverified`. For one continuous leading gap, use either exact `--confirm-account-opened-on YYYY-MM-DD` or month-resolution `--confirm-account-opened-month YYYY-MM`, never both. The exact date must equal the first source-supported period start; the month must match that period's month. Neither form edits raw coverage or excuses an internal/trailing gap. Never create a handoff for a structural `stop` gate or use reviewer input to override conflicting source evidence.

```bash
python3 -B "<fbar-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

For a reviewed handoff, replace `work/statement-preflight.json` with `work/statement-preflight-reviewed.json`.

```bash
python3 -B "<interest-root>/scripts/statements_to_interest.py" extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --preflight-json work/statement-preflight.json \
  --out work/interest-analysis.json
```

FBAR extraction rejects absent, raw review-required, stale, mismatched, or incomplete handoffs. Both downstream skills retain schema 1.2 compatibility and validate schema 1.3 source bindings, exact interval provenance, metadata-date resolutions, account-opening resolution, accepted gates, and ordered PDF fingerprints. Preflight warnings and gate resolutions remain visible downstream; domain-specific parsers still own balance or interest evidence.

## Runtime

Requires Python 3.11 or newer. `preflight` also requires machine-readable PDFs and `pdfplumber`. Check availability with:

```bash
python3 -B "<package-root>/scripts/statement_intake_preflight.py" dependency-check
```

In Codex Desktop, if the default `python3` reports `pdfplumber missing`, call `load_workspace_dependencies` and rerun with the bundled Python executable before treating the dependency as unavailable.

`self-test` uses only Python standard-library modules. `smoke-test` and `tests/run_pressure_suite.py` use `pdfplumber` and `reportlab` to exercise the real PDF extraction path.

## Maintainer Checks

After changing this skill, run:

```bash
python3 -B "<package-root>/scripts/statement_intake_preflight.py" dependency-check
python3 -B "<package-root>/scripts/statement_intake_preflight.py" self-test
python3 -B "<package-root>/scripts/statement_intake_preflight.py" smoke-test
python3 -B "<package-root>/tests/run_pressure_suite.py"  # real-PDF adversarial corpus; skips without reportlab
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
claude plugin validate --strict "<package-root>"  # when Claude tooling is available
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/statement-intake-preflight.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
