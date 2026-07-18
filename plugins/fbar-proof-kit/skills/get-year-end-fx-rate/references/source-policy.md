# Source Policy

Use this reference before answering any year-end FX rate request.

## FBAR Framing

- Use this skill for FBAR-style year-end balance conversion support.
- Do not say the result is legal advice or an official IRS, FinCEN, or Treasury determination.
- Do not use this skill for income-tax yearly or annual average workflows. Use `get-yearly-fx-rate` for that separate job.
- Record the source, retrieval date, rate direction, and retained proof so another workflow can consume `workpaper.json` directly.

Useful source pages:

- `https://fiscaldata.treasury.gov/datasets/treasury-reporting-rates-exchange/treasury-reporting-rates-of-exchange`
- `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/rates_of_exchange_source`

## Source Priority

Use the strongest available year-end source:

1. Treasury Reporting Rates of Exchange on Fiscal Data for `YYYY-12-31`.
2. Government, tax authority, or central bank source that explicitly identifies a year-end or `YYYY-12-31` rate.
3. Bank or reputable FX provider source that explicitly identifies a year-end or `YYYY-12-31` rate.
4. User/preparer supplied custom year-end rate and source.

Do not use:

- Annual, yearly, monthly, quarterly, daily-series, or intraday averages.
- A rate from `get-yearly-fx-rate`.
- Unsourced search snippets, model memory, or a page that does not expose the actual year-end date/rate.
- A manual source whose rate direction cannot be determined.
- A claimed year-end rate for a `YYYY-12-31` date that has not occurred yet.

Treasury Reporting Rates are quarterly U.S. government reporting rates. For this skill, use the December 31 record date as year-end support when it exists for the requested currency/year. If the Treasury dataset does not list the currency/year, keep that caveat and use a manual year-end source only after explicit confirmation.

The requested year-end date must exist before a packet is created. If today is before the requested `YYYY-12-31`, stop. A retrieval date, saved proof file, source note, or `--year-end-confirmed` flag cannot override the calendar.

If the Treasury API call fails with exit code 5, retain the raw JSON response for the exact query URL in the error and rerun the same lookup with `--api-file`. Treat that local JSON as source proof, not as a substitute rate: do not use search snippets, screenshots, or copied values. Record whether the response was fetched live or supplied locally.

If Fiscal Data has a row for the requested currency/year but the helper says the ISO code is unmapped, treat that as a script map-maintenance issue, not as Treasury unavailability. Update `TREASURY_ROWS_BY_CODE`, a documented alternate mapping, or the explicit exception registry, then rerun strict `map-check`. The frozen `tests/fixtures/treasury-2025-12-31.json` fixture must have no unexplained rows.

## Rate Direction

Default to `foreign-per-usd`:

```text
1 USD = <rate> <foreign currency>
```

Treasury/Fiscal Data `exchange_rate` is treated as `foreign-per-usd` for this workflow.

When a manual source gives `usd-per-foreign`, convert the displayed reciprocal so the final answer still shows both:

```text
Rate: 1 USD = <foreign-per-usd> <CURRENCY> year-end (<YYYY-12-31>)
Reciprocal: 1 <CURRENCY> = <usd-per-foreign> USD
```

Do not silently invert a rate if the source direction is unclear. Stop and resolve the direction first.

## Proof Requirements

Every answered rate needs a retained local workpaper folder containing:

- `workpaper.json`
- `workpaper.md`
- `workpaper.pdf`
- At least one saved source proof artifact when available: Fiscal Data API JSON, screenshot, PDF save/print, HTML snapshot, downloaded source data, or equivalent retained source file.

Record:

- Currency and year.
- Year-end date.
- Published rate and direction.
- Source title and URL.
- Retrieval date.
- Whether the source explicitly supports a year-end or `YYYY-12-31` rate.
- Any caveat, such as Treasury/Fiscal Data unavailable or a manual proof artifact not retained.
- Hashes for saved proof files when available.

For a manual packet, use a nonempty absolute `http://` or `https://` source URL. The retrieval date must be the actual date the source was retrieved, never a future date. If a proof artifact genuinely cannot be retained, the no-proof explanation must be a specific, trimmed sentence of at least 16 characters.

In the printable PDF, identify saved source proof artifacts by filename or packet-relative path plus hash. Do not rely on absolute local computer paths as evidence because external reviewers cannot access them. Absolute paths are acceptable in `workpaper.json` and chat artifact links for local navigation.

The proof packet supports later review. It is not automatically attached to an FBAR or tax return.

For a manual source with no local proof artifact, require a specific written reason. Record that reason in the packet and repeat it as one concise caveat in the final answer. Do not present the generated workpaper PDF itself as source proof.

## Failure Rules

Ask for clarification when:

- The currency term is ambiguous: `peso`, `dollar`, `pound`, `franc`, `ruble`, `krone/krona`, etc.
- The source provides multiple rates for the same currency/year and the correct year-end rate is unclear.

Stop instead of answering when:

- The requested year-end date has not occurred yet.
- No Treasury or verifiable manual year-end source can be found.
- Only averages are available.
- A manual source has not been explicitly confirmed as year-end support.
- A manual source has neither a saved local proof artifact nor a specific reason for its absence.
- Rate direction cannot be determined.
- The result would require legal or tax advice beyond identifying and documenting a published rate.
