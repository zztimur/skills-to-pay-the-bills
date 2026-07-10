# Get Year-End FX Rate

This skill exists for one specific headache:

```text
What year-end exchange rate did we use for FBAR-style conversion, and can we prove where it came from later?
```

The point is not to make an agent rummage around the internet and come back with a confident number. The point is to use a defensible year-end source, cite it cleanly, and leave a proof packet behind so Future Me does not have to reconstruct the decision from a chat transcript and a prayer.

## The Rule

One currency. One calendar year. A year-end rate.

Use Treasury/Fiscal Data first when it has the `YYYY-12-31` row for that currency. If Treasury does not list the currency/year, use another verifiable source that clearly supports a year-end or `YYYY-12-31` rate.

The date has to exist. If today is before the requested December 31, stop. A screenshot, a source note, or a confident preparer does not make a future year-end rate real.

"Treasury first" has to be true in practice, not just in a sentence. The maintained map covers Treasury's usable ISO rows; strict map-check makes every frozen-source row either resolve or carry an explicit exception. A published `Thailand-Baht` row should produce `THB`, not a mysterious jump to manual fallback.

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

For a Treasury packet, the source note also says whether that JSON was fetched live or supplied locally, and records its SHA-256. That is enough provenance to understand what happened without reverse-engineering a terminal session.

## When Treasury Is Offline

If the lookup exits 5, use the exact API query URL printed in the error, save its raw JSON response, and rerun the same lookup with `--api-file /path/to/response.json`. Do not replace the response with a search snippet, screenshot, or copied number. The resulting workpaper states whether the JSON was fetched live or supplied from a local file and retains its SHA-256.

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

`lookup` refuses a year whose `YYYY-12-31` date has not happened yet, before it fetches Treasury or writes a packet.

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

For manual sources, save the source page as PDF/HTML, take a screenshot, or download the source data when possible. The script copies that proof into the workpaper folder and hashes it. `--source-note` is required. Pass either `--proof-file` or a specific `--no-proof-file-reason`; the no-proof path records that reason and prints one caveat instead of pretending the generated workpaper PDF is source evidence.

The manual path has the same calendar guard as Treasury lookup. `--year-end-confirmed` confirms source wording; it is not permission to create a workpaper for a year-end date that has not occurred.

No-proof is an explicit exception, not a shortcut. Use it only when the source itself is verified but its artifact genuinely cannot be retained:

```bash
--no-proof-file-reason "The preparer verified the 2025-12-31 source, but the source artifact could not be retained."
```

## Failure Modes

The skill should stop instead of getting cute when:

- The currency is ambiguous, like `peso`, `dollar`, `pound`, `franc`, or `ruble`.
- The requested `YYYY-12-31` date has not happened yet.
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
python3 get-year-end-fx-rate/tests/run_regressions.py
python3 get-year-end-fx-rate/scripts/get_year_end_fx_rate.py map-check --year 2025 --currency AED --strict
```

The script keeps a Treasury row-to-ISO map because Fiscal Data uses labels like `United Arab Emirates-Dirham`, not ISO codes like `AED`. Use targeted `map-check --currency <ISO>` when adding a currency. Untargeted strict `map-check` must classify every row as mapped or explicitly excepted; an unexplained row is a map-maintenance failure, not proof that Treasury lacks a rate. The frozen 2025 Treasury response in `tests/fixtures/` keeps that test deterministic.

Run `tests/run_regressions.py` before a release. It executes the script-style regression checks directly, so release review does not depend on `unittest discover` finding tests that were never written as unittest classes. The checks protect the boring, expensive mistakes: locale-looking manual numbers, bad dates, unopened year-end dates, invented codes, annual-average wording, missing proof caveats, offline replay, and unmapped Treasury rows.

The workpaper machinery — folder naming, the `workpaper.md` / `workpaper.json` / `workpaper.pdf` renderers, and the copied-and-hashed source-proof schema — lives in [`workpaper-kit`](../workpaper-kit/), vendored here as `scripts/_workpaper.py` and shared with `get-yearly-fx-rate` so both proof packets look the same. Edit the canonical `workpaper-kit/workpaper.py`, never the generated copy; the pre-commit hook re-syncs it (or run `workpaper-kit/sync.sh`). The Treasury lookup, the `reject_average_language` rule, and currency handling stay here in the skill.

If the PDF layout changes, render a sample PDF and actually look at it. A byte-valid PDF is nice. A readable workpaper is the point.

Before shipping:

```bash
python3 -S skill-forge/scripts/inspect_skill_package.py get-year-end-fx-rate --json --strict
claude plugin validate --strict get-year-end-fx-rate
```

If the Anthropic skill-creator `quick_validate.py` is installed, run it against `get-year-end-fx-rate` too; otherwise the strict inspector and `claude plugin validate` are the gate. `skill-forge` is the gatekeeper for this repo. If it complains, fix the package before pushing.
