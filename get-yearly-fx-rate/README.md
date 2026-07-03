# Get Yearly FX Rate

This skill exists for one annoying tax-work question:

```text
What yearly average exchange rate did we use, and can we prove where it came from later?
```

The point is not to make an agent feel clever. The point is to get a published annual rate, cite it cleanly, and leave a workpaper behind so nobody has to reconstruct the logic from vibes six months later.

## The Rule

One currency. One calendar/tax year. A published yearly average rate.

Use the IRS yearly-average table first when it lists the currency and year. If the IRS does not list it, use a published annual average from a government, tax authority, central bank, bank, or reputable FX provider.

Do not calculate an annual average from daily, monthly, quarterly, or intraday data. If only raw time-series data exists, stop and ask for a published annual source or a custom preparer-approved rate/source.

Also: do not call the result IRS-approved. The IRS says it has no official exchange rate and generally accepts posted rates used consistently. This skill makes a support workpaper, not tax advice.

## What It Produces

For a clean run, the user-facing answer stays short:

```text
Rate: 1 USD = 1.37 CAD yearly average
Reciprocal: 1 CAD = 0.729927007299 USD
Source: IRS Yearly average currency exchange rates, https://www.irs.gov/..., retrieved YYYY-MM-DD
Proof: /absolute/path/to/workpaper.md
```

The proof folder lives under:

```text
work/fx-rate-proof/<currency>-<year>-<source-slug>/
```

It includes:

- `workpaper.md` for human review.
- `workpaper.json` for reuse by other workflows.
- A saved proof artifact where possible, such as IRS HTML, source data, PDF, screenshot, or another retained source file with a hash.

## Use It In Codex

Ask for the skill directly:

```text
Use $get-yearly-fx-rate to find the 2024 yearly average exchange rate for CAD to USD with proof.
```

Codex should read the root `SKILL.md`, follow `references/source-policy.md`, use the script for deterministic workpaper generation, and return only the rate, source, and proof path unless there is a real caveat.

## Use It In Claude Code

The same package carries a Claude plugin command:

```text
/get-yearly-fx-rate:get-yearly-fx-rate CAD 2024
```

Claude should use the same root `SKILL.md`, references, and script. The command file is only an adapter. There should not be a second hidden policy in Claude-land.

## Run The Script Manually

IRS table lookup:

```bash
python3 get-yearly-fx-rate/scripts/get_yearly_fx_rate.py lookup \
  --currency CAD \
  --year 2024 \
  --output-root work/fx-rate-proof
```

Manual workpaper for a non-IRS published annual source:

```bash
python3 get-yearly-fx-rate/scripts/get_yearly_fx_rate.py manual \
  --currency COP \
  --year 2024 \
  --rate 4200.00 \
  --rate-direction foreign-per-usd \
  --source-title "Published annual average source title" \
  --source-url "https://example.gov/rates/2024" \
  --source-note "Source labels this as a published yearly/annual average; retrieved YYYY-MM-DD" \
  --proof-file "/path/to/screenshot-or-source.html" \
  --output-root work/fx-rate-proof
```

`foreign-per-usd` means `1 USD = <rate> foreign currency`. Use `usd-per-foreign` only when the source is quoted as `1 foreign currency = <rate> USD`.

## Failure Modes

The skill should stop instead of getting cute when:

- The currency is ambiguous, like `peso`, `dollar`, or `pound`.
- The requested year is not published yet.
- The source gives daily/monthly/quarterly data but no annual average.
- Rate direction is unclear.
- The source is a search snippet instead of a real published page or file.

This is the useful kind of friction. A missing rate is better than a confident made-up one.

## Maintenance

After script changes:

```bash
python3 get-yearly-fx-rate/scripts/get_yearly_fx_rate.py self-test
```

Before shipping:

```bash
python3 /Users/timur/.codex/skills/.system/skill-creator/scripts/quick_validate.py get-yearly-fx-rate
python3 -S skill-forge/scripts/inspect_skill_package.py get-yearly-fx-rate --json --strict
claude plugin validate --strict get-yearly-fx-rate
```

`skill-forge` is the gatekeeper for this repo. If it complains, fix the package before pushing.
