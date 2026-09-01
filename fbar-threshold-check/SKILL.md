---
name: fbar-threshold-check
description: Check the $10,000 FBAR threshold from foreign account statements or reviewed account attestations. Use for reviewed preflight handoffs, extracted or reconstructed daily ledgers, certificate/maximum evidence, interval-aware multi-account aggregation, year-end FX conversion, and postflight verification.
---

# FBAR Threshold Check

Build account-by-account FBAR threshold evidence for one calendar year. Produce support artifacts and a yes/no threshold result; do not prepare FinCEN Form 114, file anything, or provide legal/tax advice.

## Required Workflow

Read `references/workflow.md` before analysis. It contains the preflight handoff, FBAR ledger review points, command sequence, FX dependency contract, and final-response shape. Read `references/fbar-source-notes.md` when explaining FBAR threshold wording, maximum account value, source limitations, or FX caveats. `<package-root>` below means the directory containing this SKILL.md.

## Skill Dependencies

Required companion skill: `statement-intake-preflight`. Use it first for shared PDF intake with `--scope one-account --require-institution`; `extract-account` requires either its clean preflight JSON or a separate reviewed-handoff JSON created after explicit user review, and verifies the ordered PDF byte-size and SHA-256 fingerprints before and after parsing. This skill keeps only FBAR balance, FX, confirmation, and aggregation logic here.

FX workpaper dependency for non-USD accounts: `get-year-end-fx-rate`. Use its retained year-end `workpaper.json` for FBAR-style conversion; do not use yearly-average workpapers from `get-yearly-fx-rate`.

Process one account at a time. First use the separate `statement-intake-preflight` skill with `--scope one-account --require-institution` and review its JSON/CSV:

```bash
python3 -B "<preflight-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --require-institution \
  --out work/statement-preflight.json
```

If the preflight status is `ready-for-domain-extraction`, extract the account ledger with that JSON. If it is `review-required`, stop for user review. Structural `stop` gates require corrected PDFs; otherwise create a reviewed handoff that acknowledges every review gate after the user confirms it. Schema 1.3 adds compact period headers, split account-label binding, explicit date roles, certificate issue dates, account migration/reissue attestations, and month-resolution account openings while preserving schema 1.2 handoffs. Every direct or inferred period interval and reviewed resolution must remain bound to the unchanged source evidence and source-preflight hash. Generated-on and certificate-issued dates are metadata, never statement coverage. When the handoff has an ambiguous/unknown currency, unknown or incompletely linked account, opt-in issuer, or out-of-period metadata-date gate, it must contain the preflight skill's structured user resolution. Do not ask for a full account number by default or repeat a question already resolved in the handoff.

```bash
python3 -B "<preflight-root>/scripts/statement_intake_preflight.py" review-handoff \
  --input work/statement-preflight.json \
  --accept-gate ambiguous-dollar \
  --user-review-confirmed \
  --out work/statement-preflight-reviewed.json
```

Then pass the ready preflight or reviewed handoff into extraction:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

For a reviewed handoff, replace `work/statement-preflight.json` with `work/statement-preflight-reviewed.json`.

Review the account JSON and CSV before confirming. The shared geometry parser recognizes column roles before amounts, supports full `DD/MM/YYYY` and ISO timestamps, and selects only the final `Balance`/`Saldo` column across currencies. It refuses ambiguous full numeric dates unless the source-bound period resolves them. A `Date ... Debit Credit` table or an explicitly signed `Fecha ... Valor`/`Date ... Amount` table can reconstruct daily balances only when a labelled opening balance plus every source-bound movement reconcile to the labelled closing balance; a zero-movement period also requires equal opening and closing checkpoints. The result stays `diagnostic-reconstructed`, never formal. Any unsigned/omitted dated row, unsupported visible money table, or failed reconciliation sets `status: parser-coverage-defect`; do not confirm it.

The CSV is a row-review artifact with one row per calendar day. Each row has an `evidence_class`, including formal transaction observations, diagnostic reconstructed values, user-attested values, carried-forward values, or missing opening coverage. The top-level `evidence_status`, `integrity_status`, `value_model`, and `time_alignment` make the distinction explicit. `unresolved-no-observations` is non-formal and cannot be confirmed. For reconstructed same-day candidates, the date-level row keeps the maximum for threshold safety while later dates carry the final reconciled balance. Do not treat a period-end summary, carried value, or attestation as formal statement extraction. Stop for user review on ambiguous amounts, missing coverage, carry gaps, conflicting same-day candidates, low-confidence rows, parser defects, or failed reconciliation.

When a labelled closing amount appears on a page with exactly one re-verified statement period, the extractor may record it as a `period-end-summary` observation. Its JSON/CSV flag retains both the summary and exact period-end source references. It never fills intervening days, and it does not remove carry-gap review or confirmation gates.

For a source-corroborated or user-confirmed COP account with one re-verified page period, either the exact text header `Fecha Descripción Movimiento Tarjeta Débito Abono Saldo` or the compact PDF-column header `Fecha | Descripción | Saldo` permits whole-COP comma grouping only in the final `Saldo` column. The compact form also requires one left-column `DD/MM` date and exactly one monetary `Saldo` cell on the same visual row. This does not relax generic separator handling: a standalone `12,000` remains review-required.

