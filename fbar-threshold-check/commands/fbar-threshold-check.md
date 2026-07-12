---
description: Check whether foreign accounts crossed the $10,000 FBAR threshold for one tax year
argument-hint: "<tax-year> <statement PDF paths...>"
---

You are running `/fbar-threshold-check:fbar-threshold-check $ARGUMENTS`.

This command is a Claude plugin entrypoint only. The package root `SKILL.md` is the control plane and `references/workflow.md` holds the authoritative process; do not restate or paraphrase that workflow here, so Claude and Codex/OpenAI stay on one process.

## Command Handling

1. Read the package root `SKILL.md`, then `references/workflow.md`, before doing anything else.
2. Treat `$ARGUMENTS` as an opening hint: the first token is the tax year, remaining tokens are statement PDF paths intended for one account. Ask only for missing required values.
3. Execute the workflow in `references/workflow.md` exactly, including the required `statement-intake-preflight` run with `--scope one-account`, its ready JSON or user-confirmed reviewed handoff passed through `--preflight-json` into `extract-account`, the mandatory stop-and-show-the-user review gate before `confirm-account` (do not self-confirm), the FX dependency contract, the carry-forward `--accept-carry-forward` gate, the per-account repeat loop, and the final-response shape. Let preflight ask only its evidence-specific questions; do not ask for a corroborated currency, a clearly contextual prior-year date with clear coverage, an already-extracted account identifier, or a full account number by default.

Do not prepare official FBAR forms and do not give legal or tax advice.
