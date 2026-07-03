---
description: Find a published yearly average exchange rate with retained proof
argument-hint: "<currency-or-ISO-code> <year>"
---

You are running `/get-yearly-fx-rate:get-yearly-fx-rate $ARGUMENTS`.

Use the package root `SKILL.md` as the control plane and read `references/source-policy.md` before answering. This command is a Claude plugin entrypoint only; keep operational workflow details in the shared root skill and references so OpenAI/Codex, Claude, and other agents use the same process.

## Command Handling

1. Parse `$ARGUMENTS` for one currency and one calendar/tax year.
2. Ask only for missing or ambiguous values.
3. Prefer the IRS yearly-average table when the currency/year is listed.
4. If IRS does not list the currency/year, find a published yearly/annual average from a government, tax authority, central bank, bank, or reputable FX provider.
5. Never calculate an annual average from daily, monthly, quarterly, or intraday rates.
6. Use `scripts/get_yearly_fx_rate.py lookup` for IRS table extraction and workpaper creation.
7. Use `scripts/get_yearly_fx_rate.py manual` only after confirming a non-IRS source publishes an annual/yearly average and saving a local proof artifact. Pass `--annual-average-confirmed` and `--proof-file`.
8. If IRS table parsing appears stale or a listed IRS row is not mapped, run `scripts/get_yearly_fx_rate.py map-check` and update the shared script map instead of guessing.
9. Return only the concise rate/source/proof output defined in `SKILL.md`, with a short caveat when a non-IRS source is used.

Do not call the rate IRS-approved and do not give tax advice.
