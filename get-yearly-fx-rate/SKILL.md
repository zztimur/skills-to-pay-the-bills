---
name: get-yearly-fx-rate
description: Find and cite a published yearly-average USD FX rate with a proof workpaper for tax support. Use for IRS annual-average exchange-rate lookups, not year-end or FBAR conversion rates.
---

# Get Yearly FX Rate

Find one published yearly average exchange rate, cite the source, and retain a proof workpaper. Return support documentation, not tax advice and not an official IRS determination.

## Required Workflow

Read `references/source-policy.md` before answering. It defines source priority, IRS wording, proof requirements, rate direction, and refusal rules.

Collect or infer:

- Currency, preferably ISO 4217 code such as `CAD`, `EUR`, or `COP`.
- Calendar/tax year.
- Output root, defaulting to `work/fx-rate-proof/`.

Ask for clarification when the currency is ambiguous, for example `peso`, `dollar`, `pound`, or `ruble` without country/ISO code.

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

If the script cannot reach the IRS page (exit code 5, common in sandboxed or proxied environments), fetch the IRS yearly-average page with your web tool, save its raw HTML as a regular UTF-8 file no larger than 5 MiB, and rerun `lookup` with `--html-file <saved.html>`. The lookup always records the canonical IRS page as its source; `--html-file` is only an offline snapshot replay.

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
  --annual-average-confirmed \
  --proof-file "/path/to/source-screenshot-or-saved-page.pdf" \
  --output-root work/fx-rate-proof
```

Use `foreign-per-usd` when the rate means one U.S. dollar equals the foreign-currency amount. Use `usd-per-foreign` only when the published rate means one unit of foreign currency equals the U.S. dollar amount.

For manual non-IRS workpapers, do not run the script until there is a local proof file and the source explicitly labels the rate as a yearly/annual average. The proof file can be a screenshot, PDF save/print, HTML snapshot, downloaded source data file, or another retained source artifact. `--source-url` must be a nonempty absolute `http://` or `https://` URL, `--source-note` must explain annual-average support, and `--retrieved` must be today or earlier. The helper refuses metadata that identifies daily, weekly, monthly, quarterly, intraday, spot, year-end, or FBAR use.

## Proof Packet

Create a retained workpaper folder for every answered rate:

```text
work/fx-rate-proof/<currency>-<year>-<source-slug>/
```

The folder must include:

- `workpaper.md`, a human-readable source note.
- `workpaper.json`, structured metadata for reuse by other workflows.
- `workpaper.pdf`, a printable summary workpaper with the rate, source, caveats, and saved source proof/hash.
- At least one saved source proof artifact: HTML snapshot, source data file, PDF save/print, screenshot, or equivalent retained source file.

For IRS lookups, the script saves the IRS HTML snapshot and hashes it. For non-IRS sources, save a screenshot/PDF/HTML/source file before running `manual`; the script copies that artifact into the workpaper folder and hashes it.

Do not fill the printable PDF with links to the workpaper's own PDF/MD/JSON files. Those artifact links belong in the final chat output. Inside the PDF, list only the source proof artifact filename or packet-relative path and its hash so the workpaper stays readable and useful to reviewers who cannot access local computer paths.

## IRS Map Refresh

The script keeps a small IRS row-to-ISO map so ambiguous currency labels such as `Dollar` or `Peso` do not get guessed. When the IRS yearly-average table changes, run:

```bash
python3 "<package-root>/scripts/get_yearly_fx_rate.py" map-check
```

If `map-check` reports unmapped IRS rows, update `IRS_ROWS_BY_CODE` and any needed aliases in `scripts/get_yearly_fx_rate.py`, then rerun `self-test` and `map-check`.

## Final Answer

Keep the user-facing answer concise and use this shape:

```markdown
Rate: 1 USD = <rate> <CURRENCY> yearly average
Reciprocal: 1 <CURRENCY> = <usd-rate> USD
Source: <source title>, <URL>, retrieved <date>
Proof: [workpaper.pdf](<absolute path to workpaper.pdf>)
Artifacts: [workpaper.pdf](<absolute path to workpaper.pdf>), [workpaper.md](<absolute path to workpaper.md>), [workpaper.json](<absolute path to workpaper.json>), [source-proof-1.ext](<absolute path to saved source proof>)
```

Use Markdown links for every retained local artifact so the chat UI can expose them as clickable/downloadable files. Wrap link targets in angle brackets because workspace paths may contain spaces. Do not wrap proof paths in backticks.

A Markdown link to a local absolute path is only clickable/downloadable when the chat client has direct filesystem access to this machine, true for a local desktop session but not for a hosted/remote session (for example, Claude Code on the web) where the user's browser cannot reach this container's filesystem. When running in such a session, also deliver each retained artifact using the host's file-delivery capability (for example, Claude Code's `SendUserFile` tool) in addition to the links above.

Add one short caveat only when needed, such as `The IRS table did not list this currency, so this uses a non-IRS published annual average.` Do not call the rate IRS-approved.

## Runtime And Validation

The script uses only Python standard-library modules. Use `python3` unless the active environment provides `python`.

`scripts/get_yearly_fx_rate.py` imports `scripts/_workpaper.py`, a generated, byte-identical copy of the shared `workpaper-kit/workpaper.py` proof-packet engine. Do not hand-edit `_workpaper.py`; update the canonical kit and run `workpaper-kit/sync.sh` to regenerate both FX skills.

After changing this skill, run:

```bash
python3 "<package-root>/scripts/get_yearly_fx_rate.py" self-test
python3 "<package-root>/scripts/get_yearly_fx_rate.py" map-check
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
```

If the Anthropic skill-creator `quick_validate.py` is installed, also run it against `<package-root>`; otherwise the strict inspector above and `claude plugin validate` below are the release gate.

If Claude tooling is available, also run:

```bash
claude plugin validate --strict "<package-root>"
```

## Package Compatibility

This is a single multi-agent package. The root `SKILL.md`, `references/`, and `scripts/` are the source of truth. `agents/openai.yaml` is OpenAI/Codex discovery metadata only. `.claude-plugin/plugin.json` and `commands/get-yearly-fx-rate.md` are Claude adapters only; do not duplicate workflow or source policy in adapter files.
