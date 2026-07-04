# FBAR Source Notes

Refresh official sources when current wording matters.

## Official Anchors

- IRS FBAR overview: `https://www.irs.gov/businesses/small-businesses-self-employed/report-of-foreign-bank-and-financial-accounts-fbar`
- FinCEN FBAR page: `https://www.fincen.gov/report-foreign-bank-and-financial-accounts`
- FinCEN line-item instructions PDF: `https://www.fincen.gov/system/files/shared/FBAR%20Line%20Item%20Filing%20Instructions.pdf`

## Threshold Framing

The IRS FBAR overview says a U.S. person must file when they have a financial interest in or signature/other authority over at least one foreign financial account and the aggregate value of foreign financial accounts exceeded `$10,000` at any time during the calendar year.

This skill does not decide U.S. person status, ownership, exceptions, signature authority, spouse filing treatment, or whether an account is reportable. It assumes the user has already selected the foreign accounts they want checked.

## Maximum Account Value

FinCEN line-item instructions describe a maximum account value process:

- Determine each account's maximum value in the account currency during the calendar year.
- Periodic account statements may be relied on when they fairly reflect the maximum value during the year.
- Value each account separately when more than one account exists.
- Convert non-USD maximum account values into USD using the required retained FX workpaper.
- If the account value is negative, use zero for maximum-value reporting.
- If the maximum value of one account or aggregate maximum values of multiple accounts exceeds `$10,000`, flag the max-value view.

This skill reports both the day-by-day aggregate view and the FinCEN maximum-value view so disagreements are visible.

## FX Dependency Note

The user has chosen `get-yearly-fx-rate` as this skill's single FX dependency. The FBAR checker must not duplicate FX sourcing. It may consume a dependency `workpaper.json`, but ordinary yearly-average workpapers are not accepted for FBAR conversion unless the dependency adds explicit FBAR/year-end-compatible metadata.

Accepted indicators include fields or source notes that clearly say FBAR, year-end, last day of calendar year, Treasury/FMS, Treasury Reporting Rates, FinCEN, or equivalent. Plain annual/yearly average language is rejected.

## Output Language

Use:

- "Daily threshold exceeded" / "No daily threshold crossing found in reviewed records."
- "FinCEN maximum-value view exceeded" / "FinCEN maximum-value view not exceeded."
- "Insufficient records for a confident daily no" when coverage is incomplete.
- "Support artifact" or "summary" instead of "official form."

Avoid:

- "FBAR filed."
- "IRS-approved."
- "Guaranteed no filing needed."
- Legal advice about ownership, exceptions, penalties, or filing obligations.
