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

- `statement_files` are the exact PDFs expected for the downstream run, in that order. Each carries `content_bytes` and `content_sha256`, so a downstream run can reject substituted, reordered, or duplicated files.
- `tax_year` and `scope` match the intended downstream workflow.
- The files have enough machine-readable text.
- `coverage_hints.statement_period_years`, source-referenced `period_intervals`, and any `contextual_date_evidence` support the requested year. A labelled prior-year opening balance, or an exact prior December 31 opening boundary ending in the requested year, is contextual evidence rather than a second statement period.
- `coverage_hints.period_coverage_review` has no possible internal, leading, or trailing statement-period gap. Its period labels are intake hints only; they do not prove complete transaction or balance coverage.
- `currency.code` is usable, or weak/unknown currency received the specific ISO confirmation below.
- `account_hints` describe one account when scope is `one-account`, or the user confirmed one-account scope without supplying an account number.
- `institution_hints` describe one institution when scope is `one-institution`.

Stop for user review when any `review_gates` entry appears. Each gate carries a `severity`:

- `stop` — a structural problem with an input file; it cannot be used as-is. Fix or drop the file before proceeding.
- `review` — the set parsed, but a scope or quality assumption needs a human to confirm before downstream extraction.

Any gate, of either severity, sets `status` to `review-required`.

### Ask only when the evidence requires it

Do not turn every preflight observation into a user question. Use the matching
wording below only when the relevant evidence is unresolved; otherwise state
the corroborated result and proceed.

- Confident currency (`currency.code` is one ISO code backed by `currency.candidates`): do **not** ask. Say: “Currency is corroborated as `COP` from the statement text. Proceeding with `COP`; no currency confirmation is needed.” Substitute the actual ISO code.
- Bare `$` or other weak/unknown currency evidence (`ambiguous-dollar` or `unknown-currency`): ask: “We could not corroborate the account currency from the statement text. Please confirm the ISO currency code (for example, `COP`).” Do not infer USD from `$`.
- Contextual prior-year date: if the statement period is clearly inside the requested year and its coverage is otherwise clear, explain it without asking: “We found `2024-12-31` as a prior-year opening boundary. The detected statement period is 2025, so this date is contextual and does not change the statement year.” This includes a period that begins exactly on the prior December 31 and ends in the requested year. If coverage is unclear, ask: “We found `2024-12-31` in the Q1 statement. It appears to be a prior-year opening balance, while the statement period appears to be 2025. Please confirm that all supplied statements cover 2025 and that this date is contextual.”
- Missing account identity in `one-account` scope (`unknown-account`): ask: “We could not extract a reliable account identifier from these statements. Please confirm that the supplied PDFs represent one account. You do not need to provide the account number.”
- Missing issuer identity in `one-institution` scope (`unknown-institution`): ask: “We could not extract a reliable institution name from these statements. Please confirm the institution name shown on the statements.”

Do not use a user response to override `mixed-years`, a structural `stop` gate,
or corroborated contradictory currency/account evidence. Record accepted review
input only through the separate reviewed-handoff resolution fields.

Complete gate catalog:

| Code | Severity | Meaning |
|---|---|---|
| `non-pdf-input` | stop | A supplied file is not a PDF. |
| `missing-file` | stop | A supplied file was not found. |
| `unreadable-pdf` | stop | A `.pdf` could not be parsed as a PDF. |
| `low-text-pdf` | stop | Machine-readable text is below threshold (scanned/image-only; out of scope for v1). |
| `duplicate-input` | review | The same statement file (same resolved path) was supplied more than once. |
| `duplicate-content` | review | Byte-identical statements were supplied under different names. |
| `mixed-years` | review | A detected statement-period year falls outside the requested tax year. Copyright/legal years are excluded even with an intervening month, a range, or a comma/space-separated list (`© 2019`, `© May 2019`, `© 2019-2024`, `© 2019, 2020, 2021`), as are bare or month-only heritage/account-open years (`since 1904`, `Customer since March 2015`); a period year carrying a numeric date (`since 2025-04-01`) is kept. A labelled opening/prior balance and an exact prior December 31 opening boundary are recorded separately as contextual evidence. |
| `unresolved-year-evidence` | review | An out-of-period year could not be classified as a statement period or labelled contextual evidence. Review the source before downstream extraction. |
| `unknown-year-coverage` | review | No statement year was detected; verify the periods manually. |
| `possible-missing-statement-period` | review | Two or more source-referenced period labels leave an internal, leading, or trailing gap in the requested year. Obtain missing statements or review the source periods. |
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
- The resolved PDF sequence matches the extraction command.
- Every expected PDF has a positive `content_bytes` and lower-case SHA-256 `content_sha256` fingerprint.

`status` is `ready-for-domain-extraction` (no gates) or `review-required` (one or more gates). `currency.candidates` lists the confirmed ISO codes that decide `currency.code` (an adjacent amount or a currency label corroborated each one); `currency.weak_candidates` lists uncorroborated all-caps tokens surfaced for the human but deliberately not used to decide `currency.code`.

For `fbar-threshold-check` specifically:

- Pass a `ready-for-domain-extraction` JSON directly.
- Never pass a raw `review-required` JSON to extraction.
- A `stop` gate requires corrected inputs and a fresh preflight.
- After the user reviews every `review` gate, create a separate handoff:

```bash
python3 "<package-root>/scripts/statement_intake_preflight.py" review-handoff \
  --input "work/statement-preflight.json" \
  --accept-gate "ambiguous-dollar" \
  --confirm-currency COP \
  --confirm-one-account \
  --user-review-confirmed \
  --out "work/statement-preflight-reviewed.json"
```

Repeat `--accept-gate` for every listed review code. Add only the resolution flags required by the source gates:

- `--confirm-currency COP` accepts one supported ISO 4217 code only when preflight could not corroborate a currency (`ambiguous-dollar` or `unknown-currency`). It cannot override a confirmed or mixed currency set.
- `--confirm-one-account` records a one-account assertion without collecting an account number. It is required for `unknown-account` or `possible-mixed-accounts` review gates.
- `--confirm-institution "Example Bank"` records a one-institution issuer name only for an `unknown-institution` review gate. It cannot override `possible-mixed-institutions` evidence.
- `--confirm-statement-year 2025` records the requested tax year when year coverage is unknown or needs review. Pair it with `--classify-contextual-year 2024` only for extracted contextual/unresolved prior-year evidence. A genuine `mixed-years` statement-period gate cannot be overridden; correct the year or statement set and rerun preflight.

The reviewed handoff preserves raw preflight evidence unchanged. Its separate `user_resolutions` section contains confirmation state, accepted gates, the source-preflight SHA-256, and only the user-provided year/currency/account-scope/institution assertions. FBAR extraction verifies the source fields, then rechecks the ordered PDF byte-size and SHA-256 fingerprints before and after parsing. `statements-to-interest` can consume a reviewed `one-institution` handoff only when it verifies the same source binding and typed institution resolution. Changed, duplicate, reordered, or legacy-unfingerprinted files require a fresh preflight (and, where applicable, a new reviewed handoff).

Downstream tools should retain preflight warnings, profile hints, and coverage hints (including period and contextual-date evidence) in their own JSON output, but they still own domain-specific parsing and review gates.

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
