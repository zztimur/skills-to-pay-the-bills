# Statements to Interest

Extract interest income from a reviewed statement set and generate an IRS-oriented support packet.

This skill is intentionally narrow:

- The statement set has already passed `statement-intake-preflight` for one institution, one tax year, readable PDFs, and currency scope.
- Interest-income support documentation, not official IRS forms or tax advice.

The user-facing deliverable is the PDF packet. JSON and CSV outputs remain available for row review and audit support, but the CSV is not the primary result unless the user asks for it.

## Package Map

- `SKILL.md` is the control plane for Codex/OpenAI Agent Skills.
- `references/workflow.md` has the detailed operational checklist, FX gate, troubleshooting, verification, and final-response template.
- `references/irs-interest-reporting.md` has IRS-oriented wording and source-link guidance.
- `scripts/statements_to_interest.py` performs extraction, FX prompts, PDF generation, dependency checks, and self-tests.
- `statement-intake-preflight` provides the shared PDF intake JSON/CSV used before extraction.
- `.claude-plugin/` and `commands/` provide the Claude Code entrypoint while reusing the same root workflow.

## FX Rule

For non-USD published yearly-average FX, use the separate `get-yearly-fx-rate` skill and pass its `workpaper.json` into this skill. Do not recreate annual FX source search inside `statements-to-interest`.

If no published annual workpaper is available, ask for a confirmed user/preparer custom rate. A custom-rate source is optional; when absent, the PDF discloses that no independent source was provided and adds a preparer-review warning.

## Common Commands

Check runtime and dependency health:

```bash
python statements-to-interest/scripts/statements_to_interest.py self-test
python statements-to-interest/scripts/statements_to_interest.py dependency-check
```

Preflight the statement set:

```bash
python3 statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --scope one-institution \
  --out "work/statement-preflight.json"
```

Extract statement rows:

```bash
python statements-to-interest/scripts/statements_to_interest.py extract \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --institution "Example Bank" \
  --preflight-json "work/statement-preflight.json" \
  --out "work/example-bank-2025-interest-analysis.json"
```

Generate a USD packet:

```bash
python statements-to-interest/scripts/statements_to_interest.py report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

For non-USD rows, follow the detailed FX workflow in `references/workflow.md`: generate or collect the FX decision, ask for confirmation, then run `report` with `--fx-rate-confirmed`.

## Validation

Before shipping or reinstalling the skill:

```bash
python -S skill-forge/scripts/inspect_skill_package.py statements-to-interest --json --strict
claude plugin validate --strict statements-to-interest
python statements-to-interest/scripts/statements_to_interest.py self-test
```

After reinstalling, verify source/installed parity:

```bash
diff -qr statements-to-interest /Users/timur/.codex/skills/statements-to-interest
```
