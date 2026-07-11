# workpaper-kit

The shared workpaper / proof-packet engine behind `get-yearly-fx-rate`,
`get-year-end-fx-rate`, and the rich PDF packet used by
`statements-to-interest`.

This is **internal plumbing, not a skill**: there is no `SKILL.md`, no command
adapter, and no agent trigger. Its FX/proof-packet core is pure standard
library. Its optional rich-packet surface lazily uses ReportLab only in skills
that already require it, such as `statements-to-interest`.

## Why this exists

The two FX skills are template siblings. Their data-source layers are
fundamentally different (IRS HTML scrape vs Treasury/Fiscal Data JSON API) and
their domain rules invert (yearly *requires* an annual average; year-end
*rejects* average language), so those stay per-skill. What they genuinely share
is the downstream machine that runs *after* a rate is known: copy + SHA-256 the
source proof, name the packet folder, render `workpaper.md` + `workpaper.json` +
`workpaper.pdf`, and emit a cited answer with markdown file links. That machine
had drifted two ways (the PDF layer was a class in one skill and free functions
in the other, with different palettes). The kit is the single, hardened copy of
exactly that machine — nothing else.

The kit **never fetches, never parses a rate, and never touches currency
aliases or source layers.** A caller hands it an already-parsed `Decimal` and a
`WorkpaperSpec`; the kit computes the reciprocal and writes the packet.

## Rich PDF packets

`ReportlabPacketRenderer` is the common layout layer for support packets that
need ReportLab flowables rather than the FX workpaper's deterministic raw-PDF
format. It provides the shared style sheet, hero/header, KPI cards, paginating
tables, review-note boxes, footer, and document build step. The caller keeps
domain decisions, row extraction, and redaction; pass only already-redacted
display values to the renderer.

The renderer imports ReportLab only when instantiated. Therefore the FX skills
remain usable with Python's standard library alone, while
`statements-to-interest` retains its existing ReportLab runtime requirement.

## Interface

```python
from _workpaper import (
    WorkpaperSpec,
    build_workpaper,      # writes md + json + pdf, returns the workpaper dict
    final_text,           # cited-answer string with a workpaper.pdf link
    artifact_links,       # comma-joined markdown links to every artifact
    markdown_file_link,   # one escaped [label](<path>) link
    RateError,            # shared error type (message, exit code)
)

workpaper = build_workpaper(spec)   # spec: WorkpaperSpec
print(final_text(workpaper))
```

`build_workpaper` writes into
`<output_root>/<code>-<year>-<slug(source_title)>/` and always warns to stderr
when it replaces an existing packet.

### `WorkpaperSpec`

| Field | Purpose |
| --- | --- |
| `output_root`, `skill_name`, `currency_code`, `year` | identity / location |
| `rate`, `rate_direction` | the already-parsed rate; the kit derives the reciprocal |
| `source_title`, `source_url`, `source_category`, `retrieval_date`, `source_note` | the common source block |
| `document_title`, `document_subtitle`, `rate_phrase`, `caveats` | presentation — the divergence knobs |
| `extra_rows` | extra `label: value` rows (md between Year and Rate; pdf rate-detail rows) |
| `extra_json` | deep-merged into `workpaper.json` (nested dicts merge, so `source.year_end_confirmed` does not clobber the source block) |
| `saved_proofs`, `proof_required`, `proof_limitations` | proof policy — `proof_required=True` raises on a missing proof file; `False` skips it |

All per-skill divergence is absorbed by these fields. The proof-entry schema
(`filename` / `packet_relative_path` / `path` / `sha256`) is identical for both
skills and lives in the kit.

## Design invariants (do not break)

- **The PDF embeds no absolute paths**, so `workpaper_pdf_sha256` — and thus
  `workpaper.json` — is deterministic and reviewer-portable. Keep it that way.
- Presentation fields ride the returned dict under a `_presentation` key that is
  **stripped before `workpaper.json` is written**, so the persisted JSON stays
  the consumer-facing schema. `final_text` reads `rate_phrase` from there.
- `workpaper.json` is written with `json.dumps(..., indent=2, sort_keys=True)`,
  so key order never matters and `extra_json` stays byte-compatible.
- The PDF engine is shared, so both skills render an identical-looking packet.
  The extraction was proven byte-identical to yearly's pre-extraction output
  (md + json + pdf); the PDF design has since evolved on purpose. `workpaper.md`
  never depends on the PDF, so a design change moves only `workpaper_pdf_sha256`
  in `workpaper.json` — when you touch the renderer, re-render a sample, look at
  the pages, and update the golden test's structural terms.

## Vendoring & sync

Skills install standalone, so a plain shared import would break with
`ImportError` once a skill is packaged on its own. Instead the canonical source
is **vendored**: `sync.sh` copies `workpaper.py` to a byte-identical
`scripts/_workpaper.py` inside each skill, which the skill imports locally
(`from _workpaper import ...`).

- **Edit `workpaper-kit/workpaper.py` only.** Never hand-edit a
  `scripts/_workpaper.py` copy — it is generated and will be overwritten.
- Regenerate the copies:

  ```bash
  workpaper-kit/sync.sh
  ```

- Verify the copies are in sync (used as a CI backstop; verify-only):

  ```bash
  workpaper-kit/sync.sh --check
  ```

Syncing is wired to run automatically from the repo's `.githooks/pre-commit`
hook (it regenerates the copies and `git add`s them so they ride the same
commit). Git will not run a tracked hook until you enable it once per clone:

```bash
git config core.hooksPath .githooks
```

## Tests

```bash
python3 workpaper-kit/test_workpaper.py     # golden / self-test
workpaper-kit/sync.sh --check               # vendored copies in sync
```

The golden test is hermetic (temp dir, no network) and pins the kit's own
contract: stable `workpaper.md` / `workpaper.json` bytes, proof hashing, the
deep-merge / extra-row / proof-policy knobs, link escaping, and PDF structural
terms.
