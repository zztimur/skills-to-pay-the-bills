---
name: statement-intake-preflight
description: Preflight machine-readable bank statement PDFs before FBAR or interest workflows. Use for shared text, scope, account, institution, currency, review-gate checks, and reviewed downstream handoffs.
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

Use `--scope one-account` before `fbar-threshold-check`. FBAR adds `--require-institution` so its one-account handoff also proves issuer evidence or requires a typed reviewed confirmation. Use `--scope one-institution` before `statements-to-interest`.

The command writes:

- JSON handoff at `--out`.
- Review CSV beside the JSON unless `--csv` is supplied. Period intervals preserve both start and end source references; the aggregated JSON binds each endpoint to its source file, page, and extracted line.

Review the JSON and CSV before passing the JSON to downstream tools. Stop for user review when the output reports non-PDF files, missing files, scanned/image-only or low-text PDFs, duplicate inputs, mixed statement periods, unresolved out-of-period year evidence, out-of-period generated-on metadata, possible missing statement periods, mixed currencies, ambiguous `$`, possible mixed accounts, or issuer evidence required by the requested downstream workflow. A graphic logo or a filename can support a user's visual review, but is never automatic issuer evidence: do not OCR, infer, or treat it as a resolved institution. Ask only the gate-specific question in `references/workflow.md`: do not ask about a corroborated currency, a clearly contextual prior-year date, or an extracted account identifier. Never ask for a full account number by default. `references/workflow.md` has the full gate catalog, severities, and ready-to-use wording.

## Downstream Handoff

Pass a `ready-for-domain-extraction` JSON directly into downstream extraction. For FBAR, and for interest extraction when its typed reviewed-handoff contract applies, turn a `review-required` preflight with no structural `stop` gates into a separate reviewed handoff after the user confirms every review gate:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" review-handoff \
  --input work/statement-preflight.json \
  --accept-gate ambiguous-dollar \
  --confirm-currency COP \
  --confirm-one-account \
  --user-review-confirmed \
  --out work/statement-preflight-reviewed.json
```

Repeat `--accept-gate` for every code shown in `review_gates`. Add resolution flags only when the corresponding gate requires them: `--confirm-currency ISO` for ambiguous/unknown currency, `--confirm-one-account` without supplying an account number when account identity is unknown, conflicting, or not source-linked across every supplied PDF, `--confirm-institution "Name"` for an unknown one-institution issuer or an issuer gate from opt-in FBAR `one-account --require-institution` intake, `--confirm-statement-year TAX_YEAR` with any `--classify-contextual-year PRIOR_YEAR` values, and `--confirm-generated-on-date YYYY-MM-DD` once for each source-labelled out-of-period generated-on date. The handoff records these separately from the raw preflight evidence. Never create a handoff for a structural `stop` gate or use reviewer input to override a genuine mixed statement period or conflicting currency/account evidence. The opt-in FBAR issuer confirmation can select one typed institution when issuer evidence is absent or conflicting; the original evidence and review gate remain visible.

```bash
python3 "<fbar-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

For a reviewed handoff, replace `work/statement-preflight.json` with `work/statement-preflight-reviewed.json`.

```bash
python3 "<interest-root>/scripts/statements_to_interest.py" extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --preflight-json work/statement-preflight.json \
  --out work/interest-analysis.json
```

FBAR extraction rejects absent, raw review-required, stale, mismatched, or incomplete handoffs. For an opt-in issuer gate, it validates and uses the typed institution resolution and rejects a conflicting `--institution` override. `statements-to-interest` accepts a reviewed `one-institution` handoff only when its typed institution, currency, year, and generated-on resolutions validate against the unchanged source preflight. Both downstream skills verify a generated-on resolution against the exact date and source reference; it remains metadata, not statement-period coverage. Preflight warnings and gate resolutions remain visible to downstream skills, which still own domain-specific parsing and review gates.

## Runtime

Requires Python 3.11 or newer. `preflight` also requires machine-readable PDFs and `pdfplumber`. Check availability with:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" dependency-check
```

In Codex Desktop, if the default `python3` reports `pdfplumber missing`, call `load_workspace_dependencies` and rerun with the bundled Python executable before treating the dependency as unavailable.

`self-test` uses only Python standard-library modules. `smoke-test` and `tests/run_pressure_suite.py` use `pdfplumber` and `reportlab` to exercise the real PDF extraction path.

## Maintainer Checks

After changing this skill, run:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" dependency-check
python3 "<package-root>/scripts/statement_intake_preflight.py" self-test
python3 "<package-root>/scripts/statement_intake_preflight.py" smoke-test
python3 "<package-root>/tests/run_pressure_suite.py"  # real-PDF adversarial corpus; skips without reportlab
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
claude plugin validate --strict "<package-root>"  # when Claude tooling is available
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/statement-intake-preflight.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
