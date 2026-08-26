# Statement Intake Preflight

This skill exists for one specific bit of useful friction:

```text
Are these statement PDFs clean enough, scoped enough, and boring enough to hand to the real tax-support workflow?
```

The point is not to extract interest, rebuild daily balances, choose FX rates, or answer an FBAR question. The point is to stop at the front door and ask whether the statement set looks like one coherent thing: one year, one account or institution, readable text, plausible currency, and no obvious trap hiding behind a tidy filename.

Current package version: 1.5.0.

It leaves behind a JSON/CSV handoff so the next skill does not have to rediscover the same intake facts from scratch.

## The Rule

One statement set. One tax year. One intended downstream scope.

Use `one-account --require-institution` when the next step is `fbar-threshold-check`; that keeps the one-account scope while requiring source-backed issuer evidence or a reviewed typed institution selection. Use `one-institution` when the next step is `statements-to-interest`.

Do not OCR screenshots. Do not smooth over a mystery `$`. Do not pretend a folder name proves the bank, account, currency, or year. If the text layer is weak, the years are mixed, account hints multiply, or currency is fuzzy, the output should say that plainly before any downstream skill starts doing more expensive work.

A visible logo or filename can help a human review a likely institution, but it is not automatic issuer evidence.

This is support documentation, not a tax conclusion. A clean preflight means the input is ready for the next parser. It does not mean the account is complete, the interest total is right, or an FBAR threshold answer exists.

## When It Asks You Something

The skill does not ask for confirmation just because it noticed a date, dollar
sign, or account field. It proceeds when the evidence is corroborated. When a
human answer is needed, it uses these prompts:

- Weak currency evidence: “We could not corroborate the account currency from the statement text. Please confirm the ISO currency code (for example, `COP`).”
- Unresolved source-labelled period year: “This statement displays a December 1–31 period but no usable year. Please confirm whether this displayed period is 2025.” The handoff accepts only the exact opaque period ID shown by preflight.
- Inferred period year: “We derived the displayed statement period from exact source-linked account evidence and directly adjacent source periods. Please review the listed source references.” Accepting this gate preserves the derived dates; it does not invite a replacement year or interval.
- Contextual prior-year date with unclear period coverage: “We found `2024-12-31` in the Q1 statement. It appears to be a prior-year opening balance, while the statement period appears to be 2025. Please confirm that all supplied statements cover 2025 and that this date is contextual.”
- Out-of-period generated-on metadata: “We found `2026-12-31` as the date the document was generated, not as a statement period. Please confirm that exact displayed date. We will retain it as document metadata and will not use it for statement-year coverage.”
- Out-of-period certificate date: confirm the exact displayed issue date. It stays metadata; an optional migration/reissue attestation must match that date and remains user-confirmed but unverified.
- Missing or incompletely linked account identity for an FBAR account set: “We could not extract a reliable account identifier from every supplied PDF. Please confirm that the supplied PDFs represent one account. You do not need to provide the account number.”
- No extractable issuer for an interest set, or an FBAR `one-account --require-institution` run: “We could not extract a reliable institution name from these statements. Please confirm the institution name shown on the statements.”

If a currency is corroborated, it proceeds without asking. If a prior-year date
is clearly an opening/prior balance, including an exact prior December 31
opening boundary ending in the requested year, it explains that the date is
contextual instead of asking for confirmation.

In Codex Desktop, use the bundled workspace Python if plain `python3` does not have `pdfplumber`. The skill should check the available runtime before calling the dependency missing.

## What It Produces

The preflight command writes:

- a JSON handoff for downstream scripts;
- a CSV review sheet for quick file-by-file inspection;
- review gates and warnings for anything that needs a human pause.

The JSON includes:

- statement files, resolved paths, byte sizes, SHA-256 fingerprints, page counts, and text counts;
- detected statement titles and direct or source-linked inferred period intervals, with independently traceable source references for both endpoints;
- stable opaque IDs for source-labelled periods whose year remains unresolved and needs one exact reviewed confirmation;
- detected years and out-of-year hints, plus source-labelled generated-on metadata kept separate from coverage;
- typed date-role evidence for statement periods, movements, opening boundaries, generated-on dates, and certificate-issued dates;
- parser hints, compact month-name period headings, and conservative next-line account-label binding;
- currency candidates it could back up with a nearby amount or name, the weaker ones it could not, and ambiguous `$` warnings;
- account hints, source-linkage coverage across the supplied PDFs, and institution hints;
- review gates such as low text, duplicate inputs, mixed years, out-of-period generated-on metadata, mixed currencies, possible mixed accounts, and possible mixed institutions.

The JSON is for machines. The CSV is for review. The chat answer should not treat either one as a finished tax or FBAR artifact.

## Use It In Codex

Ask for the skill directly:

