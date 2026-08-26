# Synthetic FBAR Proof Demo

This example runs the repository's real statement-preflight, FBAR threshold,
and year-end FX command-line tools against four invented quarterly COP
statements. Every person, account identifier, balance, transaction, and
institution name is fictional. The PDFs say so on the page.

This is a software demonstration and support-artifact example. It does not
prepare or file FinCEN Form 114, decide whether anyone must file, or provide tax
or legal advice.

## Run it

From the repository root:

```bash
examples/fbar-proof-demo/run_demo.py
```

The launcher uses the current Python when `reportlab`, `pdfplumber`, and Pillow
are available. In Codex Desktop it can relaunch itself with the bundled artifact
runtime when plain `python3` is intentionally minimal. Poppler's `pdftoppm` is
needed only for the public PNG previews; pass `--skip-media` to test the core
proof workflow without regenerating media.

The demo deliberately uses the frozen
`get-year-end-fx-rate/tests/fixtures/treasury-2025-12-31.json` response. It does
not call the network. Source PDFs are generated deterministically; the workflow
CLIs still record the current run time and local artifact paths in their normal
provenance fields. Before the final packet is sealed, the committed text samples
replace any private checkout prefix with the literal `${REPO_ROOT}` token and
rebind the reviewed handoff's source-preflight hash. Aggregation then records
safe packet-relative paths, binds the final published bytes, and successfully
reverifies the packet after relocation. The generated PDF evidence is unchanged
by path sanitization.

## What it proves

The happy path:

1. Generates four machine-readable, quarterly synthetic statements for account
   `00000042`.
2. Runs `statement-intake-preflight --scope one-account
   --require-institution` and gets exactly one review gate:
   `unknown-institution`.
3. Creates a source-bound reviewed handoff with the conspicuously fictional
   institution `Fictional Orbit Bank - Synthetic Demo Only`.
4. Extracts the real 365-row ledger and asserts 20 observed dates, zero missing
   days, no long carry gap, and a maximum native balance of 10,500,000 COP on
   2025-10-10.
5. Retains the frozen Treasury/Fiscal Data source response and workpaper for
   `1 USD = 3773.62 COP` on 2025-12-31.
6. Confirms the reviewed synthetic ledger, then writes the schema 1.4 aggregate
   JSON, interval-aware CSV, PDF, and portable postflight manifest. It reruns
   `verify-packet` against the published bytes. Both the daily threshold and
   FinCEN maximum-value views are `no` for this fixture, with lower/upper answers
   both `no` and an integrity result of `pass`.

The refusal path omits Q2, then asserts all fail-closed behavior:

- Preflight returns exit `3` with `possible-missing-statement-period`.
- Extraction reports `daily_threshold: insufficient-records` and
  `maximum_account_value: not-determinable`.
- No daily threshold decision field is emitted.
- `confirm-account` returns exit `2` and writes no confirmed ledger because the
  91-day carry-forward gap was not explicitly accepted.

`ledger-review.json` is a demo-only record of deterministic fixture assertions.
In a real workflow, the process must stop at that point and wait for the user to
review and confirm the ledger.

## Retained artifacts

Key happy-path outputs:

- [`fbar-2025-summary.json`](artifacts/happy-path/fbar-2025-summary.json)
- [`fbar-2025-summary.csv`](artifacts/happy-path/fbar-2025-summary.csv)
- [`fbar-2025-summary.pdf`](artifacts/happy-path/fbar-2025-summary.pdf)
- [`fbar-2025-summary-postflight.json`](artifacts/happy-path/fbar-2025-summary-postflight.json)
- [`fbar-2025-summary-verification.json`](artifacts/happy-path/fbar-2025-summary-verification.json)
- [`account-ledger.csv`](artifacts/happy-path/account-ledger.csv)
- [`statement-preflight-reviewed.json`](artifacts/happy-path/statement-preflight-reviewed.json)
- [`FX workpaper.json`](artifacts/happy-path/fx-proof/cop-2025-treasury-reporting-rates-of-exchange-fiscal-data/workpaper.json)
- [`FX workpaper.pdf`](artifacts/happy-path/fx-proof/cop-2025-treasury-reporting-rates-of-exchange-fiscal-data/workpaper.pdf)

Refusal evidence:

- [`refusal.json`](artifacts/refusal-path/refusal.json)
- [`insufficient account ledger`](artifacts/refusal-path/account-ledger.json)
- [`missing-period preflight`](artifacts/refusal-path/statement-preflight.json)

Run-level evidence:

- [`demo-manifest.json`](demo-manifest.json)
- [`demo-run.txt`](demo-run.txt)
- [`checksums.sha256`](checksums.sha256)
- [36-second terminal recording](../../docs/assets/demo/fbar-proof-demo-terminal.gif)
- [reduced-motion terminal poster](../../docs/assets/demo/fbar-proof-demo-terminal-poster.png)
- [rendered summary page](../../docs/assets/demo/fbar-proof-summary-page-1.png)
- [rendered FX proof page](../../docs/assets/demo/fbar-fx-proof-page-1.png)

The checksum manifest covers the statement fixtures, retained workflow packet
including postflight/verification JSON, run transcript, demo manifest, and
public media. It intentionally excludes both copies of the checksum manifest
itself. `run_demo.py --check` runs the current code in scratch and independently
rechecks that the committed sample schemas, artifact inventory, and portable
postflight result are current.
