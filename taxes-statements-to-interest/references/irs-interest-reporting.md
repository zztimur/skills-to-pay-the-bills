# IRS-Oriented Interest Reporting Notes

Use these notes to keep the worksheet practical and conservative. Refresh the linked IRS pages when current-year wording matters.

## Scope

- This skill package creates a support packet for tax preparation. It does not prepare or file official IRS forms.
- Treat interest extracted from foreign bank statements as potentially taxable interest for review.
- Use one institution and one tax year per run. Split mixed banks or mixed years.

## Source anchors

- Schedule B: https://www.irs.gov/forms-pubs/about-schedule-b-form-1040
  - Schedule B may be needed for more than $1,500 of taxable interest or ordinary dividends and for foreign-account/trust questions.
- Publication 550: https://www.irs.gov/publications/p550
  - Pub. 550 discusses interest income and generally reporting taxable interest on Form 1040 or 1040-SR, line 2b, with Schedule B Part I in listed cases.
- Foreign currency conversion: https://www.irs.gov/individuals/international-taxpayers/foreign-currency-and-currency-exchange-rates
  - U.S. tax return amounts are reported in U.S. dollars. For U.S.-dollar functional currency taxpayers, foreign-currency items that affect income tax are translated into dollars.
- IRS yearly average rates: https://www.irs.gov/individuals/international-taxpayers/yearly-average-currency-exchange-rates
  - The IRS yearly average table expresses foreign currency units per U.S. dollar; convert foreign currency to U.S. dollars by dividing by the table rate.
- FBAR overview: https://www.irs.gov/businesses/small-businesses-self-employed/report-of-foreign-bank-and-financial-accounts-fbar
  - FBAR is separate from the federal tax return and depends on foreign-account facts, including aggregate maximum value thresholds.
- Form 8938 information: https://www.irs.gov/forms-pubs/about-form-8938
  - Form 8938 is a separate review item for specified foreign financial assets.

## Report language

Use careful language:

- "Support packet", "worksheet", "for preparer review", and "not an official IRS form".
- "Review Schedule B applicability" rather than "file Schedule B".
- "Review FBAR/Form 8938 applicability" rather than deciding the filing result.
- "FX rate supplied by user/agent from source listed below" unless the source is directly verified in the current task.

## FX defaults

If counted rows are already denominated in USD, no FX rate decision is needed.

If counted rows are not denominated in USD, ask for a rate decision before generating the final PDF. Recommend one of:

- IRS yearly average exchange rate for recurring interest in one tax year.
- A user-provided rate and source when the user or preparer has a preferred method.

For non-USD rows, the report script expects `--fx-rate` as foreign currency units per 1 U.S. dollar by default. Use `--rate-direction usd-per-foreign` only if the user supplies a U.S. dollars per 1 foreign currency unit rate.