```text
Use $statement-intake-preflight to check these 2025 statement PDFs before FBAR extraction.
```

Codex should read the root `SKILL.md`, then `references/workflow.md`, run the script, and review the JSON/CSV. A clean preflight can go to the downstream skill; a non-structural review-required preflight needs a separate user-confirmed handoff when the downstream workflow supports its typed resolutions. FBAR extraction checks the same PDFs in the same order against the preflight byte-size and SHA-256 fingerprints.

## Use It In Claude Code

The same package carries a Claude plugin command:

```text
/statement-intake-preflight:statement-intake-preflight 2025 one-account statement-01.pdf statement-02.pdf
```

The command file is only an adapter. The root `SKILL.md`, references, and script are the source of truth.

## Run The Script Manually

Requires Python 3.11 or newer with `pdfplumber`.

Preflight for FBAR account-ledger extraction:

```bash
python3 -B statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --require-institution \
  --out work/statement-preflight.json
```

Preflight for interest-income extraction:

```bash
python3 -B statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-institution \
  --out work/statement-preflight.json
```

Then either pass a clean preflight JSON to the downstream skill, or create a reviewed handoff after explicit user confirmation of every non-structural review gate:

```bash
python3 -B statement-intake-preflight/scripts/statement_intake_preflight.py review-handoff \
  --input work/statement-preflight.json \
  --accept-gate ambiguous-dollar \
  --confirm-currency COP \
  --user-review-confirmed \
  --out work/statement-preflight-reviewed.json
```

Repeat `--accept-gate` for each gate. Structural `stop` gates cannot be accepted: correct the inputs and rerun preflight. Schema 1.3 also supports exact certificate-date confirmation, a matching migration/reissue attestation plus note, and either exact-date or month-resolution account opening for a single leading gap. For a reviewed handoff, use the new JSON path below:

Add only resolution flags required by source gates: exact period ID/year, ISO currency, one-account assertion, typed institution, contextual year, generated-on date, or certificate-issued date. A migration/reissue assertion must repeat the exact confirmed certificate date and include `--migration-note`; it stays unverified user evidence. An `inferred-period-year` gate takes only its exact acceptance. For one continuous leading gap, choose exact `--confirm-account-opened-on 2025-05-01` or lower-resolution `--confirm-account-opened-month 2025-05`, never both; neither excuses a missing middle or year-end statement. Metadata dates bind exact source references but never become coverage. Do not provide a full account number by default or accept a genuine mixed statement year. <!-- privacy-gate: allow -->

```bash
python3 -B fbar-threshold-check/scripts/fbar_threshold_check.py extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight-reviewed.json \
  --out work/account-1.json
```

```bash
python3 -B statements-to-interest/scripts/statements_to_interest.py extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --preflight-json work/statement-preflight-reviewed.json \
  --out work/interest-analysis.json
```

## Failure Modes

The skill should stop or ask for review when:

- a file is missing or is not a PDF;
- a `.pdf` file cannot actually be read as a PDF;
- the PDF has little or no machine-readable text;
- the same statement PDF is handed in more than once, even under a different name;
- statement years do not line up with the requested year;
- an out-of-period generated-on date is present and needs source-bound review;
- `$` appears without enough context to know the currency;
- multiple currencies appear in one supposed currency bucket;
- multiple account hints appear, or one account hint cannot be source-linked across every supplied PDF, before an FBAR account run;
- multiple institution hints appear before an interest run or an FBAR run that opted into `--require-institution`;
- the user wants a final FBAR or tax-support result without running the downstream skill.

This is the good kind of early annoyance. Finding a scope problem here is cheaper than finding it inside a finished-looking support packet.

## Maintenance

After script changes:

```bash
python3 -B statement-intake-preflight/scripts/statement_intake_preflight.py dependency-check
python3 -B statement-intake-preflight/scripts/statement_intake_preflight.py self-test
python3 -B statement-intake-preflight/scripts/statement_intake_preflight.py smoke-test
python3 -S skill-forge/scripts/inspect_skill_package.py statement-intake-preflight --json --strict
```

The `self-test` is dependency-free and runs in CI. Before shipping a detector
change, also run the end-to-end adversarial suite, which drives real PDFs
through the CLI to reproduce every failure mode past audits surfaced (needs
`reportlab` + `pdfplumber`; it skips cleanly when they are absent):

```bash
python3 -B statement-intake-preflight/tests/run_pressure_suite.py
```

If Claude Code is available locally:

```bash
claude plugin validate --strict statement-intake-preflight
```

After reinstalling, verify source/installed parity:

```bash
diff -qr statement-intake-preflight ~/.codex/skills/statement-intake-preflight
```

`privacy-gate` and `skill-forge` are the repo gatekeepers. If either complains, fix the package before shipping.
