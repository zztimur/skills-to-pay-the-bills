---
description: Preflight one statement PDF set before FBAR or interest extraction
argument-hint: "<tax-year> <one-account|one-institution> <statement PDF paths...>"
---

You are running `/statement-intake-preflight:statement-intake-preflight $ARGUMENTS`.

Use the package root `SKILL.md` as the control plane and read `references/workflow.md` before analysis. The command is a Claude plugin entrypoint only; keep operational workflow details in the shared root skill and references so Codex/OpenAI and Claude use the same process.

## Command Handling

1. Parse `$ARGUMENTS` for one tax year, one scope (`one-account` or `one-institution`), statement PDF paths, and optional output folder.
2. Ask only for missing required values.
3. Run `scripts/statement_intake_preflight.py preflight` exactly as described in `references/workflow.md`.
4. Review the JSON and CSV before handing anything to downstream skills. For FBAR, create `review-handoff` only after the user explicitly confirms every non-structural review gate; never accept a structural `stop` gate.
5. Report the JSON path, CSV path, status, review gates, and, when created, the reviewed-handoff path.

Do not extract interest totals, build FBAR daily ledgers, source FX rates, prepare official forms, or give tax/legal advice.
