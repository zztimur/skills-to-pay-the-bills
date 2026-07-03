---
name: get-yearly-fx-rate
description: Find and cite a published yearly average FX rate for one currency/year, with proof workpaper. Use for annual USD exchange rates, tax support, and rate-source proof.
---

# Get Yearly FX Rate

Find one published yearly average exchange rate, cite the source, and retain a proof workpaper. Return support documentation, not tax advice and not an official IRS determination.

## Required Workflow

Read `references/source-policy.md` before answering. It defines source priority, IRS wording, proof requirements, rate direction, and refusal rules.

Collect or infer:

- Currency, preferably ISO 4217 code such as `CAD`, `EUR`, or `COP`.
- Calendar/tax year.
- Output root, defaulting to `work/fx-rate-proof/`.

Ask for clarification when the currency is ambiguous, for example `peso`, `dollar`, or `pound` without country/ISO code.

## Source Search

1. Prefer the IRS yearly average currency exchange rates page when the currency and year are listed.
2. If the IRS table does not list the currency/year, search for a published annual average from a government, tax authority, central bank, bank, or reputable FX provider.
3. Do not derive an annual average from daily, weekly, monthly, quarterly, or intraday rates.
4. If no published annual average is found, stop and ask the user or preparer for a custom rate/source.

Use `scripts/get_yearly_fx_rate.py` for deterministic IRS extraction and workpaper generation.

IRS lookup:

```bash
python3 "<package-root>/scripts/get_yearly_fx_rate.py" lookup \
  --currency CAD \
  --year 2024 \
  --output-root work/fx-rate-proof
```

Manual published-source workpaper after the agent has found a non-IRS annual source:

```bash
python3 "<package-root>/scripts/get_yearly_fx_rate.py" manual \
  --currency COP \
  --year 2024 \
  --rate 4200.00 \
  --rate-direction foreign-per-usd \
  --source-title "Published annual average source title" \
  --source-url "https://example.gov/rates/2024" \
  --source-note "Source labels this as a published yearly/annual average; retrieved YYYY-MM-DD" \
  --output-root work/fx-rate-proof
```

Use `foreign-per-usd` when the rate means one U.S. dollar equals the foreign-currency amount. Use `usd-per-foreign` only when the published rate means one unit of foreign currency equals the U.S. dollar amount.

## Proof Packet

Create a retained workpaper folder for every answered rate:

```text
work/fx-rate-proof/<currency>-<year>-<source-slug>/
```

The folder must include:

- `workpaper.md`, a human-readable source note.
- `workpaper.json`, structured metadata for reuse by other workflows.
- Best available saved proof: HTML snapshot, source data file, PDF save/print, screenshot, or a link to an externally retained proof file.

For IRS lookups, the script saves the IRS HTML snapshot and hashes it. For non-IRS sources, save a screenshot/PDF/HTML/source file when the environment supports it, or record the source URL, retrieval date, and proof limitation in the workpaper.

## Final Answer

Keep the user-facing answer concise and use this shape:

```text
Rate: 1 USD = <rate> <CURRENCY> yearly average
Reciprocal: 1 <CURRENCY> = <usd-rate> USD
Source: <source title>, <URL>, retrieved <date>
Proof: <absolute path to workpaper.md>
```

Add one short caveat only when needed, such as `The IRS table did not list this currency, so this uses a non-IRS published annual average.` Do not call the rate IRS-approved.

## Runtime And Validation

The script uses only Python standard-library modules. Use `python3` unless the active environment provides `python`.

After changing this skill, run:

```bash
python3 "<package-root>/scripts/get_yearly_fx_rate.py" self-test
python3 /Users/timur/.codex/skills/.system/skill-creator/scripts/quick_validate.py "<package-root>"
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
```

If Claude tooling is available, also run:

```bash
claude plugin validate --strict "<package-root>"
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/get-yearly-fx-rate.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
