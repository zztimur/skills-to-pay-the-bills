# Statements To Interest Workflow

Use this workflow whenever the skill is triggered. Keep all work local unless the user explicitly asks for otherwise.

Keep raw statement evidence in local JSON/CSV artifacts. The generated PDF redacts emails, IBANs, and labelled account-like identifiers including dotted formats by default, while retaining dates and monetary amounts. Use redacted evidence snippets plus page citations.

## Contents

1. Intake
2. Runtime Setup
3. Preflight
4. Extraction
5. Review The Extracted Results
6. FX Decision
7. Report Verification
8. Final Response
9. Troubleshooting
10. Maintenance Checks

## 1. Intake

Collect or infer these inputs:

- Statement PDF paths.
- Tax year.
- Institution name visible in the PDFs or clearly represented by the file paths.
- Ready `statement-intake-preflight` JSON, or a valid reviewed handoff, for `--scope one-institution`.
- Account currency only if preflight/extraction leaves it unresolved and the user or statement evidence can confirm it.
- Output folder, defaulting to `outputs/`.
- Work folder for JSON/CSV intermediates, defaulting to `work/`.

Before running this skill's extraction:

- Use `statement-intake-preflight` to verify shared statement intake scope: one institution, one tax year, one currency bucket, readable PDFs, and institution/currency hints.
- Treat `statement-intake-preflight` as a required companion skill, not optional setup. If it is not installed or discoverable, stop before extraction and ask the user to install or run it.
- Verify only the interest-specific intent here: the user wants an interest-income worksheet or tax-support packet, not official tax filing.

If preflight reports mixed banks, unrelated account providers, mixed years, mixed currencies, low/no text, or ambiguous currency, resolve that in the preflight step before this skill extracts interest rows.

## 2. Runtime Setup

Use a Python runtime with `pdfplumber`, `reportlab`, and `pypdf`.

In Codex desktop:

1. Call `load_workspace_dependencies`.
2. Use the bundled Python path it returns when system Python is missing dependencies.
3. Check dependencies before analysis:

```bash
python -c 'import pdfplumber, reportlab, pypdf; print("deps ok")'
```

If dependencies are unavailable, stop before analysis and tell the user which package is missing. The shared PDF text/readiness gate is owned by `statement-intake-preflight`; this runtime check is only for the downstream parser and PDF writer.

## 3. Preflight

Create the work/output folders before running commands:

```bash
mkdir -p work outputs
```

Run the shared statement preflight with `--scope one-institution`:

```bash
python3 "<preflight-root>/scripts/statement_intake_preflight.py" preflight \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --scope one-institution \
  --out "work/statement-preflight.json"
```

Review the preflight JSON and CSV. Pass a `ready-for-domain-extraction` JSON directly. For a non-structural `review-required` result, create a reviewed handoff only after the user confirms every listed gate and supplies any required typed resolution. `statements-to-interest` accepts a reviewed handoff only for a `one-institution` scope when its source JSON and SHA-256 remain unchanged, every gate is acknowledged, and its year, currency, and institution resolutions validate. It never accepts structural stops, genuine `mixed-years`, or `possible-mixed-institutions` evidence. Pass the same ordered `--pdf` paths to extraction: the handoff binds each normalized path, byte size, and lower-case SHA-256. The extractor verifies those fingerprints before reading the PDFs and immediately after all reads, so legacy no-fingerprint, reordered, or changed PDFs fail cleanly. Resolve the intake issue through preflight rather than bypassing it with an ad hoc flag. Do not duplicate those shared checks in this skill; preflight owns PDF readability, institution/year/currency scope, ambiguous `$`, and account/institution hints. Preflight does not replace interest-row extraction; it only standardizes the intake handoff.

## 4. Extraction

Run extraction:

```bash
python "<package-root>/scripts/statements_to_interest.py" extract \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --institution "Example Bank" \
  --account-currency COP \
  --preflight-json "work/statement-preflight.json" \
  --out "work/example-bank-2025-interest-analysis.json"
```

The script writes:

- Analysis JSON at `--out`.
- Review CSV beside the JSON unless `--csv` is supplied. This is for row inspection; do not make CSV the user-facing deliverable unless the user asks for it.
- `institution_profile` with institution name, account currency, statement titles, detected periods, institution-label source, and statement count.
- `preflight` summary with source SHA-256 digest and `verified_statement_files` containing the verified normalized paths, byte sizes, and SHA-256 digests; the script requires `--preflight-json`, and rejects raw non-ready/gated results, mismatched tax year, institution, scope, or ordered PDF sequence. A valid reviewed one-institution handoff remains source-bound and carries its accepted review gates and typed resolutions.
- `source_pdf_summary` and per-file content byte sizes and SHA-256 digests for local audit traceability.

