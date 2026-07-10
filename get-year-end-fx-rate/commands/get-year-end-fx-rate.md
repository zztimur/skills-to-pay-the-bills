---
description: Find a Treasury/Fiscal Data or verified manual year-end exchange rate with retained proof
argument-hint: "<currency-or-ISO-code> <year>"
---

You are running `/get-year-end-fx-rate:get-year-end-fx-rate $ARGUMENTS`.

Use the package root `SKILL.md` as the control plane and read `references/source-policy.md` before answering. This command is a Claude plugin entrypoint only; keep operational workflow details in the shared root skill and references so OpenAI/Codex, Claude, and other agents use the same process.

## Command Handling

1. Parse `$ARGUMENTS` for one currency and one calendar year.
2. Ask only for missing or ambiguous values.
3. Stop if the requested `YYYY-12-31` date has not occurred yet; no proof file or manual confirmation overrides that.
4. Prefer Treasury/Fiscal Data for the `YYYY-12-31` Treasury Reporting Rates of Exchange row.
5. If Treasury/Fiscal Data does not list the currency/year, find or request another verifiable year-end source.
6. Never calculate averages and never reuse a yearly-average rate.
7. Use `scripts/get_year_end_fx_rate.py lookup` for Treasury/Fiscal Data extraction and workpaper creation.
8. If a Treasury command exits 5, fetch the exact query URL from its error, save the raw JSON response, and rerun with `--api-file`. Do not use a search snippet, screenshot, or copied number as the substitute source.
9. Use `scripts/get_year_end_fx_rate.py manual` only after confirming the source supports a year-end or `YYYY-12-31` rate. Pass a nonempty `--source-note` and `--year-end-confirmed`; pass either `--proof-file` or a specific `--no-proof-file-reason`.
10. Return only the concise rate/source/proof output defined in `SKILL.md`. Preserve the script's caveat when no saved source proof file was available.

Do not call the rate legal/tax advice and do not use yearly-average wording.
