#!/usr/bin/env python3
"""Real-PDF contract suite for statement-intake-preflight -> FBAR extraction.

This test drives both command-line tools, rather than calling internal helpers,
so it catches disagreements about the handoff JSON, PDF fingerprints, or CLI
arguments. One late-mutation case calls the same extraction command through an
internal test seam because a subprocess cannot deterministically modify a PDF
after parsing but before the final fingerprint check. It needs the required
sibling `statement-intake-preflight` skill and the test-only `reportlab` and
`pdfplumber` dependencies:

    python3 fbar-threshold-check/tests/run_preflight_integration.py

Exit code: 0 when every scenario passes (or test-only PDF dependencies are not
available); 1 when an integration assertion fails. Fixtures are synthetic.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
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


def make_sanitized_cop_style_fixture(work: Path) -> list[Path]:
    """Generate redacted quarter statements for the Spanish-table FBAR path.

    Keep this fixture generated and synthetic: it models only the structural
    properties that previously failed on a real statement (page-bound
    ``DESDE/HASTA`` dates, a standalone ``NÚMERO`` account header, and short
    DD/MM table dates). It contains no statement-derived data.
    """
    quarters = (
        (
            "q1",
            "2025/01/01",
            "2025/03/31",
            [
                "1/01 SALDO INICIAL 9,800,000.00",
                "4/01 COMPRA 100,000.00 9,876,543.21",
                "31/01 CIERRE 25,000.00 9,850,000.00",
                "28/02 CIERRE 25,000.00 9,875,000.00",
                "31/03 CIERRE 50,000.00 9,900,000.00",
            ],
        ),
        (
            "q2",
            "2025/04/01",
            "2025/06/30",
            [
                "1/04 SALDO INICIAL 9,950,000.00",
                "30/04 CIERRE 20,000.00 9,930,000.00",
                "31/05 CIERRE 10,000.00 9,940,000.00",
                "15/06 ABONO 25,000.00 9,925,000.00",
                "30/06 CIERRE 35,000.00 9,960,000.00",
            ],
        ),
        (
            "q3",
            "2025/07/01",
            "2025/09/30",
            [
                "1/07 SALDO INICIAL 9,975,000.00",
                "31/07 CIERRE 25,000.00 10,000,000.00",
                "31/08 CIERRE 100,000.00 10,100,000.00",
                "15/09 ABONO 275,000.00 10,250,000.00",
                "30/09 CIERRE 50,000.00 10,200,000.00",
            ],
        ),
        (
            "q4",
            "2025/10/01",
            "2025/12/31",
            [
                "1/10 SALDO INICIAL 10,300,000.00",
                "10/10 AJUSTE -50,000.00 10,500,000.00",
                "31/10 CIERRE 100,000.00 10,400,000.00",
                "30/11 CIERRE 50,000.00 10,350,000.00",
                "31/12 CIERRE 250,000.00 10,250,000.00",
            ],
        ),
    )
    paths: list[Path] = []
    for quarter, start, end, rows in quarters:
        path = work / f"cop-style-{quarter}.pdf"
        make_pdf(path, [
            "ESTADO DE CUENTA",
            "CUENTA DE AHORROS",
            "NÚMERO 76543210",  # privacy-gate: allow (synthetic account fixture)
            f"DESDE {start} HASTA {end}",
            "MONEDA COP",
            "FECHA DETALLE MOVIMIENTO SALDO",
            *rows,
        ])
        paths.append(path)
    return paths


def make_compact_cop_column_fixture(work: Path, *, saldo_header: str = "SALDO") -> list[Path]:
    """Generate a redacted compact COP table with a final PDF-positioned Saldo cell.

    The unrelated numeric value between Descripción and Saldo is intentional:
    it proves the extractor chooses the final column by coordinates rather than
    a last-number heuristic. The fixture contains only invented values.
    """
    quarters = (
        (
            "q1",
            "2025/01/01",
            "2025/03/31",
            [
                ("1/01", "COMPRA REDACTADA", "9,800,000"),
                ("4/01", "COMPRA REDACTADA", "9,876,543.21"),
                ("31/01", "CIERRE REDACTADO", "9,850,000"),
                ("28/02", "CIERRE REDACTADO", "9,875,000"),
                ("31/03", "CIERRE REDACTADO", "9,900,000"),
            ],
        ),
        (
            "q2",
            "2025/04/01",
            "2025/06/30",
            [
                ("1/04", "COMPRA REDACTADA", "9,950,000"),
                ("30/04", "CIERRE REDACTADO", "9,930,000"),
                ("31/05", "CIERRE REDACTADO", "9,940,000"),
                ("15/06", "ABONO REDACTADO", "9,925,000"),
                ("30/06", "CIERRE REDACTADO", "9,960,000"),
            ],
        ),
        (
            "q3",
            "2025/07/01",
            "2025/09/30",
            [
                ("1/07", "COMPRA REDACTADA", "9,975,000"),
                ("31/07", "CIERRE REDACTADO", "10,000,000"),
                ("31/08", "CIERRE REDACTADO", "10,100,000"),
                ("15/09", "ABONO REDACTADO", "10,250,000"),
                ("30/09", "CIERRE REDACTADO", "10,200,000"),
            ],
        ),
        (
            "q4",
            "2025/10/01",
            "2025/12/31",
            [
                ("1/10", "COMPRA REDACTADA", "10,300,000"),
                ("10/10", "AJUSTE REDACTADO", "10,500,000"),
                ("31/10", "CIERRE REDACTADO", "10,400,000"),
                ("30/11", "CIERRE REDACTADO", "10,350,000"),
                ("31/12", "CIERRE REDACTADO", "10,250,000"),
            ],
        ),
    )
    paths: list[Path] = []
    for quarter, start, end, rows in quarters:
        path = work / f"compact-cop-{saldo_header.lower()}-{quarter}.pdf"
        document = canvas.Canvas(str(path))
        document.drawString(40, 800, "ESTADO DE CUENTA REDACTADO")
        document.drawString(40, 782, "NÚMERO 76543210")  # privacy-gate: allow (synthetic account fixture)
        document.drawString(40, 764, f"DESDE {start} HASTA {end}")
        document.drawString(40, 720, "FECHA")
        document.drawString(130, 720, "DESCRIPCIÓN")
        document.drawString(535, 720, saldo_header)
        y = 700
        for transaction_date, description, balance in rows:
            document.drawString(40, y, transaction_date)
            document.drawString(130, y, description)
            document.drawString(450, y, "700,001")
            document.drawString(535, y, balance)
            y -= 18
        document.save()
        paths.append(path)
    return paths


def make_period_end_only_quarterly_fixture(work: Path) -> list[Path]:
    """Generate four complete-period statements with summaries, not transactions.

    This guards the end-to-end distinction between an exact period-end summary
    and transaction-row balance evidence. All values and account identifiers
    are synthetic.
    """
    quarters = (
        ("q1", "January 1", "March 31", "9,800.00"),
        ("q2", "April 1", "June 30", "9,950.00"),
        ("q3", "July 1", "September 30", "10,100.00"),
        ("q4", "October 1", "December 31", "10,250.00"),
    )
    paths: list[Path] = []
    for quarter, start, end, balance in quarters:
        path = work / f"period-end-only-{quarter}.pdf"
        make_pdf(path, [
            "Example Bank Quarterly Statement",
            "Account Number: 44445555",  # privacy-gate: allow (synthetic account fixture)
            f"Statement period {start} 2025 to {end} 2025",
            "Currency USD",
            f"Statement ending balance {balance} USD",
        ])
        paths.append(path)
    return paths


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_fbar_module() -> object:
    """Load the command module only for the deterministic late-mutation seam."""
    spec = importlib.util.spec_from_file_location("fbar_threshold_check_integration_module", FBAR_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load FBAR script module: {FBAR_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))


def preflight(
    work: Path, tag: str, pdfs: list[Path], *, require_institution: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path, dict[str, object] | None]:
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
        *( ["--require-institution"] if require_institution else [] ),
        "--out",
        str(output),
    ])
    return process, output, read_json(output)


def extract(
    work: Path,
    tag: str,
    pdfs: list[Path],
    handoff: Path,
    *,
    account_currency: str | None = None,
    institution: str | None = None,
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
    if institution:
        command.extend(["--institution", institution])
    process = run(command)
    return process, output, read_json(output)


def create_reviewed_handoff(
    work: Path, tag: str, source: Path, data: dict[str, object], *, institution: str | None = None
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
    if {"unknown-institution", "possible-mixed-institutions"} & set(gate_codes) and institution:
        command.extend(["--confirm-institution", institution])
    process = run(command)
    return process, output, read_json(output)


if not PREFLIGHT_SCRIPT.is_file():
    print(f"FAIL: required sibling preflight script is missing: {PREFLIGHT_SCRIPT}")
    raise SystemExit(1)

fbar_module = load_fbar_module()

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

    # Complete quarterly statement-period coverage alone must not make four
    # labelled closing summaries look like an annual daily ledger. Exercise
    # preflight, extraction, JSON, and the generated review CSV together.
    period_end_only = make_period_end_only_quarterly_fixture(work)
    summary_preflight_process, summary_preflight_path, summary_preflight_data = preflight(
        work, "period-end-only", period_end_only
    )
    summary_extract_process, summary_account_path, summary_account_data = extract(
        work, "period-end-only", period_end_only, summary_preflight_path
    )
    summary_coverage = (
        summary_account_data.get("coverage", {})
        if isinstance(summary_account_data, dict) and isinstance(summary_account_data.get("coverage"), dict)
        else {}
    )
    summary_sufficiency = (
        summary_account_data.get("data_sufficiency", {})
        if isinstance(summary_account_data, dict) and isinstance(summary_account_data.get("data_sufficiency"), dict)
        else {}
    )
    summary_review = (
        summary_account_data.get("review_summary", {})
        if isinstance(summary_account_data, dict) and isinstance(summary_account_data.get("review_summary"), dict)
        else {}
    )
    summary_rows = summary_account_data.get("daily_ledger", []) if isinstance(summary_account_data, dict) else []
    period_end_rows = [
        row for row in summary_rows
        if isinstance(row, dict) and row.get("evidence_class") == "period-end-summary"
    ] if isinstance(summary_rows, list) else []
    expected_period_ends = {
        "2025-03-31": Decimal("9800.00"),
        "2025-06-30": Decimal("9950.00"),
        "2025-09-30": Decimal("10100.00"),
        "2025-12-31": Decimal("10250.00"),
    }
    period_end_values = {
        str(row.get("date")): Decimal(str(row.get("native_balance")))
        for row in period_end_rows
    }
    highest_summary = max(
        period_end_rows,
        key=lambda row: Decimal(str(row.get("native_balance"))),
        default=None,
    )
    summary_period_review = summary_review.get("period_end_summaries") if isinstance(summary_review, dict) else {}
    summary_warnings = summary_account_data.get("warnings", []) if isinstance(summary_account_data, dict) else []
    summary_artifacts = summary_account_data.get("artifacts", {}) if isinstance(summary_account_data, dict) else {}
    summary_csv = (
        Path(str(summary_artifacts.get("review_csv")))
        if isinstance(summary_artifacts, dict) and summary_artifacts.get("review_csv")
        else summary_account_path.with_name(summary_account_path.stem + "-review.csv")
    )
    try:
        summary_csv_rows = list(csv.DictReader(summary_csv.read_text(encoding="utf-8").splitlines()))
    except (OSError, csv.Error):
        summary_csv_rows = []
    summary_csv_period_ends = [
        row for row in summary_csv_rows if row.get("evidence_class") == "period-end-summary"
    ]
    summary_checks = {
        "complete_statement_periods": summary_preflight_process.returncode == 0
        and isinstance(summary_preflight_data, dict)
        and summary_preflight_data.get("status") == "ready-for-domain-extraction",
        "period_end_rows": period_end_values == expected_period_ends
        and all(row.get("balance_source") == "period-end-summary" and row.get("confidence") == "medium" for row in period_end_rows),
        "source_references": all(
            isinstance(row.get("source_refs"), list)
            and len(row["source_refs"]) == 2
            and period_end_only[index].name in str(row["source_refs"][0])
            and period_end_only[index].name in str(row["source_refs"][1])
            for index, row in enumerate(period_end_rows)
        ),
        "coverage_is_not_daily_proof": summary_coverage.get("observed_days") == 4
        and summary_coverage.get("transaction_observed_days") == 0
        and summary_coverage.get("period_end_summary_days") == 4
        and summary_coverage.get("missing_days") == 89
        and summary_coverage.get("complete_year") is False
        and isinstance(summary_coverage.get("carry_gaps"), list)
        and len(summary_coverage["carry_gaps"]) == 3
        and all(int(gap.get("days", 0)) > 40 for gap in summary_coverage["carry_gaps"] if isinstance(gap, dict)),
        "maximum_is_not_determined": isinstance(highest_summary, dict)
        and highest_summary.get("date") == "2025-12-31"
        and Decimal(str(highest_summary.get("native_balance"))) == Decimal("10250.00")
        and summary_sufficiency == {
            "evidence_profile": "period-end-only",
            "daily_threshold": {
                "answer": "insufficient-records",
                "reason_codes": ["period-end-only", "missing-opening-coverage", "long-carry-forward-gap"],
            },
            "maximum_account_value": {
                "answer": "not-determinable",
                "reason_codes": ["period-end-only", "missing-opening-coverage", "long-carry-forward-gap"],
            },
        }
        and isinstance(summary_account_data, dict)
        and "daily_threshold" not in summary_account_data,
        "review_artifacts": isinstance(summary_period_review, dict)
        and summary_period_review.get("count") == 4
        and summary_period_review.get("requires_user_review") is True
        and len(summary_csv_rows) == 365
        and len(summary_csv_period_ends) == 4
        and all(
            "period-end summary" in row.get("review_flags", "")
            and period_end_only[index].name in row.get("source_refs", "")
            for index, row in enumerate(summary_csv_period_ends)
        )
        and isinstance(summary_warnings, list)
        and any("carry-forward gap" in str(warning) for warning in summary_warnings),
    }
    check(
        "COVERAGE-0 four period-end-only statements retain exact summaries but refuse an annual maximum",
        summary_extract_process.returncode == 0 and all(summary_checks.values()),
        "" if all(summary_checks.values()) else json.dumps(summary_checks, sort_keys=True),
    )

    # Exercise the exact end-to-end shape that a Spanish, page-period-bound
    # balance table needs. The generated fixtures intentionally omit an issuer
    # name so the opt-in FBAR institution confirmation is part of the same
    # reviewed handoff, without using a real statement or account number.
    cop_style = make_sanitized_cop_style_fixture(work)
    spanish_process, spanish_path, spanish_data = preflight(
        work, "cop-style", cop_style, require_institution=True
    )
    spanish_handoff_process, spanish_handoff_path, spanish_handoff_data = create_reviewed_handoff(
        work,
        "cop-style",
        spanish_path,
        spanish_data or {},
        institution="Marca66",
    )
    spanish_extract_process, spanish_account_path, spanish_account_data = extract(
        work, "cop-style", cop_style, spanish_handoff_path
    )
    spanish_gates = spanish_data.get("review_gates") if isinstance(spanish_data, dict) else []
    spanish_currency = spanish_data.get("currency") if isinstance(spanish_data, dict) else None
    spanish_hints = spanish_data.get("account_hints") if isinstance(spanish_data, dict) else None
    spanish_files = spanish_data.get("statement_files") if isinstance(spanish_data, dict) else []
    spanish_periods = [
        item.get("period_intervals")
        for item in spanish_files
        if isinstance(item, dict)
    ] if isinstance(spanish_files, list) else []
    spanish_account = spanish_account_data.get("account") if isinstance(spanish_account_data, dict) else None
    spanish_coverage = spanish_account_data.get("coverage") if isinstance(spanish_account_data, dict) else None
    spanish_rows = spanish_account_data.get("daily_ledger") if isinstance(spanish_account_data, dict) else []
    observed_rows = {
        str(row.get("date")): row
        for row in spanish_rows
        if isinstance(row, dict) and row.get("balance_source") == "observed"
    } if isinstance(spanish_rows, list) else {}
    expected_observed = {
        "2025-01-01": Decimal("9800000.00"),
        "2025-01-04": Decimal("9876543.21"),
        "2025-01-31": Decimal("9850000.00"),
        "2025-02-28": Decimal("9875000.00"),
        "2025-03-31": Decimal("9900000.00"),
        "2025-04-01": Decimal("9950000.00"),
        "2025-04-30": Decimal("9930000.00"),
        "2025-05-31": Decimal("9940000.00"),
        "2025-06-15": Decimal("9925000.00"),
        "2025-06-30": Decimal("9960000.00"),
        "2025-07-01": Decimal("9975000.00"),
        "2025-07-31": Decimal("10000000.00"),
        "2025-08-31": Decimal("10100000.00"),
        "2025-09-15": Decimal("10250000.00"),
        "2025-09-30": Decimal("10200000.00"),
        "2025-10-01": Decimal("10300000.00"),
        "2025-10-10": Decimal("10500000.00"),
        "2025-10-31": Decimal("10400000.00"),
        "2025-11-30": Decimal("10350000.00"),
        "2025-12-31": Decimal("10250000.00"),
    }
    observed_values_match = {
        day: Decimal(str(row.get("native_balance"))) for day, row in observed_rows.items()
    } == expected_observed
    maximum_row = max(
        observed_rows.values(),
        key=lambda row: Decimal(str(row.get("native_balance"))),
        default=None,
    )
    october_tenth = observed_rows.get("2025-10-10", {})
    october_notes = october_tenth.get("notes") if isinstance(october_tenth, dict) else []
    october_sources = october_tenth.get("source_refs") if isinstance(october_tenth, dict) else []
    spanish_artifacts = spanish_account_data.get("artifacts") if isinstance(spanish_account_data, dict) else None
    spanish_csv = (
        Path(str(spanish_artifacts.get("review_csv")))
        if isinstance(spanish_artifacts, dict) and spanish_artifacts.get("review_csv")
        else spanish_account_path.with_name(spanish_account_path.stem + "-review.csv")
    )
    try:
        spanish_csv_text = spanish_csv.read_text(encoding="utf-8")
    except OSError:
        spanish_csv_text = ""
    try:
        spanish_csv_rows = list(csv.DictReader(spanish_csv_text.splitlines()))
    except csv.Error:
        spanish_csv_rows = []
    reviewed_institution = (
        spanish_handoff_data.get("user_resolutions", {}).get("institution")
        if isinstance(spanish_handoff_data, dict)
        and isinstance(spanish_handoff_data.get("user_resolutions"), dict)
        else None
    )
    flow_checks = {
        "preflight": spanish_process.returncode == 0 and isinstance(spanish_data, dict)
        and spanish_data.get("status") == "review-required"
        and isinstance(spanish_gates, list)
        and [gate.get("code") for gate in spanish_gates if isinstance(gate, dict)] == ["unknown-institution"],
        "intake_evidence": isinstance(spanish_currency, dict) and spanish_currency.get("code") == "COP"
        and spanish_hints == ["76543210"]
        and len(spanish_periods) == 4
        and all(isinstance(periods, list) and len(periods) == 1 for periods in spanish_periods),
        "reviewed_handoff": spanish_handoff_process.returncode == 0
        and isinstance(reviewed_institution, dict)
        and reviewed_institution.get("name") == "Marca66",
        "account": spanish_extract_process.returncode == 0
        and isinstance(spanish_account, dict)
        and spanish_account.get("institution") == "Marca66"
        and spanish_account.get("currency") == "COP"
        and spanish_account.get("account_number_hints") == ["76543210"],
        "coverage": isinstance(spanish_coverage, dict)
        and spanish_coverage.get("complete_year") is True
        and spanish_coverage.get("missing_days") == 0
        and spanish_coverage.get("observed_days") == len(expected_observed)
        and spanish_coverage.get("carry_gaps") == [],
        "observed_balances": observed_values_match,
        "maximum_candidate": isinstance(maximum_row, dict)
        and maximum_row.get("date") == "2025-10-10"
        and Decimal(str(maximum_row.get("native_balance"))) == Decimal("10500000.00"),
        "source_notes": october_tenth.get("confidence") == "medium"
        and isinstance(october_notes, list)
        and any("source-bound page statement period" in str(note) for note in october_notes)
        and isinstance(october_sources, list)
        and any(cop_style[-1].name in str(source) for source in october_sources),
        "review_csv": "source-bound page statement period" in spanish_csv_text
        and cop_style[-1].name in spanish_csv_text
        and spanish_csv_rows
        and "evidence_class" in (spanish_csv_rows[0] or {})
        and any(row.get("evidence_class") == "transaction" for row in spanish_csv_rows)
        and any(row.get("evidence_class") == "carried-forward" for row in spanish_csv_rows),
    }
    flow_ok = all(flow_checks.values())
    check(
        "FLOW-1 sanitized Spanish table fixture preserves source-bound dates, balances, coverage, and reviewed institution",
        flow_ok,
        "" if flow_ok else json.dumps(flow_checks, sort_keys=True),
    )

    # The compact table has no timestamp, no per-row currency marker, and an
    # incidental numeric value before the final balance. It can use the strict
    # coordinate parser only after the reviewer has confirmed COP in the
    # preflight handoff.
    compact_style = make_compact_cop_column_fixture(work)
    compact_preflight_process, compact_preflight_path, compact_preflight_data = preflight(
        work, "compact-cop", compact_style, require_institution=True
    )
    compact_handoff_process, compact_handoff_path, _compact_handoff_data = create_reviewed_handoff(
        work, "compact-cop", compact_preflight_path, compact_preflight_data or {}, institution="Marca66"
    )
    compact_extract_process, _compact_account_path, compact_account_data = extract(
        work, "compact-cop", compact_style, compact_handoff_path
    )
    compact_rows = compact_account_data.get("daily_ledger") if isinstance(compact_account_data, dict) else []
    compact_observed = {
        str(row.get("date")): row
        for row in compact_rows
        if isinstance(row, dict) and row.get("balance_source") == "observed"
    } if isinstance(compact_rows, list) else {}
    compact_values_match = {
        day: Decimal(str(row.get("native_balance"))) for day, row in compact_observed.items()
    } == expected_observed
    compact_notes = [note for row in compact_observed.values() for note in row.get("notes", []) if isinstance(note, str)]
    compact_confidence = {row.get("confidence") for row in compact_observed.values()}
    compact_warnings = compact_account_data.get("warnings") if isinstance(compact_account_data, dict) else []
    compact_checks = {
        "reviewed_preflight": compact_preflight_process.returncode == 0 and compact_handoff_process.returncode == 0,
        "all_expected_balances": compact_values_match,
        "high_confidence": compact_confidence == {"high"},
        "final_saldo_column_note": any("compact COP Fecha Descripción Saldo table by PDF columns" in note for note in compact_notes),
        "no_omitted_rows": not any("Compact COP table" in str(warning) and "omitted" in str(warning) for warning in compact_warnings),
    }
    compact_detail = {
        **compact_checks,
        "missing_dates": sorted(set(expected_observed) - set(compact_observed)),
        "unexpected_dates": sorted(set(compact_observed) - set(expected_observed)),
    }
    check(
        "FLOW-2 compact COP columns select only the final Saldo cell after a reviewed COP handoff",
        compact_extract_process.returncode == 0 and all(compact_checks.values()),
        "" if all(compact_checks.values()) else json.dumps(compact_detail, sort_keys=True),
    )

    # A header typo must not turn on the compact parser. The existing generic
    # path can still expose medium-confidence rows for reviewer inspection.
    mismatch_style = make_compact_cop_column_fixture(work, saldo_header="SALDOS")
    mismatch_preflight_process, mismatch_preflight_path, mismatch_preflight_data = preflight(
        work, "compact-cop-mismatch", mismatch_style, require_institution=True
    )
    mismatch_handoff_process, mismatch_handoff_path, _mismatch_handoff_data = create_reviewed_handoff(
        work, "compact-cop-mismatch", mismatch_preflight_path, mismatch_preflight_data or {}, institution="Marca66"
    )
    mismatch_extract_process, _mismatch_account_path, mismatch_account_data = extract(
        work, "compact-cop-mismatch", mismatch_style, mismatch_handoff_path
    )
    mismatch_rows = mismatch_account_data.get("daily_ledger") if isinstance(mismatch_account_data, dict) else []
    mismatch_observed = [
        row for row in mismatch_rows if isinstance(row, dict) and row.get("balance_source") == "observed"
    ] if isinstance(mismatch_rows, list) else []
    mismatch_notes = [note for row in mismatch_observed for note in row.get("notes", []) if isinstance(note, str)]
    mismatch_checks = {
        "reviewed_preflight": mismatch_preflight_process.returncode == 0 and mismatch_handoff_process.returncode == 0,
        "generic_rows_remain_reviewable": len(mismatch_observed) == len(expected_observed)
        and {row.get("confidence") for row in mismatch_observed} == {"medium"},
        "compact_profile_not_used": not any("compact COP Fecha Descripción Saldo table by PDF columns" in note for note in mismatch_notes),
    }
    check(
        "GUARD-1 compact COP parsing refuses a mismatched header",
        mismatch_extract_process.returncode == 0 and all(mismatch_checks.values()),
        "" if all(mismatch_checks.values()) else json.dumps(mismatch_checks, sort_keys=True),
    )

    # Missing source-bound page periods must keep the coordinate path off.
    unbound_data = json.loads(compact_preflight_path.read_text(encoding="utf-8"))
    for item in unbound_data.get("statement_files", []):
        if isinstance(item, dict):
            item["period_intervals"] = []
    compact_preflight_path.write_text(json.dumps(unbound_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    unbound_handoff_process, unbound_handoff_path, _unbound_handoff_data = create_reviewed_handoff(
        work, "compact-cop-unbound", compact_preflight_path, unbound_data, institution="Marca66"
    )
    unbound_extract_process, _unbound_account_path, unbound_account_data = extract(
        work, "compact-cop-unbound", compact_style, unbound_handoff_path
    )
    unbound_rows = unbound_account_data.get("daily_ledger") if isinstance(unbound_account_data, dict) else []
    unbound_observed = [
        row for row in unbound_rows if isinstance(row, dict) and row.get("balance_source") == "observed"
    ] if isinstance(unbound_rows, list) else []
    unbound_notes = [note for row in unbound_observed for note in row.get("notes", []) if isinstance(note, str)]
    check(
        "GUARD-2 compact COP parsing refuses pages without a re-verified period",
        unbound_handoff_process.returncode == 0 and unbound_extract_process.returncode == 0
        and not unbound_observed
        and not any("compact COP Fecha Descripción Saldo table by PDF columns" in note for note in unbound_notes),
        unbound_extract_process.stderr.strip(),
    )

    # Mutate one synthetic source only after every applicable parser path has
    # completed. The final identity check must reject it before account JSON or
    # CSV artifacts are created. This is deterministic rather than timing a
    # second process between PDF parsing and the last fingerprint check.
    post_parse_preflight_process, post_parse_preflight_path, post_parse_preflight_data = preflight(
        work, "post-parse-mutation", compact_style, require_institution=True
    )
    post_parse_handoff_process, post_parse_handoff_path, _post_parse_handoff_data = create_reviewed_handoff(
        work,
        "post-parse-mutation",
        post_parse_preflight_path,
        post_parse_preflight_data or {},
        institution="Marca66",
    )
    post_parse_output = work / "post-parse-mutation-account.json"
    post_parse_csv = post_parse_output.with_name(post_parse_output.stem + "-review.csv")
    post_parse_target = compact_style[-1]
    mutation_events: list[str] = []

    def mutate_after_parsing() -> None:
        post_parse_target.write_bytes(post_parse_target.read_bytes() + b"\n% synthetic mutation after parsing\n")
        mutation_events.append(post_parse_target.name)

    post_parse_args = argparse.Namespace(
        pdf=[str(path) for path in compact_style],
        tax_year=2025,
        out=str(post_parse_output),
        csv=str(post_parse_csv),
        account_id=None,
        institution=None,
        account_currency=None,
        preflight_json=str(post_parse_handoff_path),
    )
    post_parse_error = ""
    post_parse_code: int | None = None
    try:
        fbar_module.command_extract_account(
            post_parse_args,
            before_final_fingerprint_check=mutate_after_parsing,
        )
    except fbar_module.FbarError as exc:
        post_parse_error = str(exc)
        post_parse_code = exc.code
    check(
        "IDENTITY-3 a PDF changed after parsing is rejected before account artifacts are written",
        post_parse_preflight_process.returncode == 0
        and post_parse_handoff_process.returncode == 0
        and mutation_events == [post_parse_target.name]
        and post_parse_code == 2
        and "changed after preflight" in post_parse_error
        and not post_parse_output.exists()
        and not post_parse_csv.exists(),
        post_parse_error,
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

    issuer_unknown = work / "issuer-unknown.pdf"
    make_pdf(issuer_unknown, [
        "Monthly Account Statement",
        "Account Number: 88889999",  # privacy-gate: allow (synthetic account fixture)
        "Statement period January 1 2025 to December 31 2025",
        "Currency USD",
        "2025-01-01 Closing balance 9,900.00 USD",
        "2025-12-31 Closing balance 9,900.00 USD",
    ])
    legacy_institution_process, _legacy_institution_path, legacy_institution_data = preflight(
        work, "legacy-institution", [issuer_unknown]
    )
    legacy_requirements = legacy_institution_data.get("requirements") if isinstance(legacy_institution_data, dict) else None
    legacy_gates = legacy_institution_data.get("review_gates") if isinstance(legacy_institution_data, dict) else []
    check(
        "INSTITUTION-1 legacy one-account preflight stays issuer-optional",
        legacy_institution_process.returncode == 0
        and isinstance(legacy_institution_data, dict)
        and legacy_institution_data.get("status") == "ready-for-domain-extraction"
        and legacy_requirements == {"institution_required": False}
        and not any(isinstance(gate, dict) and gate.get("code") == "unknown-institution" for gate in legacy_gates),
        legacy_institution_process.stderr.strip(),
    )

    required_institution_process, required_institution_path, required_institution_data = preflight(
        work, "required-institution", [issuer_unknown], require_institution=True
    )
    required_institution_gates = required_institution_data.get("review_gates") if isinstance(required_institution_data, dict) else []
    required_handoff_process, required_handoff_path, required_handoff_data = create_reviewed_handoff(
        work,
        "required-institution",
        required_institution_path,
        required_institution_data or {},
        institution="Reviewed Test Bank",
    )
    required_extract_process, _required_account_path, required_account_data = extract(
        work, "required-institution", [issuer_unknown], required_handoff_path
    )
    required_account = required_account_data.get("account") if isinstance(required_account_data, dict) else None
    required_resolutions = required_handoff_data.get("user_resolutions") if isinstance(required_handoff_data, dict) else None
    reviewed_institution = required_resolutions.get("institution") if isinstance(required_resolutions, dict) else None
    check(
        "INSTITUTION-2 opt-in one-account preflight requires and retains a typed issuer confirmation",
        required_institution_process.returncode == 0
        and isinstance(required_institution_data, dict)
        and any(isinstance(gate, dict) and gate.get("code") == "unknown-institution" for gate in required_institution_gates)
        and required_handoff_process.returncode == 0
        and isinstance(reviewed_institution, dict)
        and reviewed_institution.get("name") == "Reviewed Test Bank"
        and required_extract_process.returncode == 0
        and isinstance(required_account, dict)
        and required_account.get("institution") == "Reviewed Test Bank",
        required_extract_process.stderr.strip(),
    )

    institution_conflict_process, _institution_conflict_path, _institution_conflict_data = extract(
        work,
        "required-institution-conflict",
        [issuer_unknown],
        required_handoff_path,
        institution="Different Test Bank",
    )
    check(
        "INSTITUTION-3 a CLI issuer cannot override the reviewed institution resolution",
        institution_conflict_process.returncode == 2
        and "conflicts with the user-confirmed institution" in institution_conflict_process.stderr,
        institution_conflict_process.stderr.strip(),
    )

    mixed_institutions = [work / "issuer-alpha.pdf", work / "issuer-beta.pdf"]
    make_pdf(mixed_institutions[0], [
        "Alpha Bank N.A.",
        "Account Number: 11223344",  # privacy-gate: allow (synthetic account fixture)
        "Statement period January 1 2025 to June 30 2025",
        "Currency USD",
        "2025-01-01 Closing balance 9,900.00 USD",
        "2025-06-30 Closing balance 9,900.00 USD",
    ])
    make_pdf(mixed_institutions[1], [
        "Beta Banco S.A.",
        "Account Number: 11223344",  # privacy-gate: allow (synthetic account fixture)
        "Statement period July 1 2025 to December 31 2025",
        "Currency USD",
        "2025-07-01 Closing balance 9,900.00 USD",
        "2025-12-31 Closing balance 9,900.00 USD",
    ])
    mixed_institution_process, mixed_institution_path, mixed_institution_data = preflight(
        work, "mixed-institution", mixed_institutions, require_institution=True
    )
    mixed_handoff_process, _mixed_handoff_path, mixed_handoff_data = create_reviewed_handoff(
        work,
        "mixed-institution",
        mixed_institution_path,
        mixed_institution_data or {},
        institution="Reviewed Test Bank",
    )
    mixed_gates = mixed_institution_data.get("review_gates") if isinstance(mixed_institution_data, dict) else []
    mixed_resolutions = mixed_handoff_data.get("user_resolutions") if isinstance(mixed_handoff_data, dict) else None
    mixed_resolution = mixed_resolutions.get("institution") if isinstance(mixed_resolutions, dict) else None
    check(
        "INSTITUTION-4 mixed issuer evidence is source-gated and requires the typed reviewed selection",
        mixed_institution_process.returncode == 0
        and any(isinstance(gate, dict) and gate.get("code") == "possible-mixed-institutions" for gate in mixed_gates)
        and mixed_handoff_process.returncode == 0
        and isinstance(mixed_resolution, dict)
        and mixed_resolution.get("resolved_gate_codes") == ["possible-mixed-institutions"],
        mixed_handoff_process.stderr.strip(),
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
    mid_sufficiency = mid_account.get("data_sufficiency", {}) if isinstance(mid_account, dict) and isinstance(mid_account.get("data_sufficiency"), dict) else {}
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
        and isinstance(mid_sufficiency.get("daily_threshold"), dict) and mid_sufficiency["daily_threshold"].get("answer") == "insufficient-records"
        and isinstance(mid_sufficiency.get("maximum_account_value"), dict) and mid_sufficiency["maximum_account_value"].get("answer") == "not-determinable"
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
    end_sufficiency = end_account.get("data_sufficiency", {}) if isinstance(end_account, dict) and isinstance(end_account.get("data_sufficiency"), dict) else {}
    check(
        "COVERAGE-2 an omitted year-end statement retains trailing-gap warnings and no daily answer",
        end_process.returncode == 0 and end_handoff_process.returncode == 0 and end_extract_process.returncode == 0
        and isinstance(end_data, dict) and any(isinstance(gate, dict) and gate.get("code") == "possible-missing-statement-period" for gate in end_data.get("review_gates", []))
        and isinstance(end_coverage, dict) and int(end_coverage.get("trailing_carry_days", 0)) > 40
        and end_coverage.get("carry_gap_review_required") is True
        and isinstance(end_warnings, list) and any("final" in str(warning) and "carried forward" in str(warning) for warning in end_warnings)
        and isinstance(end_sufficiency.get("daily_threshold"), dict) and end_sufficiency["daily_threshold"].get("answer") == "insufficient-records"
        and isinstance(end_sufficiency.get("maximum_account_value"), dict) and end_sufficiency["maximum_account_value"].get("answer") == "not-determinable"
        and isinstance(end_account, dict) and end_account.get("status") == "extracted-review-required" and "daily_threshold" not in end_account,
        end_extract_process.stderr.strip(),
    )


failed = [result for result in results if not result[1]]
for name, passed, detail in results:
    print(f"{'PASS' if passed else 'FAIL'}  {name:<86} {detail}")
print(f"\n{len(results) - len(failed)}/{len(results)} passed, {len(failed)} failed")
raise SystemExit(1 if failed else 0)
