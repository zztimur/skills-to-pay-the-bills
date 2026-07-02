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
6. Review JSON/CSV outputs, account currency, institution profile, and warnings before generating the PDF.
7. For non-USD rows, propose an official/published yearly average rate for the tax year, show the USD total, and ask the user to confirm it or provide a custom rate/source before reporting. Do not calculate the annual average yourself from daily/monthly data. Pass `--fx-rate-confirmed` only after confirmation; use `--fx-rates-json` only when the user explicitly requests item-date spot rates.
8. Return JSON, CSV, PDF, institution/profile details, totals, and review flags.

Do not prepare official IRS forms and do not give tax advice.
