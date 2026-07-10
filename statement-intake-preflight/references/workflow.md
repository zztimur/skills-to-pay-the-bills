# Statement Intake Preflight Workflow

Use this workflow whenever the skill is triggered. Keep all work local unless the user explicitly asks otherwise.

## 1. Intake

Collect or infer:

- Calendar/tax year.
- Statement PDF paths.
- Scope: `one-account` for FBAR account-ledger extraction, or `one-institution` for interest-income extraction.
- Work folder, defaulting to `work/`.

Before running preflight, verify the user is asking for early statement review, not a completed FBAR or interest-income result. If the user wants final domain artifacts, run this skill first, then hand the resulting JSON to the downstream skill.

## 2. Runtime Setup

Requires Python 3.11 or newer (the script uses `datetime.UTC`). Use a Python runtime with `pdfplumber`:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" dependency-check
```

In Codex Desktop, if plain `python3` reports `pdfplumber missing`, call `load_workspace_dependencies` and retry the command with the bundled Python executable. If no available runtime has `pdfplumber`, stop before preflight and report the missing dependency. `dependency-check` exits non-zero and prints `pdfplumber missing` both when the package is absent and when it is installed but fails to import (a broken native dependency), rather than crashing. Do not OCR scanned/image-only PDFs in v1.

## 3. Run Preflight

Run:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --scope one-account \
  --out "work/statement-preflight.json"
```

Use `--scope one-account` before `fbar-threshold-check`. Use `--scope one-institution` before `statements-to-interest`.

The command writes:

- JSON handoff at `--out`.
- Review CSV beside the JSON unless `--csv` is supplied.

## 4. Review Gate

Open the JSON and CSV before continuing. Confirm:

- `statement_files` are the exact PDFs expected for the downstream run. Each carries a `content_sha256` of its bytes, so a downstream run can pin that it parsed the same files preflight saw.
- `tax_year` and `scope` match the intended downstream workflow.
- The files have enough machine-readable text.
- `profile.statement_titles` and `coverage_hints.detected_periods` look like the requested year.
- `currency.code` is usable, or unresolved currency is explicitly handled downstream.
- `account_hints` describe one account when scope is `one-account`.
- `institution_hints` describe one institution when scope is `one-institution`.

Stop for user review when any `review_gates` entry appears. Each gate carries a `severity`:

- `stop` — a structural problem with an input file; it cannot be used as-is. Fix or drop the file before proceeding.
- `review` — the set parsed, but a scope or quality assumption needs a human to confirm before downstream extraction.

Any gate, of either severity, sets `status` to `review-required`.

Complete gate catalog:

| Code | Severity | Meaning |
|---|---|---|
| `non-pdf-input` | stop | A supplied file is not a PDF. |
| `missing-file` | stop | A supplied file was not found. |
| `unreadable-pdf` | stop | A `.pdf` could not be parsed as a PDF. |
| `low-text-pdf` | stop | Machine-readable text is below threshold (scanned/image-only; out of scope for v1). |
| `duplicate-input` | review | The same statement file (same resolved path) was supplied more than once. |
| `duplicate-content` | review | Byte-identical statements were supplied under different names. |
| `mixed-years` | review | A detected statement year falls outside the requested tax year. Copyright/legal years are excluded even with an intervening month, a range, or a comma/space-separated list (`© 2019`, `© May 2019`, `© 2019-2024`, `© 2019, 2020, 2021`), as are bare or month-only heritage/account-open years (`since 1904`, `Customer since March 2015`); a period year carrying a numeric date (`since 2025-04-01`) is kept. |
| `unknown-year-coverage` | review | No statement year was detected; verify the periods manually. |
| `ambiguous-dollar` | review | `$` appears with no unambiguous ISO code or currency name. |
| `unknown-currency` | review | No account currency marker was found. |
| `mixed-currencies` | review | More than one currency was confirmed. |
| `possible-mixed-accounts` | review (`one-account`) | More than one account identifier was found. |
| `unknown-account` | review (`one-account`) | No account identifier was found. |
| `possible-mixed-institutions` | review (`one-institution`) | More than one institution was found. |
| `unknown-institution` | review (`one-institution`) | No institution was found in early statement text. |

Detection is multilingual where it matters. Currency labels include the German `Währung` (`devise` and `valuta` are deliberately excluded: they collide with the English verb "devise" and with "value date"); negative amounts (accounting parens `(1.234,56)` and the European trailing minus `1.234,56-`) and footnoted codes (`USD¹`) still corroborate a currency; institution detection recognizes `-bank` compound brands (`Commerzbank`, `Rabobank`) when the line also carries banking/statement context, plus `Sparkasse`; account labels include `Konto`, `Kontonummer`, and `Compte`. `USD` is corroborated exactly like every other ISO code — via the `$`/`US$` symbol, a currency label, or an adjacent amount — with no bare-word shortcut, so an incidental "usd" substring or a lone card-FX disclosure line never confirms it.

Preflight is not a guarantee of complete coverage. It is an intake guardrail and handoff artifact.

## 5. Handoff Contract

Downstream tools should accept only JSON where:

- `skill` is `statement-intake-preflight`.
- `schema_version` is supported.
- `tax_year` matches the extraction command.
- `scope` matches the downstream workflow.
- The resolved PDF set matches the extraction command.

`status` is `ready-for-domain-extraction` (no gates) or `review-required` (one or more gates). `currency.candidates` lists the confirmed ISO codes that decide `currency.code` (an adjacent amount or a currency label corroborated each one); `currency.weak_candidates` lists uncorroborated all-caps tokens surfaced for the human but deliberately not used to decide `currency.code`.

Downstream tools should import preflight warnings and profile hints into their own JSON output, but they still own domain-specific parsing and review gates.

## 6. Final Response

After preflight, return:

- JSON path.
- CSV path.
- Status.
- Review gates and warnings.
- Suggested downstream command, if the user is continuing to FBAR or interest extraction.

The command exits `0` by default; read the JSON for `status` and `review_gates`. Pass `--exit-nonzero-on-review` to make a `review-required` result exit `3` for scripted callers. Bad arguments, output-path collisions, and a missing `pdfplumber` dependency exit `2`.

A path (plain or as a Markdown link) is only clickable/downloadable when the chat client has direct filesystem access to this machine, true for a local desktop session but not for a hosted/remote session (for example, Claude Code on the web) where the user's browser cannot reach this container's filesystem. When running in such a session and the user needs to review the JSON/CSV directly, also deliver them using the host's file-delivery capability (for example, Claude Code's `SendUserFile` tool).

Do not say any FBAR or tax-support packet is complete after preflight alone.

## 7. Maintainer Smoke Test

After script changes, run `self-test` for deterministic parser logic and `smoke-test` with a dependency-backed Python runtime to verify the real PDF extraction path.
