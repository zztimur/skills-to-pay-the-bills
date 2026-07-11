---
description: Extract foreign-bank interest from a preflighted statement set for Schedule B support
argument-hint: "<tax-year> <institution> [account currency] <statement PDF paths...>"
---

You are running `/statements-to-interest:statements-to-interest $ARGUMENTS`.

Use the package root `SKILL.md` as the control plane and read `references/workflow.md` before analysis. The command is a Claude plugin entrypoint only; keep all operational workflow details in the shared root skill and references so Codex/OpenAI and Claude use the same process.

## Command Handling

1. Parse `$ARGUMENTS` for tax year, institution name, statement PDF paths, optional account currency, and optional output folder.
2. Ask only for missing required values.
3. Run `statement-intake-preflight` with `--scope one-institution`; let that skill own one-institution, one-year, text-PDF, currency-bucket, and ambiguous `$` intake gates.
4. Review the preflight JSON/CSV. Pass it to `scripts/statements_to_interest.py extract` only when it reports `ready-for-domain-extraction` with no review gates; the required `--preflight-json` is bound to the exact PDF set, tax year, and institution.
5. Run `scripts/statements_to_interest.py` exactly as described in `references/workflow.md`.
6. Review interest rows, excluded candidates, totals, warnings, and FX readiness internally. Do not issue a zero-interest packet when excluded interest-like candidates exist; when no interest-like evidence exists, require explicit preparer confirmation and record its note in the packet.
7. For non-USD rows, use the separate `get-yearly-fx-rate` skill to create a proof-backed yearly-average workpaper, then pass its `workpaper.json` to `fx-prompt` and `report` with `--fx-workpaper-json`. Do not source published yearly averages inside this command. If the dependency is unavailable, stop and ask the user to install/run it or provide a confirmed user/preparer custom rate with `--fx-method user-rate`; `--fx-source` is optional for custom rates.
8. If FX confirmation is pending, ask the confirmation/custom-rate question and state that the PDF is pending. After confirmation, generate and verify the PDF, then return the PDF first with totals, review flags, and the `get-yearly-fx-rate` proof artifacts. Mention CSV only as an audit artifact if useful or requested.

Do not prepare official IRS forms and do not give tax advice.
