# IRS-Oriented Interest Reporting Notes

Use these notes to keep the worksheet practical and conservative. Refresh the linked IRS pages when current-year wording matters.

## Scope

- This skill package creates a support packet for tax preparation. It does not prepare or file official IRS forms.
- Treat interest extracted from foreign bank statements as potentially taxable interest for review.
- Assume `statement-intake-preflight` has already scoped the reviewed statement set to one institution and one tax year. If mixed banks or mixed years appear later, return to preflight before reporting.

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
- "FX rate supplied by user/agent from source listed below" when a source is supplied or directly verified in the current task.
- "User/preparer supplied custom FX rate; no independent source provided" when the user chooses a custom rate without source support.
- "Account currency inferred from statement title/header" when the statement clearly names the currency, such as `Movimientos de cuenta en COP`.

## FX defaults

If counted rows are already denominated in USD, no FX rate decision is needed.

If counted rows are not denominated in USD, ask for a rate decision before generating the final PDF. Recommend one of:

- A proof-backed yearly average workpaper from the `get-yearly-fx-rate` dependency. That skill decides whether IRS or another published annual source is appropriate and retains the source proof.
- A user-provided rate, with optional source, when the user or preparer has a preferred method.
- Spot exchange rates only when the user or preparer explicitly requests item-date conversion.

Do not calculate yearly averages from daily, weekly, monthly, or intraday rates. If `get-yearly-fx-rate` is unavailable or cannot produce a published annual workpaper, stop before report generation and ask the user/preparer for a confirmed custom rate. A custom-rate source is useful but optional.

For non-USD yearly-average rows, the report script expects `--fx-workpaper-json` from `get-yearly-fx-rate`; it reads `foreign_per_usd` from that workpaper. Use `--fx-method user-rate` with `--fx-rate`, optional `--fx-source`, and `--rate-direction` when the user/preparer supplies a custom rate. The report must name the FX method, disclose when no custom-rate source was supplied, and pass `--fx-rate-confirmed` only after the user confirms the proposed published yearly average workpaper or supplies a custom rate.
