# Statement Intake Preflight

This skill exists for one specific bit of useful friction:

```text
Are these statement PDFs clean enough, scoped enough, and boring enough to hand to the real tax-support workflow?
```

The point is not to extract interest, rebuild daily balances, choose FX rates, or answer an FBAR question. The point is to stop at the front door and ask whether the statement set looks like one coherent thing: one year, one account or institution, readable text, plausible currency, and no obvious trap hiding behind a tidy filename.

It leaves behind a JSON/CSV handoff so the next skill does not have to rediscover the same intake facts from scratch.

## The Rule

One statement set. One tax year. One intended downstream scope.

Use `one-account` when the next step is `fbar-threshold-check`. Use `one-institution` when the next step is `statements-to-interest`.

Do not OCR screenshots. Do not smooth over a mystery `$`. Do not pretend a folder name proves the bank, account, currency, or year. If the text layer is weak, the years are mixed, account hints multiply, or currency is fuzzy, the output should say that plainly before any downstream skill starts doing more expensive work.

This is support documentation, not a tax conclusion. A clean preflight means the input is ready for the next parser. It does not mean the account is complete, the interest total is right, or an FBAR threshold answer exists.

## What It Produces

The preflight command writes:

- a JSON handoff for downstream scripts;
- a CSV review sheet for quick file-by-file inspection;
- review gates and warnings for anything that needs a human pause.

The JSON includes:

- statement files, resolved paths, page counts, and text counts;
- detected statement titles and periods;
- detected years and out-of-year hints;
- currency candidates and ambiguous `$` warnings;
- account hints and institution hints;
- review gates such as low text, mixed years, mixed currencies, possible mixed accounts, and possible mixed institutions.

The JSON is for machines. The CSV is for review. The chat answer should not treat either one as a finished tax or FBAR artifact.

## Use It In Codex

Ask for the skill directly:

```text
Use $statement-intake-preflight to check these 2025 statement PDFs before FBAR extraction.
```

Codex should read the root `SKILL.md`, then `references/workflow.md`, run the script, review the JSON/CSV, and only then hand the JSON to `fbar-threshold-check` or `statements-to-interest`.

## Use It In Claude Code

The same package carries a Claude plugin command:

```text
/statement-intake-preflight:statement-intake-preflight 2025 one-account statement-01.pdf statement-02.pdf
```

The command file is only an adapter. The root `SKILL.md`, references, and script are the source of truth.

## Run The Script Manually

Preflight for FBAR account-ledger extraction:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-account \
  --out work/statement-preflight.json
```

Preflight for interest-income extraction:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --scope one-institution \
  --out work/statement-preflight.json
```

Then pass the reviewed JSON to the downstream skill:

```bash
python3 fbar-threshold-check/scripts/fbar_threshold_check.py extract-account \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --preflight-json work/statement-preflight.json \
  --out work/account-1.json
```

```bash
python3 statements-to-interest/scripts/statements_to_interest.py extract \
  --pdf statement-01.pdf statement-02.pdf \
  --tax-year 2025 \
  --institution "Example Bank" \
  --preflight-json work/statement-preflight.json \
  --out work/interest-analysis.json
```

## Failure Modes

The skill should stop or ask for review when:

- a file is missing or is not a PDF;
- the PDF has little or no machine-readable text;
- statement years do not line up with the requested year;
- `$` appears without enough context to know the currency;
- multiple currencies appear in one supposed currency bucket;
- multiple account hints appear before an FBAR account run;
- multiple institution hints appear before an interest run;
- the user wants a final FBAR or tax-support result without running the downstream skill.

This is the good kind of early annoyance. Finding a scope problem here is cheaper than finding it inside a finished-looking support packet.

## Maintenance

After script changes:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py self-test
python3 -S skill-forge/scripts/inspect_skill_package.py statement-intake-preflight --json --strict
```

If Claude Code is available locally:

```bash
claude plugin validate --strict statement-intake-preflight
```

After reinstalling, verify source/installed parity:

```bash
diff -qr statement-intake-preflight /Users/timur/.codex/skills/statement-intake-preflight
```

`privacy-gate` and `skill-forge` are the repo gatekeepers. If either complains, fix the package before shipping.
