#!/usr/bin/env python3
"""Real-PDF contract suite for statement-intake-preflight -> FBAR extraction.

This test drives both command-line tools, rather than calling internal helpers,
so it catches disagreements about the handoff JSON, PDF fingerprints, or CLI
arguments. It needs the required sibling `statement-intake-preflight` skill and
the test-only `reportlab` and `pdfplumber` dependencies:

    python3 fbar-threshold-check/tests/run_preflight_integration.py

Exit code: 0 when every scenario passes (or test-only PDF dependencies are not
available); 1 when an integration assertion fails. Fixtures are synthetic.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:  # Test-only dependencies; keep a dependency-free CI runner green.
    from reportlab.pdfgen import canvas
    import pdfplumber  # noqa: F401
except BaseException as exc:  # noqa: BLE001 - a broken native dependency may not raise ImportError.
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise
    print(f"SKIP: integration suite needs reportlab + pdfplumber ({type(exc).__name__}); not run.")
    raise SystemExit(0)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_ROOT = PACKAGE_ROOT.parent / "statement-intake-preflight"
FBAR_SCRIPT = PACKAGE_ROOT / "scripts" / "fbar_threshold_check.py"
PREFLIGHT_SCRIPT = PREFLIGHT_ROOT / "scripts" / "statement_intake_preflight.py"
results: list[tuple[str, bool, str]] = []


def make_pdf(path: Path, lines: list[str]) -> None:
    document = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        if y < 60:
            document.showPage()
            y = 800
        document.drawString(40, y, line)
        y -= 14
    document.save()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))


def preflight(work: Path, tag: str, pdfs: list[Path]) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, object] | None]:
    output = work / f"{tag}-preflight.json"
    process = run([
        sys.executable,
        str(PREFLIGHT_SCRIPT),
        "preflight",
        "--pdf",
        *(str(path) for path in pdfs),
        "--tax-year",
        "2025",
        "--scope",
        "one-account",
        "--out",
        str(output),
    ])
    return process, output, read_json(output)


def extract(work: Path, tag: str, pdfs: list[Path], handoff: Path) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, object] | None]:
    output = work / f"{tag}-account.json"
    process = run([
        sys.executable,
        str(FBAR_SCRIPT),
        "extract-account",
        "--pdf",
        *(str(path) for path in pdfs),
        "--tax-year",
        "2025",
        "--preflight-json",
        str(handoff),
        "--out",
        str(output),
    ])
    return process, output, read_json(output)


def create_reviewed_handoff(work: Path, source: Path, data: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, object] | None]:
    output = work / "reviewed-handoff.json"
    gates = data.get("review_gates")
    gate_codes = [str(gate.get("code")) for gate in gates if isinstance(gate, dict) and gate.get("code")] if isinstance(gates, list) else []
    command = [sys.executable, str(PREFLIGHT_SCRIPT), "review-handoff", "--input", str(source)]
    for code in gate_codes:
        command.extend(["--accept-gate", code])
    command.extend(["--user-review-confirmed", "--out", str(output)])
    # Supply only the structured resolutions requested by the generated
    # preflight. The handoff keeps these separate from its raw evidence.
    if {"ambiguous-dollar", "unknown-currency"} & set(gate_codes):
        command.extend(["--confirm-currency", "COP"])
    if {"unknown-account", "possible-mixed-accounts"} & set(gate_codes):
        command.append("--confirm-one-account")
    if {"unresolved-year-evidence", "unknown-year-coverage"} & set(gate_codes):
        command.extend(["--confirm-statement-year", "2025"])
    process = run(command)
    return process, output, read_json(output)


if not PREFLIGHT_SCRIPT.is_file():
    print(f"FAIL: required sibling preflight script is missing: {PREFLIGHT_SCRIPT}")
    raise SystemExit(1)

with tempfile.TemporaryDirectory(prefix="fbar-preflight-integration-") as temporary:
    work = Path(temporary)
    january = work / "jan-sep.pdf"
    february = work / "oct-dec.pdf"
    make_pdf(january, [
        "Example Bank Statement",
        "Account Number: 12345678",  # privacy-gate: allow (synthetic account fixture)
        "Statement period January 1 2025 to September 30 2025",
        "Currency USD",
        "2025-01-01 Closing balance 9,999.00 USD",
        "2025-09-30 Closing balance 10,001.00 USD",
    ])
    make_pdf(february, [
        "Example Bank Statement",
        "Account Number: 12345678",  # privacy-gate: allow (synthetic account fixture)
        "Statement period October 1 2025 to December 31 2025",
        "Currency USD",
        "2025-10-01 Closing balance 10,002.00 USD",
        "2025-12-31 Closing balance 10,003.00 USD",
    ])

    ready_process, ready_path, ready_data = preflight(work, "ready", [january, february])
    ready = ready_process.returncode == 0 and ready_data is not None and ready_data.get("status") == "ready-for-domain-extraction"
    check("READY-1 full-year real statement PDFs produce a clean preflight", ready, ready_process.stderr.strip())

    extract_process, _account_path, account_data = extract(work, "ready", [january, february], ready_path)
    verified = account_data.get("preflight", {}).get("verified_statement_files", []) if isinstance(account_data, dict) and isinstance(account_data.get("preflight"), dict) else []
    observed = account_data.get("coverage", {}).get("observed_days") if isinstance(account_data, dict) and isinstance(account_data.get("coverage"), dict) else None
    expected = ready_data.get("statement_files", []) if isinstance(ready_data, dict) else []
    fingerprints_match = isinstance(expected, list) and isinstance(verified, list) and [
        (item.get("resolved_file"), item.get("content_bytes"), item.get("content_sha256"))
        for item in expected if isinstance(item, dict)
    ] == [
        (item.get("resolved_file"), item.get("content_bytes"), item.get("content_sha256"))
        for item in verified if isinstance(item, dict)
    ]
    check(
        "READY-2 FBAR extracts the preflighted real PDFs and records verified fingerprints",
        extract_process.returncode == 0 and account_data is not None and observed == 4 and fingerprints_match,
        extract_process.stderr.strip(),
    )

    reordered_process, _reordered_path, _reordered_data = extract(work, "reordered", [february, january], ready_path)
    check(
        "IDENTITY-1 reordered PDFs are rejected before extraction",
        reordered_process.returncode == 2 and "Preflight PDF sequence" in reordered_process.stderr,
        reordered_process.stderr.strip(),
    )

    changed = work / "changed.pdf"
    make_pdf(changed, [
        "Example Bank Monthly Statement",
        "Account Number: 22223333",  # privacy-gate: allow (synthetic account fixture)
        "Statement period March 1 2025 to March 31 2025",
        "Currency USD",
        "2025-03-01 Closing balance 9,500.00 USD",
    ])
    changed_preflight_process, changed_preflight_path, changed_preflight_data = preflight(work, "changed", [changed])
    changed.write_bytes(changed.read_bytes() + b"\n% changed after preflight\n")
    changed_process, _changed_path, _changed_data = extract(work, "changed", [changed], changed_preflight_path)
    check(
        "IDENTITY-2 a changed PDF is rejected before parsing",
        changed_preflight_process.returncode == 0 and changed_preflight_data is not None
        and changed_process.returncode == 2 and "changed after preflight" in changed_process.stderr,
        changed_process.stderr.strip(),
    )

    reviewed_pdf = work / "reviewed.pdf"
    make_pdf(reviewed_pdf, [
        "Example Bank Monthly Statement",
        "Account Number: 44445555",  # privacy-gate: allow (synthetic account fixture)
        "Statement period April 1 2025 to April 30 2025",
        "2025-04-01 Closing balance $9,999.00",
        "2025-04-30 Closing balance $10,001.00",
    ])
    review_process, review_path, review_data = preflight(work, "review", [reviewed_pdf])
    raw_review_process, _raw_review_path, _raw_review_data = extract(work, "raw-review", [reviewed_pdf], review_path)
    review_gates = review_data.get("review_gates") if isinstance(review_data, dict) else []
    check(
        "REVIEW-1 a real review-required preflight cannot be used directly",
        review_process.returncode == 0 and isinstance(review_gates, list) and bool(review_gates)
        and raw_review_process.returncode == 2 and "review-required" in raw_review_process.stderr,
        raw_review_process.stderr.strip(),
    )

    handoff_process, handoff_path, handoff_data = create_reviewed_handoff(work, review_path, review_data or {})
    reviewed_process, _reviewed_path, reviewed_data = extract(work, "reviewed", [reviewed_pdf], handoff_path)
    handoff_status = reviewed_data.get("preflight", {}).get("status") if isinstance(reviewed_data, dict) and isinstance(reviewed_data.get("preflight"), dict) else None
    check(
        "REVIEW-2 a reviewed handoff from the real preflight proceeds to extraction",
        handoff_process.returncode == 0 and handoff_data is not None and reviewed_process.returncode == 0
        and handoff_status == "reviewed-for-domain-extraction",
        reviewed_process.stderr.strip(),
    )

    review_path.write_text(review_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stale_handoff_process, _stale_path, _stale_data = extract(work, "stale-handoff", [reviewed_pdf], handoff_path)
    check(
        "REVIEW-3 a reviewed handoff rejects a source preflight changed after review",
        stale_handoff_process.returncode == 2 and "changed after review" in stale_handoff_process.stderr,
        stale_handoff_process.stderr.strip(),
    )


failed = [result for result in results if not result[1]]
for name, passed, detail in results:
    print(f"{'PASS' if passed else 'FAIL'}  {name:<86} {detail}")
print(f"\n{len(results) - len(failed)}/{len(results)} passed, {len(failed)} failed")
raise SystemExit(1 if failed else 0)
