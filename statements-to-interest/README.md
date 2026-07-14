# Statements to Interest

Extract interest income from a ready statement set and generate an IRS-oriented support packet.

This skill is intentionally narrow:

- The statement set has already passed `statement-intake-preflight` for one institution, one tax year, readable PDFs, and currency scope.
- Interest-income support documentation, not official IRS forms or tax advice.
- Schedule B interest support only; use `fbar-threshold-check` for FBAR maximum-balance or threshold work.

Required companion skill: `statement-intake-preflight`. Extraction requires its intake JSON with `status: ready-for-domain-extraction` and no review gates, or a source-bound reviewed `one-institution` handoff with every required typed resolution, plus the same ordered PDF paths, tax year, and institution.

The preflight artifact records each PDF's normalized path, byte size, and lower-case SHA-256. Extraction verifies those fingerprints before reading the PDFs and immediately after all reads. It rejects legacy no-fingerprint artifacts, reordered or changed PDFs, raw review-required JSON, structural stops, genuine mixed-year or mixed-institution evidence, and incomplete/tampered reviewed handoffs. A reviewed institution name must match `--institution`; a reviewed out-of-period generated-on date must match the exact source date and reference and remains document metadata rather than year coverage.

The user-facing deliverable is the PDF packet. JSON and CSV outputs remain available for row review and audit support, but the CSV is not the primary result unless the user asks for it.

Raw evidence stays in local JSON/CSV artifacts. The PDF packet redacts emails, IBANs, and labelled account-like identifiers including dotted formats by default, while retaining dates and monetary amounts for review. It uses page citations with redacted snippets.

Ambiguous excluded interest-like candidates block a packet even when other interest rows were counted. Review them, then either correct the source and rerun extraction or create a digest-bound `resolve-exclusions` artifact that records the reviewer decision. Clear non-interest exclusions such as withholding remain visible but do not block reporting.

Zero extracted rows are not proof of zero interest. After resolving any ambiguous exclusions, a zero-interest packet requires explicit preparer confirmation with `--zero-interest-confirmed` and a non-empty `--zero-interest-confirmation-note`; the packet retains that note.

Reporting rechecks the analysis schema, preflight digest, original statement paths and hashes, any exclusion-resolution digest, and retained FX proof hashes before it writes the PDF. A packet should not stay green after its evidence changes underneath it.

## Package Map

- `SKILL.md` is the control plane for Codex/OpenAI Agent Skills.
- `references/workflow.md` has the detailed operational checklist, FX gate, troubleshooting, verification, and final-response template.
- `references/irs-interest-reporting.md` has IRS-oriented wording and source-link guidance.
- `scripts/statements_to_interest.py` performs extraction, FX prompts, packet assembly, dependency checks, and self-tests.
- `tests/run_preflight_integration.py` runs a synthetic real-PDF handoff regression through the sibling preflight and interest CLIs.
- In the source repository only, `workpaper-kit/workpaper.py` is vendored as `scripts/_workpaper.py` and owns the shared ReportLab packet layout. Change the canonical kit there and run `workpaper-kit/sync.sh`; do not hand-edit the vendored copy. The installed package is a read-only consumer and does not include `workpaper-kit`.
- `statement-intake-preflight` provides the shared PDF intake JSON/CSV used before extraction.
- `.claude-plugin/` and `commands/` provide the Claude Code entrypoint while reusing the same root workflow.

## FX Rule

For non-USD published yearly-average FX, use the separate `get-yearly-fx-rate` skill and pass its `workpaper.json` into this skill. Do not recreate annual FX source search inside `statements-to-interest`.

Conditional FX dependency: `get-yearly-fx-rate`. Use it when the packet needs a published yearly-average FX workpaper; use a confirmed user/preparer custom rate only when that dependency cannot produce one or the user chooses a custom rate.

If no published annual workpaper is available, ask for a confirmed user/preparer custom rate. A custom-rate source is optional; when absent, the PDF discloses that no independent source was provided and adds a preparer-review warning.

## Runtime

This package needs Python 3.11 or newer with `pdfplumber`, `reportlab`, and `pypdf`.

In Codex desktop, call `load_workspace_dependencies` and set `PYTHON` to the Python executable it returns before running any commands:

```bash
# Use the path returned by load_workspace_dependencies; do not hard-code a cache path.
PYTHON="<bundled Python path returned by load_workspace_dependencies>"
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py dependency-check
```

Outside Codex, set `PYTHON` to an environment where those three packages are installed. Use `"$PYTHON"` in the commands below.

`dependency-check` also looks in sibling skill folders, Codex installs, and `~/.claude/skills`. For another host layout, set `STATEMENTS_TO_INTEREST_SKILL_ROOTS` to one or more skill-root directories separated by the platform path separator. This only improves dependency diagnostics; the host must still discover the companion skills.

## Common Commands

Check runtime and dependency health:

```bash
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py self-test
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py dependency-check
```

Preflight the statement set:

```bash
"$PYTHON" statement-intake-preflight/scripts/statement_intake_preflight.py preflight \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --scope one-institution \
  --out "work/statement-preflight.json"
```

Extract statement rows:

```bash
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py extract \
  --pdf "statement-01.pdf" "statement-02.pdf" \
  --tax-year 2025 \
  --institution "Example Bank" \
  --preflight-json "work/statement-preflight.json" \
  --out "work/example-bank-2025-interest-analysis.json"
```

Generate a USD packet:

```bash
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --out "outputs/example-bank-2025-interest-support-packet.pdf"
```

Resolve ambiguous excluded candidates before reporting:

```bash
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py resolve-exclusions \
  --input "work/example-bank-2025-interest-analysis.json" \
  --all-excluded-not-interest-confirmed \
  --reviewer-note "Preparer reviewed every excluded candidate and found no additional interest income." \
  --out "work/example-bank-2025-excluded-candidates-resolution.json"
```

Pass the resulting file to `report` with `--excluded-candidates-resolution-json`. The command rejects it if the analysis or excluded-candidate list changes.

For a reviewed true-zero result, report it explicitly:

```bash
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py report \
  --input "work/example-bank-2025-interest-analysis.json" \
  --zero-interest-confirmed \
  --zero-interest-confirmation-note "Preparer reviewed all supplied statements and confirmed no interest was credited." \
  --out "outputs/example-bank-2025-zero-interest-support-packet.pdf"
```

For non-USD rows, follow the detailed FX workflow in `references/workflow.md`: generate or collect the FX decision, ask for confirmation, then run `report` with `--fx-rate-confirmed`.

## Validation

`self-test` includes deidentified, layout-style `pdfplumber` text fixtures in `tests/fixtures/`. They cover collapsed columns, repeated headers and footers, identifier-shaped values, dates and times, and competing balance amounts without retaining customer statement data.

Before shipping or reinstalling the skill:

```bash
"$PYTHON" -S skill-forge/scripts/inspect_skill_package.py statements-to-interest --json --strict
claude plugin validate --strict statements-to-interest
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py dependency-check
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py self-test
"$PYTHON" statements-to-interest/scripts/statements_to_interest.py smoke-test
"$PYTHON" statements-to-interest/tests/run_preflight_integration.py
```

Sync a cache-free package, then verify source/installed parity:

```bash
rsync -a --delete --delete-excluded \
  --exclude '__pycache__/' --exclude '*.py[co]' \
  statements-to-interest/ ~/.codex/skills/statements-to-interest/
diff -qr statements-to-interest ~/.codex/skills/statements-to-interest
```