`report` accepts only the current extraction contract: it reloads the ready preflight artifact and rechecks its digest plus every source PDF byte size and SHA-256 digest. If those artifacts changed or are missing, re-run extraction rather than editing the analysis JSON.

Each `excluded_candidates` item records whether it is `clear-non-interest` or `ambiguous`. Clear exclusions such as withholding remain visible in the support packet but do not block a report. Any ambiguous candidate makes the analysis `review-required`, even when other interest rows were counted.

Use `--account-currency` only when the preflight artifact or statement evidence confirms the currency and the interest extractor cannot infer it. A reviewed preflight currency resolution takes precedence and conflicts with a different CLI value. Do not treat `$` alone as proof of USD; unresolved `$` belongs back in preflight before reporting.

## 5. Review The Extracted Results

Open the JSON and review CSV before generating a report. The CSV is an internal audit/review aid; the final user-facing artifact should be the PDF packet. Review these JSON fields:

- `preflight`: ready intake artifact summary, including `verified_statement_files`.
- `institution_profile`: consistency with the ready preflight artifact.
- `rows`: counted interest rows.
- `totals.row_count`: counted row count.
- `totals.foreign_total_by_currency`: source-currency totals.
- `warnings`: script-level review flags.
- `excluded_candidates`: interest-like lines excluded from totals, including their classification and `requires_review` flag.
- `status` and `review_gates`: extraction readiness. Any ambiguous excluded candidate produces `review-required`, which blocks a packet even when rows were counted. `zero-interest-confirmation-required` needs explicit preparer confirmation.

For each counted row, check:

- Date is inside the requested tax year.
- Description is genuinely interest income or credited interest.
- `amount_foreign` is the interest amount, not the balance.
- Currency is expected and not `UNKNOWN`.
- Rows with `$` symbols use the account currency when statement text identifies the account currency.
- Confidence and notes do not require user confirmation.

Stop before reporting when:

- Any row has `confidence` set to `low`.
- The notes say the transaction date is missing.
- There are ambiguous excluded interest-like candidates. Review every one. If all are non-interest, create a digest-bound resolution; if any is countable interest, correct the source or obtain better statement evidence and rerun extraction.
- Counted rows include unexpected currencies.
- The extracted total looks inconsistent with statement summaries.

If the extractor repeats a preflight-style warning about institution, year, PDF text, or currency scope, return to the preflight artifact and rerun preflight/extraction. Do not resolve that scope issue ad hoc here.

Do not add, remove, or edit rows by guess. If a row is missing or wrong, explain the evidence and ask for confirmation or better source data.

To record a reviewer decision that every ambiguous excluded candidate is non-interest, create a resolution bound to the exact analysis JSON:

```bash
python "<package-root>/scripts/statements_to_interest.py" resolve-exclusions \
  --input "work/example-bank-2025-interest-analysis.json" \
  --all-excluded-not-interest-confirmed \
  --reviewer-note "Preparer reviewed every excluded candidate and found no additional interest income." \
  --out "work/example-bank-2025-excluded-candidates-resolution.json"
```

Pass that file to `report` with `--excluded-candidates-resolution-json`. The command rejects a resolution if the analysis or excluded-candidate list has changed. Do not edit the analysis JSON to clear a gate.

If `totals.row_count` is zero:

- If ambiguous `excluded_candidates` exist, create the resolution above. A zero-interest PDF still also requires explicit preparer confirmation.
- If no interest-like evidence exists, a zero-interest PDF requires explicit preparer confirmation. Use `--zero-interest-confirmed` and a note explaining the review; the packet records that note.

## 6. FX Decision

If all counted rows are USD:

- Do not ask for an FX rate.
- Generate the report without `--fx-method`, `--fx-rate`, or `--fx-source`.

If counted rows are not USD:

1. Use the separate `get-yearly-fx-rate` skill to find a published yearly average and create a retained proof workpaper.
2. Pass that skill's `workpaper.json` into `statements_to_interest.py` with `--fx-workpaper-json`.
   The report verifies that the declared workpaper PDF and each retained source-proof file still exist and match their SHA-256 digests before it will create a packet.
