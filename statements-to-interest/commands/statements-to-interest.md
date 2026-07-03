---
description: Analyze one bank statement set for one tax year and render an interest support packet
argument-hint: "<tax-year> <institution> [account currency] <statement PDF paths...>"
---

You are running `/statements-to-interest:statements-to-interest $ARGUMENTS`.

Use the package root `SKILL.md` as the control plane and read `references/workflow.md` before analysis. The command is a Claude plugin entrypoint only; keep all operational workflow details in the shared root skill and references so Codex/OpenAI and Claude use the same process.

## Command Handling

1. Parse `$ARGUMENTS` for tax year, institution name, statement PDF paths, optional account currency, and optional output folder.
2. Ask only for missing required values.
3. Enforce one institution and one tax year.
4. Use machine-readable text PDFs only.
5. Run `scripts/statements_to_interest.py` exactly as described in `references/workflow.md`.
6. Review JSON and the review CSV internally, then make the PDF packet the user-facing deliverable.
7. For non-USD rows, use the separate `get-yearly-fx-rate` skill to create a proof-backed yearly-average workpaper, then pass its `workpaper.json` to `fx-prompt` and `report` with `--fx-workpaper-json`. Do not source published yearly averages inside this command. If the dependency is unavailable, stop and ask the user to install/run it or provide a user/preparer custom rate/source with `--fx-method user-rate`.
8. If FX confirmation is pending, ask the confirmation/custom-rate question and state that the PDF is pending. After confirmation, generate and verify the PDF, then return the PDF first with totals, review flags, and the `get-yearly-fx-rate` proof artifacts. Mention CSV only as an audit artifact if useful or requested.

Do not prepare official IRS forms and do not give tax advice.
