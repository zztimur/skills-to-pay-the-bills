---
description: Check one tax year's foreign account balances against FBAR thresholds
argument-hint: "<tax-year> <statement PDF paths...>"
---

You are running `/fbar-threshold-check:fbar-threshold-check $ARGUMENTS`.

Use the package root `SKILL.md` as the control plane and read `references/workflow.md` before analysis. The command is a Claude plugin entrypoint only; keep operational workflow details in the shared root skill and references so Codex/OpenAI and Claude use the same process.

## Command Handling

1. Parse `$ARGUMENTS` for tax year and statement PDF paths for one account.
2. Ask only for missing required values.
3. Enforce one account, one tax year, and one currency bucket per extraction pass.
4. Use machine-readable PDFs only.
5. Run `scripts/fbar_threshold_check.py extract-account` as described in `references/workflow.md`.
6. Review JSON and CSV internally before asking the user to confirm the account ledger.
7. For non-USD accounts, use the separate `get-year-end-fx-rate` skill (preferred) to produce a year-end workpaper - or an explicitly FBAR-compatible `get-yearly-fx-rate` workpaper - then pass its `workpaper.json` to `confirm-account`. Do not source or override FX rates inside this command.
8. If `confirm-account` reports carry-forward gaps, review `coverage.carry_gaps` with the user and pass `--accept-carry-forward` only after they explicitly accept the carried spans.
9. After each confirmed account, ask if the user has another foreign account for the same year. Aggregate only after the user says there are no more accounts.
10. Return the daily threshold result (`daily_threshold.answer`: yes/no/insufficient-records), the FinCEN maximum-value view, final JSON/CSV/PDF paths, and unresolved warnings.

Do not prepare official FBAR forms and do not give legal or tax advice.