3. Treat `get-yearly-fx-rate` as the conditional FX dependency for this path. Do not search for or validate published annual FX sources inside `statements-to-interest`; that is the dependency's job.
4. If `get-yearly-fx-rate` is unavailable or cannot produce a workpaper, stop before the PDF and ask the user to install/run it or provide a confirmed user/preparer custom rate.
5. Accept a user/preparer custom rate when preferred. Use `--fx-method user-rate`; `--fx-source` is optional for custom rates.
6. Show the user the workpaper rate, source, proof documents, direction, and resulting USD total before generating the PDF. Use `fx-prompt` to print the exact confirmation question when a workpaper is available. Ask: "Confirm this published yearly average rate, or send a custom rate to use instead."
7. Generate the non-USD PDF only after the user confirms the proposed yearly average workpaper, custom rate, or explicitly requested daily spot rates. Pass `--fx-rate-confirmed` and a non-empty concise `--fx-confirmation-note`.
8. Confirm rate direction:
   - Default is `foreign-per-usd`, matching IRS yearly average tables.
   - Use `--rate-direction usd-per-foreign` only when the supplied rate is USD per one foreign currency unit.
9. Use item-date spot rates only if the user or preparer explicitly asks for that method.

Daily-rate JSON proof metadata must be an object with `saved_file` and its matching SHA-256 digest. The referenced file is verified before previewing or reporting.

Yearly average confirmation prompt shape:

```text
I found/propose this published yearly average FX rate for <currency>/<tax year> using get-yearly-fx-rate:

Rate: <rate> <foreign currency> per 1 USD
Source: <source and retrieval date>
Proof: <workpaper.pdf and retained source proof paths>
Method: get-yearly-fx-rate published yearly average workpaper
Source-currency total: <amount>
USD total using this rate: <amount>

Confirm this published yearly average rate, or send a custom rate to use instead.
```

Generate that prompt with:

```bash
python "<package-root>/scripts/statements_to_interest.py" fx-prompt \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-workpaper-json "work/fx-rate-proof/cop-2025-source/workpaper.json"
```

If the user has not confirmed yet, stop here. Do not summarize the run as complete and do not foreground the CSV/JSON as the result. Tell the user the PDF will be generated immediately after they confirm the proposed rate or provide a custom rate.

Yearly average report example:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-workpaper-json "work/fx-rate-proof/cop-2025-source/workpaper.json" \
  --fx-rate-confirmed \
  --fx-confirmation-note "User confirmed the published yearly average rate" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

Custom-rate fallback example without an external source:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-method user-rate \
  --fx-rate 4200.00 \
  --rate-direction foreign-per-usd \
  --fx-rate-confirmed \
  --fx-confirmation-note "User supplied and confirmed a custom rate without an external source" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

If the user/preparer provides a custom-rate source, add `--fx-source "..."`. If no source is supplied, the script labels the rate as user/preparer supplied, notes that no independent source was provided, and adds a preparer-review warning to the PDF.

Daily spot override example:

```json
{
  "method": "posted-daily-spot",
  "source": "Banco de la Republica TRM series 1, retrieved 2026-07-02",
  "proof": {
    "saved_file": "work/fx-rate-proof/cop-2025-daily-source.html",
    "sha256": "<saved proof digest>"
  },
  "rate_direction": "foreign-per-usd",
  "rates": {
    "2025-01-03": "4410.50",
    "2025-09-03": "4016.94"
  }
}
```

```bash
python "<package-root>/scripts/statements_to_interest.py" fx-prompt \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-method posted-daily-spot \
  --fx-rates-json "work/example-bank-2025-fx-rates.json"
```

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --fx-method posted-daily-spot \
  --fx-rates-json "work/example-bank-2025-fx-rates.json" \
  --fx-rate-confirmed \
  --fx-confirmation-note "User requested and confirmed daily spot rates" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

USD report example:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

Resolved-exclusions report example:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --excluded-candidates-resolution-json "work/example-bank-2025-excluded-candidates-resolution.json" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

Confirmed zero-interest report example:

