# Source Policy

Use this reference before answering any yearly FX rate request.

## IRS Framing

- Do not say a rate is IRS-approved.
- The IRS states that U.S. tax-return amounts must be expressed in U.S. dollars.
- The IRS yearly-average page says the IRS has no official exchange rate and generally accepts any posted exchange rate used consistently.
- For item-level tax translation, the IRS generally refers to the prevailing spot rate. This skill is for yearly average support when a yearly average is appropriate for the user's workflow or preparer.

Useful IRS pages:

- `https://www.irs.gov/individuals/international-taxpayers/yearly-average-currency-exchange-rates`
- `https://www.irs.gov/individuals/international-taxpayers/foreign-currency-and-currency-exchange-rates`

## Source Priority

Use the strongest available published annual average:

1. IRS yearly average table, when the currency and year are listed.
2. Government, tax authority, or central bank published yearly/annual average.
3. Bank or reputable FX provider published yearly/annual average.
4. User/preparer supplied custom rate and source.

Do not use:

- Agent-calculated averages from daily/monthly/quarterly data.
- Treasury quarterly reporting rates as a silent annual average.
- Spot rates unless the user explicitly asks for spot-rate support instead of a yearly average.
- Unsourced search snippets, LLM memory, or a page that does not expose the actual year/rate.

Treasury reporting rates are government data, but they are quarterly reporting rates for U.S. government agency reporting and are not annual averages. Use them only with an explicit caveat and only when the user or preparer accepts that source.

## Rate Direction

Default to `foreign-per-usd`:

```text
1 USD = <rate> <foreign currency>
```

This matches the IRS yearly average table and the `Rate:` line expected by this skill.

When a source gives `usd-per-foreign`, convert the displayed reciprocal so the final answer still shows both:

```text
Rate: 1 USD = <foreign-per-usd> <CURRENCY> yearly average
Reciprocal: 1 <CURRENCY> = <usd-per-foreign> USD
```

Do not silently invert a rate if the source direction is unclear. Stop and resolve the direction first.

## Proof Requirements

Every answered rate needs a retained local workpaper folder containing:

- `workpaper.md`
- `workpaper.json`
- `workpaper.pdf`
- At least one saved source proof artifact: screenshot, PDF save/print, HTML snapshot, downloaded source data, or equivalent retained source file.

Record:

- Currency and year.
- Published rate and direction.
- Source title and URL.
- Retrieval date.
- Whether the source explicitly labels the value as annual/yearly average.
- Any caveat, such as IRS table unavailable or proof artifact limited to HTML.
- Hashes for saved proof files when available.

The proof packet supports later review. It is not automatically attached to a tax return.

For non-IRS/manual sources, require both:

- Explicit confirmation that the source labels the rate as a yearly/annual average.
- A local proof file saved before the workpaper is created.

Do not accept a bare URL as the only proof for a manual non-IRS source.

## IRS Map Refresh

The helper script intentionally uses an IRS row-to-ISO map because the IRS table labels currencies by country and generic names such as `Dollar` or `Peso`. When the IRS table changes, run `scripts/get_yearly_fx_rate.py map-check`. If it reports unmapped rows, update `IRS_ROWS_BY_CODE` and any aliases needed for user-friendly currency names, then rerun the self-test and map check.

## Failure Rules

Ask for clarification when:

- The currency term is ambiguous: `peso`, `dollar`, `pound`, `krone/krona`, etc.
- The requested year is not published yet.
- The source provides multiple rates for the same currency/year and the correct rate is unclear.

Stop instead of answering when:

- No published annual average can be found.
- Only daily/monthly/quarterly data is available and no user/preparer custom rate was supplied.
- A manual non-IRS source has no saved local proof artifact.
- A manual non-IRS source has not been explicitly confirmed as a published yearly/annual average.
- Rate direction cannot be determined.
- The result would require tax advice beyond identifying and documenting a published rate.
