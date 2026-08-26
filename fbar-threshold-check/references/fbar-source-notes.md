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

## Evidence And Computation Limits

Keep policy, evidence, and arithmetic separate:

- `formal-extracted` means a supported statement parser captured source-bound balances; it does not decide reportability.
- `diagnostic-reconstructed` means labelled opening balance plus signed movements reconciled to a labelled close; it remains a workpaper reconstruction.
- `user-attested-*` means the user/preparer confirmed values from certificate or other evidence; it is never relabelled as formal extraction.
- An undated annual maximum has no defensible daily placement. Aggregate it as a possible daily upper bound and report `review-required` when it can change the threshold answer.
- Statement dates do not establish intraday simultaneity. Label daily combinations `date-only-upper-bound` unless stronger timestamp evidence exists.
- Compare exact converted values to `$10,000` before display rounding. Exactly `$10,000` is not an exceedance. Show whole-dollar maximum values separately and disclose when rounding policy changes a displayed result.

## FX Dependency Note

This skill consumes FX proof workpapers only from its `get-year-end-fx-rate` companion skill and must not duplicate FX sourcing:

- `get-year-end-fx-rate`: produces Treasury/Fiscal Data or verified manual year-end rates - the FBAR-style conversion basis - and is accepted by construction.

Do not use `get-yearly-fx-rate` workpapers for FBAR conversion. Yearly-average rates belong to income-tax support workflows, not year-end FBAR threshold evidence.

## Output Language

Use:

- "Daily threshold exceeded" / "No daily threshold crossing found in reviewed records."
- "FinCEN maximum-value view exceeded" / "FinCEN maximum-value view not exceeded."
- "Insufficient records for a confident daily no" when coverage is incomplete.
- "Review required: known daily total does not exceed $10,000, but the possible upper bound does" when undated evidence is answer-sensitive.
- "Support artifact" or "summary" instead of "official form."

Avoid:

- "FBAR filed."
- "IRS-approved."
- "Guaranteed no filing needed."
- Legal advice about ownership, exceptions, penalties, or filing obligations.