```bash
python "<package-root>/scripts/statements_to_interest.py" report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --zero-interest-confirmed \
  --zero-interest-confirmation-note "Preparer reviewed all supplied statements and confirmed no interest was credited." \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

## 7. Report Verification

After generating the PDF:

- Confirm the PDF exists and has at least one page.
- Confirm the extracted text includes `Foreign Bank Interest Support Packet`.
- Confirm the PDF text includes the expected USD total.
- Confirm warning/review flags are represented when present.
- Confirm the PDF does not expose full local source paths, full account-like identifiers (including IBANs and dotted labelled accounts), or raw emails; preserve transaction dates and monetary amounts; statement tables should use redacted filenames and page citations.

Use `pypdf` for a quick text check:

```bash
python -c 'from pypdf import PdfReader; p="outputs/example-bank-2025-interest-support-packet.pdf"; text="\n".join((page.extract_text() or "") for page in PdfReader(p).pages); print("Foreign Bank Interest Support Packet" in text, "USD" in text)'
```

## 8. Final Response

If FX confirmation is still pending, do not use a final-completion response. Ask the FX confirmation/custom-rate question directly and state that the support PDF is pending that answer.

After generating and verifying the PDF, return a concise summary with the PDF first:

- Support PDF path.
- Analysis JSON path.
- FX workpaper and source proof artifact paths from `get-yearly-fx-rate` when used.
- Interest CSV path only if the user asked for CSV or wants the audit artifact.
- Institution/profile details used in the packet, especially account currency and statement coverage.
- Row count.
- Source-currency total.
- USD total.
- Warnings, excluded candidates, or confirmation decisions.
- A reminder that this is a support worksheet, not an official IRS form or tax advice.

A path (plain or as a Markdown link) is only clickable/downloadable when the chat client has direct filesystem access to this machine, true for a local desktop session but not for a hosted/remote session (for example, Claude Code on the web) where the user's browser cannot reach this container's filesystem. When running in such a session, also deliver the support PDF, analysis JSON, and any FX workpaper/source proof artifacts using the host's file-delivery capability (for example, Claude Code's `SendUserFile` tool).

Example:

```text
Generated the support packet for Example Bank 2025.

Rows counted: 12
Source total: USD 59.55
USD total: USD 59.55

Files:
- PDF: ...
- JSON: ...

Review flags: institution label was found only in the file path; verify the PDFs are all from Example Bank.
```

## 9. Troubleshooting

| Symptom | Action |
|---|---|
| `pdfplumber is required` | Use bundled Codex Python or another environment with `pdfplumber`. |
| `reportlab is required` | Use bundled Codex Python or install/use an environment with `reportlab`. |
| `little machine-readable text` | Resolve the low-text gate in `statement-intake-preflight`; scanned/image-only PDFs are out of scope for the deterministic workflow. |
| Institution name not found | Resolve the institution hint in `statement-intake-preflight`, rerun to a ready preflight, then retry extraction with the best ready label. |
| Multiple currencies detected | Resolve the currency-bucket issue in `statement-intake-preflight`; if counted interest rows still show unexpected currencies, stop for user review. |
| `UNKNOWN` currency | Do not report until currency is confirmed through a ready preflight or an explicit user/preparer override. |
| `$` rows from a COP statement are labeled USD | Resolve ambiguous `$` in preflight and rerun extraction with `--account-currency COP` only after the currency is confirmed. |
| Non-USD report asks for FX | Run `get-yearly-fx-rate`, pass its `workpaper.json` to `fx-prompt`, ask the user to confirm it or provide a custom rate, then rerun `report` with `--fx-rate-confirmed`. |
| `get-yearly-fx-rate` is unavailable | Run `dependency-check` if needed, then stop before the PDF and ask the user to install/run the dependency or provide a confirmed user/preparer custom rate. |
| Only daily/monthly rates are available | Do not calculate an annual average yourself; use `get-yearly-fx-rate` only if it can produce a published annual workpaper, otherwise ask the user/preparer for a custom rate. |
| Different interest dates need different FX rates | Use `--fx-rates-json` only when the user or preparer explicitly requests daily spot rates. |
| Extracted amount looks like a balance | Do not report blindly; inspect evidence text and ask the user to confirm before editing source data or relying on the row. |

## 10. Maintenance Checks

After editing `scripts/statements_to_interest.py`, run:

```bash
python "<package-root>/scripts/statements_to_interest.py" self-test
python "<package-root>/tests/run_preflight_integration.py"
```

The canonical `workpaper-kit` exists only in the source repository. An installed package treats `scripts/_workpaper.py` as a read-only vendored dependency; do not try to edit or regenerate it there.

To verify the published-yearly-average FX dependency is discoverable:

```bash
python "<package-root>/scripts/statements_to_interest.py" dependency-check
```

Before shipping or reinstalling the skill, validate the package:

```bash
python -S /path/to/skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
```

If Claude plugin compatibility matters, also run:

```bash
claude plugin validate --strict "<package-root>"
```
