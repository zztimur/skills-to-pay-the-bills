# Get Year-End FX Rate

This skill exists for one specific headache:

```text
What year-end exchange rate did we use for FBAR-style conversion, and can we prove where it came from later?
```

The point is not to make an agent rummage around the internet and come back with a confident number. The point is to use a defensible year-end source, cite it cleanly, and leave a proof packet behind so Future Me does not have to reconstruct the decision from a chat transcript and a prayer.

## The Rule

One currency. One calendar year. A year-end rate.

Use Treasury/Fiscal Data first when it has the `YYYY-12-31` row for that currency. If Treasury does not list the currency/year, use another verifiable source that clearly supports a year-end or `YYYY-12-31` rate.

Do not calculate averages. Do not quietly borrow the yearly-average skill. Do not use monthly, quarterly, daily-series, or annual-average rates and pretend they are year-end rates. That is how a neat spreadsheet becomes a small tax archaeology project.

Also: this is support documentation, not legal advice and not an official IRS, FinCEN, or Treasury blessing. It gives the user a rate, source, and retained proof.

## What It Produces

For a clean Treasury run, the answer stays short:

```text
Rate: 1 USD = 3773.62 COP year-end (2025-12-31)
Reciprocal: 1 COP = 0.000264997536 USD
Source: Treasury Reporting Rates of Exchange - Fiscal Data, https://fiscaldata.treasury.gov/..., retrieved YYYY-MM-DD
Proof: [workpaper.pdf](</absolute/path/to/workpaper.pdf>)
Artifacts: [workpaper.pdf](</absolute/path/to/workpaper.pdf>), [workpaper.md](</absolute/path/to/workpaper.md>), [workpaper.json](</absolute/path/to/workpaper.json>), [treasury-fiscal-data-response.json](</absolute/path/to/treasury-fiscal-data-response.json>)
```

The proof folder lives under:

```text
work/fbar-fx-rate-proof/<currency>-<year>-<source-slug>/
```

It includes:

- `workpaper.json` for the FBAR checker or any later workflow that wants machine-readable proof.
- `workpaper.md` for a human-readable note.
- `workpaper.pdf` for printable review.
- A saved source artifact, usually the Treasury/Fiscal Data API JSON response for lookup runs.

The JSON is the source of truth for machines. The PDF is for humans. The chat answer links everything because a plain path in chat is technically information and practically annoying.

## Use It In Codex

Ask for the skill directly:

```text
Use $get-year-end-fx-rate to find the 2025 year-end exchange rate for COP to USD with proof.
```

Codex should read the root `SKILL.md`, follow `references/source-policy.md`, use the script for deterministic lookup/workpaper generation, and return only the rate, source, and proof links unless there is a real caveat.

## Use It In Claude Code

The same package carries a Claude plugin command:

```text
/get-year-end-fx-rate:get-year-end-fx-rate COP 2025
```

Claude should use the same root `SKILL.md`, references, and script. The command file is only an adapter. There should not be a second policy hiding in Claude-land.

## Run The Script Manually

Treasury/Fiscal Data lookup:

```bash
python3 get-year-end-fx-rate/scripts/get_year_end_fx_rate.py lookup \
  --currency COP \
  --year 2025 \
  --output-root work/fbar-fx-rate-proof
```

Manual workpaper for a verified year-end source:

```bash
python3 get-year-end-fx-rate/scripts/get_year_end_fx_rate.py manual \
  --currency COP \
  --year 2025 \
  --rate 3900.00 \
  --rate-direction foreign-per-usd \
  --source-title "Published year-end source title" \
  --source-url "https://example.gov/rates/2025-year-end" \
  --source-note "Source labels this as the 2025-12-31 or year-end rate; retrieved YYYY-MM-DD" \
  --year-end-confirmed \
  --proof-file "/path/to/screenshot-or-source.html" \
  --output-root work/fbar-fx-rate-proof
```

`foreign-per-usd` means `1 USD = <rate> foreign currency`. Use `usd-per-foreign` only when the source is quoted as `1 foreign currency = <rate> USD`.

For manual sources, save the source page as PDF/HTML, take a screenshot, or download the source data when possible. The script copies that proof into the workpaper folder and hashes it. If no proof file is supplied, the workpaper says so plainly instead of pretending the evidence is better than it is.

## Failure Modes

The skill should stop instead of getting cute when:

- The currency is ambiguous, like `peso`, `dollar`, `pound`, `franc`, or `ruble`.
- Treasury/Fiscal Data has no `YYYY-12-31` row and no manual year-end source is available.
- The source gives averages instead of a year-end rate.
- Rate direction is unclear.
- The source is a search snippet instead of a real published page or file.
- The request would require legal or tax advice beyond documenting a rate and source.

This is the useful kind of friction. A missing rate is better than a tidy-looking wrong one.

## Maintenance

After script changes:

```bash
python3 get-year-end-fx-rate/scripts/get_year_end_fx_rate.py self-test
```

If the PDF layout changes, render a sample PDF and actually look at it. A byte-valid PDF is nice. A readable workpaper is the point.

Before shipping:

```bash
python3 /Users/timur/.codex/skills/.system/skill-creator/scripts/quick_validate.py get-year-end-fx-rate
python3 -S skill-forge/scripts/inspect_skill_package.py get-year-end-fx-rate --json --strict
claude plugin validate --strict get-year-end-fx-rate
```

`skill-forge` is the gatekeeper for this repo. If it complains, fix the package before pushing.
