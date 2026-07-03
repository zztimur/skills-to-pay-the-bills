---
name: get-year-end-fx-rate
description: Find and cite a Treasury/Fiscal Data or verified manual year-end USD FX rate with FBAR-style proof. Use for FBAR/foreign account year-end conversion, not yearly-average tax workflows.
---

# Get Year-End FX Rate

Find one year-end USD exchange rate, cite the source, and retain a proof workpaper. Return support documentation for FBAR-style conversion, not legal advice and not an official determination.

## Required Workflow

Read `references/source-policy.md` before answering. It defines source priority, FBAR framing, proof requirements, rate direction, and refusal rules.

Collect or infer:

- Currency, preferably ISO 4217 code such as `COP`, `CAD`, or `EUR`.
- Calendar year.
- Output root, defaulting to `work/fbar-fx-rate-proof/`.

Ask for clarification when the currency is ambiguous, for example `peso`, `dollar`, `pound`, or `ruble` without country/ISO code.

## Source Search

1. Prefer the Treasury Reporting Rates of Exchange dataset on Fiscal Data for the `YYYY-12-31` record date.
2. If Treasury/Fiscal Data has no year-end record for that currency/year, use another verifiable year-end source and retain the source note.
3. Do not calculate averages and do not reuse yearly-average rates from `get-yearly-fx-rate`.
4. If no verifiable year-end source is found, stop and ask the user or preparer for a custom year-end rate/source.

Use `scripts/get_year_end_fx_rate.py` for deterministic Treasury lookup and workpaper generation.

Treasury/Fiscal Data lookup:

```bash
python3 "<package-root>/scripts/get_year_end_fx_rate.py" lookup \
  --currency COP \
  --year 2025 \
  --output-root work/fbar-fx-rate-proof
```

Manual fallback after the agent has found or received a verifiable year-end source:

```bash
python3 "<package-root>/scripts/get_year_end_fx_rate.py" manual \
  --currency COP \
  --year 2025 \
  --rate 3900.00 \
  --rate-direction foreign-per-usd \
  --source-title "Published year-end source title" \
  --source-url "https://example.gov/rates/2025-year-end" \
  --source-note "Source labels this as the 2025-12-31 or year-end rate; retrieved YYYY-MM-DD" \
  --year-end-confirmed \
  --proof-file "/path/to/source-screenshot-or-saved-page.pdf" \
  --output-root work/fbar-fx-rate-proof
```

Use `foreign-per-usd` when the rate means one U.S. dollar equals the foreign-currency amount. Use `usd-per-foreign` only when the source means one unit of foreign currency equals the U.S. dollar amount.

For manual workpapers, do not run the script until the source clearly supports a year-end rate. A saved local proof file is strongly preferred; the script accepts `--proof-file` and copies/hashes it. If no proof file is available, the workpaper records that limitation.

## Treasury Map Maintenance

The helper uses a Treasury row-to-ISO map because Fiscal Data rows use country/currency labels, not ISO codes. When a valid Treasury-supported currency is rejected as unmapped, update `TREASURY_ROWS_BY_CODE` and any unambiguous aliases in `scripts/get_year_end_fx_rate.py`, then run:

```bash
python3 "<package-root>/scripts/get_year_end_fx_rate.py" map-check \
  --year 2025 \
  --currency AED \
  --strict
```

Use `--api-file` with a saved Fiscal Data JSON response when network access is unavailable.

## Proof Packet

Create a retained workpaper folder for every answered rate:

```text
work/fbar-fx-rate-proof/<currency>-<year>-<source-slug>/
```

The folder must include:

- `workpaper.json`, structured metadata for reuse by FBAR checking workflows.
- `workpaper.md`, a small human-readable source note.
- `workpaper.pdf`, a printable proof note with rate, source, caveats, and saved proof/hash details.
- For Treasury lookups, a saved Fiscal Data API JSON response.
- For manual sources, a saved screenshot/PDF/HTML/source artifact when one is available.

Do not fill the printable PDF with links to the workpaper's own PDF/MD/JSON files. Those artifact links belong in the final chat output. Inside the PDF, list only saved source proof filenames or packet-relative paths and hashes.

## Final Answer

Keep the user-facing answer concise and use this shape:

```markdown
Rate: 1 USD = <rate> <CURRENCY> year-end (<YYYY-12-31>)
Reciprocal: 1 <CURRENCY> = <usd-rate> USD
Source: <source title>, <URL>, retrieved <date>
Proof: [workpaper.pdf](<absolute path to workpaper.pdf>)
Artifacts: [workpaper.pdf](<absolute path to workpaper.pdf>), [workpaper.md](<absolute path to workpaper.md>), [workpaper.json](<absolute path to workpaper.json>), [source-proof-1.ext](<absolute path to saved source proof>)
```

Use Markdown links for every retained local artifact so the chat UI can expose them as clickable/downloadable files. Wrap link targets in angle brackets because workspace paths may contain spaces. Do not wrap proof paths in backticks.

Add one short caveat only when needed, such as `Treasury/Fiscal Data did not list this currency/year, so this uses a manual year-end source.` Do not call the rate official tax advice.

## Runtime And Validation

The script uses only Python standard-library modules. Use `python3` unless the active environment provides `python`.

After changing this skill, run:

```bash
python3 "<package-root>/scripts/get_year_end_fx_rate.py" self-test
python3 /Users/timur/.codex/skills/.system/skill-creator/scripts/quick_validate.py "<package-root>"
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
```

If Claude tooling is available, also run:

```bash
claude plugin validate --strict "<package-root>"
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/get-year-end-fx-rate.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
