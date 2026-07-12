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


def extract(
    work: Path, tag: str, pdfs: list[Path], handoff: Path, *, account_currency: str | None = None
) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, object] | None]:
    output = work / f"{tag}-account.json"
    command = [
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
    ]
    if account_currency:
        command.extend(["--account-currency", account_currency])
    process = run(command)
    return process, output, read_json(output)


def create_reviewed_handoff(
    work: Path, tag: str, source: Path, data: dict[str, object]
) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, object] | None]:
    output = work / f"{tag}-reviewed-handoff.json"
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

    handoff_process, handoff_path, handoff_data = create_reviewed_handoff(work, "review", review_path, review_data or {})
    reviewed_process, _reviewed_path, reviewed_data = extract(work, "reviewed", [reviewed_pdf], handoff_path)
    handoff_status = reviewed_data.get("preflight", {}).get("status") if isinstance(reviewed_data, dict) and isinstance(reviewed_data.get("preflight"), dict) else None
    reviewed_account = reviewed_data.get("account", {}) if isinstance(reviewed_data, dict) and isinstance(reviewed_data.get("account"), dict) else {}
    reviewed_profile = reviewed_data.get("preflight", {}) if isinstance(reviewed_data, dict) and isinstance(reviewed_data.get("preflight"), dict) else {}
    reviewed_resolutions = reviewed_profile.get("user_resolutions") if isinstance(reviewed_profile, dict) else None
    check(
        "REVIEW-2 reviewed currency and scope resolutions control extraction and are retained",
        handoff_process.returncode == 0 and handoff_data is not None and reviewed_process.returncode == 0
        and handoff_status == "reviewed-for-domain-extraction" and isinstance(reviewed_account, dict)
        and reviewed_account.get("currency") == "COP" and reviewed_account.get("account_id") == "reviewed-one-account-2025"
        and reviewed_account.get("account_id_source") == "reviewed-one-account-local-label"
        and isinstance(reviewed_resolutions, dict)
        and isinstance(reviewed_resolutions.get("currency"), dict)
        and reviewed_resolutions["currency"].get("code") == "COP",
        reviewed_process.stderr.strip(),
    )

    currency_conflict_process, _currency_conflict_path, _currency_conflict_data = extract(
        work, "reviewed-currency-conflict", [reviewed_pdf], handoff_path, account_currency="USD"
    )
    check(
        "REVIEW-3 a CLI currency cannot override the reviewed handoff resolution",
        currency_conflict_process.returncode == 2 and "conflicts with the user-confirmed currency" in currency_conflict_process.stderr,
        currency_conflict_process.stderr.strip(),
    )

    tampered_handoff = work / "review-tampered-resolution.json"
    tampered_data = read_json(handoff_path) or {}
    tampered_resolutions = tampered_data.get("user_resolutions") if isinstance(tampered_data, dict) else None
    if isinstance(tampered_resolutions, dict):
        tampered_resolutions["source_preflight_sha256"] = "0" * 64
    tampered_handoff.write_text(json.dumps(tampered_data, indent=2) + "\n", encoding="utf-8")
    tampered_process, _tampered_path, _tampered_data = extract(work, "reviewed-tampered", [reviewed_pdf], tampered_handoff)
    check(
        "REVIEW-4 a source-unbound reviewed resolution is rejected before parsing",
        tampered_process.returncode == 2 and "not bound to the reviewed source preflight" in tampered_process.stderr,
        tampered_process.stderr.strip(),
    )

    invalid_currency_handoff = work / "review-invalid-currency.json"
    invalid_currency_data = read_json(handoff_path) or {}
    invalid_currency_resolutions = invalid_currency_data.get("user_resolutions") if isinstance(invalid_currency_data, dict) else None
    invalid_currency = invalid_currency_resolutions.get("currency") if isinstance(invalid_currency_resolutions, dict) else None
    if isinstance(invalid_currency, dict):
        invalid_currency["code"] = "ZZZ"
    invalid_currency_handoff.write_text(json.dumps(invalid_currency_data, indent=2) + "\n", encoding="utf-8")
    invalid_currency_process, _invalid_currency_path, _invalid_currency_data = extract(
        work, "reviewed-invalid-currency", [reviewed_pdf], invalid_currency_handoff
    )
    check(
        "REVIEW-5 a reviewed currency must be a supported ISO code",
        invalid_currency_process.returncode == 2 and "supported ISO currency code" in invalid_currency_process.stderr,
        invalid_currency_process.stderr.strip(),
    )

    review_path.write_text(review_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stale_handoff_process, _stale_path, _stale_data = extract(work, "stale-handoff", [reviewed_pdf], handoff_path)
    check(
        "REVIEW-6 a reviewed handoff rejects a source preflight changed after review",
        stale_handoff_process.returncode == 2 and "changed after review" in stale_handoff_process.stderr,
        stale_handoff_process.stderr.strip(),
    )

    missing_midyear = [work / "missing-mid-q1.pdf", work / "missing-mid-q3.pdf", work / "missing-mid-q4.pdf"]
    make_pdf(missing_midyear[0], [
        "Example Bank Quarterly Statement",
        "Account Number: 55556666",  # privacy-gate: allow (synthetic account fixture)
        "Statement period January 1 2025 to March 31 2025",
        "Currency USD",
        "2025-01-01 Closing balance 9,500.00 USD",
        "2025-03-31 Closing balance 9,500.00 USD",
    ])
    make_pdf(missing_midyear[1], [
        "Example Bank Quarterly Statement",
        "Account Number: 55556666",  # privacy-gate: allow (synthetic account fixture)
        "Statement period July 1 2025 to September 30 2025",
        "Currency USD",
        "2025-07-01 Closing balance 9,600.00 USD",
        "2025-09-30 Closing balance 9,600.00 USD",
    ])
    make_pdf(missing_midyear[2], [
        "Example Bank Quarterly Statement",
        "Account Number: 55556666",  # privacy-gate: allow (synthetic account fixture)
        "Statement period October 1 2025 to December 31 2025",
        "Currency USD",
        "2025-10-01 Closing balance 9,700.00 USD",
        "2025-12-31 Closing balance 9,700.00 USD",
    ])
    mid_process, mid_path, mid_data = preflight(work, "missing-midyear", missing_midyear)
    mid_handoff_process, mid_handoff_path, _mid_handoff_data = create_reviewed_handoff(
        work, "missing-midyear", mid_path, mid_data or {}
    )
    mid_extract_process, _mid_account_path, mid_account = extract(work, "missing-midyear", missing_midyear, mid_handoff_path)
    mid_coverage = mid_account.get("coverage", {}) if isinstance(mid_account, dict) and isinstance(mid_account.get("coverage"), dict) else {}
    mid_warnings = mid_account.get("warnings", []) if isinstance(mid_account, dict) else []
    mid_preflight = mid_account.get("preflight", {}) if isinstance(mid_account, dict) and isinstance(mid_account.get("preflight"), dict) else {}
    mid_hints = mid_preflight.get("coverage_hints", {}) if isinstance(mid_preflight, dict) else {}
    mid_gaps = mid_coverage.get("carry_gaps", []) if isinstance(mid_coverage, dict) else []
    check(
        "COVERAGE-1 an omitted mid-year statement stays review-required with source and carry-gap evidence",
        mid_process.returncode == 0 and mid_handoff_process.returncode == 0 and mid_extract_process.returncode == 0
        and isinstance(mid_data, dict) and any(isinstance(gate, dict) and gate.get("code") == "possible-missing-statement-period" for gate in mid_data.get("review_gates", []))
        and isinstance(mid_hints, dict) and isinstance(mid_hints.get("period_coverage_review"), dict)
        and isinstance(mid_gaps, list) and any(isinstance(gap, dict) and int(gap.get("days", 0)) > 40 for gap in mid_gaps)
        and isinstance(mid_warnings, list) and any("possible calendar coverage gap" in str(warning) for warning in mid_warnings)
        and isinstance(mid_account, dict) and mid_account.get("status") == "extracted-review-required" and "daily_threshold" not in mid_account,
        mid_extract_process.stderr.strip(),
    )

    missing_year_end = [work / "missing-year-end-q1.pdf", work / "missing-year-end-q2.pdf", work / "missing-year-end-q3.pdf"]
    for path, start, end, first_date, last_date in (
        (missing_year_end[0], "January 1", "March 31", "2025-01-01", "2025-03-31"),
        (missing_year_end[1], "April 1", "June 30", "2025-04-01", "2025-06-30"),
        (missing_year_end[2], "July 1", "September 30", "2025-07-01", "2025-09-30"),
    ):
        make_pdf(path, [
            "Example Bank Quarterly Statement",
            "Account Number: 77778888",  # privacy-gate: allow (synthetic account fixture)
            f"Statement period {start} 2025 to {end} 2025",
            "Currency USD",
            f"{first_date} Closing balance 9,800.00 USD",
            f"{last_date} Closing balance 9,800.00 USD",
        ])
    end_process, end_path, end_data = preflight(work, "missing-year-end", missing_year_end)
    end_handoff_process, end_handoff_path, _end_handoff_data = create_reviewed_handoff(
        work, "missing-year-end", end_path, end_data or {}
    )
    end_extract_process, _end_account_path, end_account = extract(work, "missing-year-end", missing_year_end, end_handoff_path)
    end_coverage = end_account.get("coverage", {}) if isinstance(end_account, dict) and isinstance(end_account.get("coverage"), dict) else {}
    end_warnings = end_account.get("warnings", []) if isinstance(end_account, dict) else []
    check(
        "COVERAGE-2 an omitted year-end statement retains trailing-gap warnings and no daily answer",
        end_process.returncode == 0 and end_handoff_process.returncode == 0 and end_extract_process.returncode == 0
        and isinstance(end_data, dict) and any(isinstance(gate, dict) and gate.get("code") == "possible-missing-statement-period" for gate in end_data.get("review_gates", []))
        and isinstance(end_coverage, dict) and int(end_coverage.get("trailing_carry_days", 0)) > 40
        and end_coverage.get("carry_gap_review_required") is True
        and isinstance(end_warnings, list) and any("final" in str(warning) and "carried forward" in str(warning) for warning in end_warnings)
        and isinstance(end_account, dict) and end_account.get("status") == "extracted-review-required" and "daily_threshold" not in end_account,
        end_extract_process.stderr.strip(),
    )


failed = [result for result in results if not result[1]]
for name, passed, detail in results:
    print(f"{'PASS' if passed else 'FAIL'}  {name:<86} {detail}")
print(f"\n{len(results) - len(failed)}/{len(results)} passed, {len(failed)} failed")
raise SystemExit(1 if failed else 0)