Confirm only after the user has reviewed the account ledger:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --out work/account-1-confirmed.json
```

For non-USD accounts, use the separate `get-year-end-fx-rate` skill (preferred for FBAR-style conversion) to create the required workpaper, then pass its `workpaper.json`:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" confirm-account \
  --input work/account-1.json \
  --balances-confirmed \
  --fx-workpaper-json work/fx-year-end-proof/cop-2025-source/workpaper.json \
  --out work/account-1-confirmed.json
```

Important dependency guardrail: this skill consumes only `workpaper.json` files from `get-year-end-fx-rate` for non-USD accounts. Do not source, calculate, or override FX rates inside this skill. Do not use yearly-average workpapers from `get-yearly-fx-rate` for FBAR conversion.

When statements cannot supply a daily ledger but the user or preparer has reviewed a certificate, annual maximum, all-year zero assertion, or separate daily workpaper, use `attest-account` instead of forcing the extractor. Pass the original source PDFs and preflight, acknowledge every raw non-structural gate exactly, label the evidence `user-attested-daily`, `user-attested-zero`, or `user-attested-maximum`, and require explicit confirmation. These ledgers are confirmed but permanently non-formal. An undated annual maximum produces an interval-aware upper bound during aggregation rather than being assigned to an invented date.

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" attest-account \
  --pdf account-certificate.pdf \
  --tax-year 2025 \
  --preflight-json work/certificate-preflight.json \
  --accept-gate out-of-period-certificate-date \
  --evidence-class user-attested-maximum \
  --maximum-native 500 \
  --account-id account-1 \
  --institution "Confirmed institution" \
  --account-currency USD \
  --user-attestation-confirmed \
  --out work/account-1-attested.json
```

After each confirmed account, ask whether the user has another foreign account for the same year. When the user says there are no more accounts, aggregate:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" aggregate \
  --account-ledger work/account-1-confirmed.json work/account-2-confirmed.json \
  --packet-root . \
  --out outputs/fbar-2025-summary.json
```

The aggregate command writes:

- Final JSON decision file at `--out`.
- Final CSV beside the JSON unless `--csv` is supplied.
- Concise human PDF beside the JSON unless `--pdf` is supplied.
- Postflight integrity/provenance manifest beside the JSON unless `--postflight` is supplied.

`--packet-root` must be an existing directory containing every bound ledger, FX workpaper, and generated output. The manifest stores only safe paths relative to that root and records its own relative location, allowing `verify-packet` to infer the new root after the directory is copied. Without the option, aggregate selects the artifacts' common ancestor and refuses a filesystem-root-only packet.

Verify a retained packet after copying or before reuse:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" verify-packet \
  --summary outputs/fbar-2025-summary.json \
  --manifest outputs/fbar-2025-summary-postflight.json
```

Verification requires an exact binding set, rejects missing/replaced/duplicate/unsafe artifacts, validates nested FX proof, and recomputes converted ledger values, daily bounds, account maxima, and rounding results from the bound bytes. Treat any nonzero exit as an integrity failure; do not use the retained threshold result until the packet is rebuilt or the mismatch is resolved.

## Result Rules

Report both views separately:

- Daily threshold: whether combined USD account values exceeded `$10,000` on any day. When an undated maximum can change the answer, report `review-required` plus the lower/upper answers rather than forcing yes or no.
- FinCEN maximum-value view: whether aggregate converted account maximums exceeded `$10,000`.

The comparison is strictly greater than `$10,000`; exactly `$10,000` is not an exceedance. Report exact converted values separately from whole-dollar display values, plus any `time_alignment`, `rounding_policy`, evidence, integrity, and answer-sensitivity caveats in the summary.

If the daily threshold is exceeded, list the dates. If records are incomplete, say the reviewed records are insufficient for a confident daily-threshold answer instead of returning a false `no`.

## Runtime

`extract-account` still needs `pdfplumber` to parse balances from the reviewed PDFs; shared PDF readiness belongs to `statement-intake-preflight`. Check parser availability with:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" dependency-check
```

If it reports `pdfplumber missing` in Codex Desktop, call `load_workspace_dependencies` and rerun with the bundled Python executable. Otherwise, explain that extraction needs a Python runtime with `pdfplumber` and give one next step; `confirm-account`, `aggregate`, and `self-test` can still run without it.

`confirm-account`, `aggregate`, and `self-test` use only Python standard-library modules.

## Maintainer Checks

After changing this skill (not needed for normal use), run `self-test` plus the repo gatekeeper:

```bash
python3 -B "<package-root>/scripts/fbar_threshold_check.py" self-test
python3 -B "<package-root>/tests/run_postmortem_regressions.py"
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
claude plugin validate --strict "<package-root>"  # when Claude tooling is available
```

For changes to the `statement-intake-preflight` handoff, run
`tests/run_preflight_integration.py` from a checkout where the required sibling
skill is available; it uses real PDFs and skips cleanly without test-only
`reportlab` and `pdfplumber`.

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/fbar-threshold-check.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
