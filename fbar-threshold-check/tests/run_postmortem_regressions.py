#!/usr/bin/env python3
"""Synthetic regressions for the 2025 real-run parser/evidence failures."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

try:
    from reportlab.pdfgen import canvas
    import pdfplumber  # noqa: F401
except BaseException as exc:  # noqa: BLE001
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise
    print(f"SKIP: postmortem regressions need reportlab + pdfplumber ({type(exc).__name__}); not run.")
    raise SystemExit(0)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_ROOT = PACKAGE_ROOT.parent / "statement-intake-preflight"
FBAR = PACKAGE_ROOT / "scripts" / "fbar_threshold_check.py"
PREFLIGHT = PREFLIGHT_ROOT / "scripts" / "statement_intake_preflight.py"
results: list[tuple[str, bool, str]] = []


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def refreshed_binding(path: Path, existing: dict | None = None) -> dict:
    binding = dict(existing or {})
    binding.update({"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return binding


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, condition, detail))


def draw_table_pdf(
    path: Path,
    *,
    currency: str,
    headers: list[tuple[int, str]],
    rows: list[list[tuple[int, str]]],
    opening: str | None = None,
    closing: str | None = None,
) -> None:
    doc = canvas.Canvas(str(path))
    doc.drawString(40, 810, "Northwind Ledger Cooperative Statement")
    doc.drawString(40, 792, "Account Number: 24682468")  # privacy-gate: allow (synthetic account fixture)
    doc.drawString(40, 774, "Statement period January 1 2025 to December 31 2025")
    doc.drawString(40, 756, f"Currency {currency}")
    if opening is not None:
        doc.drawString(40, 738, f"Opening balance {opening}")
    header_y = 710
    for x, value in headers:
        doc.drawString(x, header_y, value)
    y = 688
    for row in rows:
        for x, value in row:
            doc.drawString(x, y, value)
        y -= 20
    if closing is not None:
        doc.drawString(40, y - 8, f"Closing balance {closing}")
    doc.save()


def preflight(work: Path, tag: str, pdfs: list[Path]) -> tuple[Path, dict, subprocess.CompletedProcess[str]]:
    out = work / f"{tag}-preflight.json"
    process = run([
        sys.executable, "-B", str(PREFLIGHT), "preflight", "--pdf", *(str(path) for path in pdfs),
        "--tax-year", "2025", "--scope", "one-account", "--require-institution", "--out", str(out),
    ])
    return out, read_json(out) if out.exists() else {}, process


def reviewed_handoff(work: Path, tag: str, source: Path, data: dict) -> Path:
    out = work / f"{tag}-reviewed.json"
    command = [sys.executable, "-B", str(PREFLIGHT), "review-handoff", "--input", str(source)]
    codes = [str(item["code"]) for item in data.get("review_gates", [])]
    for code in codes:
        command.extend(["--accept-gate", code])
    if {"unknown-account", "possible-mixed-accounts", "incomplete-account-linkage"} & set(codes):
        command.append("--confirm-one-account")
    if {"unknown-currency", "ambiguous-dollar"} & set(codes):
        command.extend(["--confirm-currency", "USD"])
    if {"unknown-institution", "possible-mixed-institutions"} & set(codes):
        command.extend(["--confirm-institution", "Northwind Ledger Cooperative"])
    command.extend(["--user-review-confirmed", "--out", str(out)])
    process = run(command)
    if process.returncode != 0:
        raise AssertionError(process.stderr)
    return out


def extract(work: Path, tag: str, pdfs: list[Path], handoff: Path) -> tuple[Path, dict, subprocess.CompletedProcess[str]]:
    out = work / f"{tag}-account.json"
    process = run([
        sys.executable, "-B", str(FBAR), "extract-account", "--pdf", *(str(path) for path in pdfs),
        "--tax-year", "2025", "--preflight-json", str(handoff), "--out", str(out),
    ])
    return out, read_json(out) if out.exists() else {}, process


def attest(
    work: Path,
    tag: str,
    pdf: Path,
    preflight_path: Path,
    preflight_data: dict,
    evidence_class: str,
    *,
    maximum: str | None = None,
    daily_csv: Path | None = None,
    currency: str = "USD",
    fx_workpaper: Path | None = None,
) -> Path:
    out = work / f"{tag}.json"
    command = [
        sys.executable, "-B", str(FBAR), "attest-account", "--pdf", str(pdf), "--tax-year", "2025",
        "--preflight-json", str(preflight_path), "--evidence-class", evidence_class,
        "--account-id", tag, "--institution", "Northwind Ledger Cooperative", "--account-currency", currency,
        "--user-attestation-confirmed", "--out", str(out),
    ]
    for gate in preflight_data.get("review_gates", []):
        command.extend(["--accept-gate", str(gate["code"])])
    if maximum is not None:
        command.extend(["--maximum-native", maximum])
    if daily_csv is not None:
        command.extend(["--daily-csv", str(daily_csv)])
    if fx_workpaper is not None:
        command.extend(["--fx-workpaper-json", str(fx_workpaper)])
    process = run(command)
    if process.returncode != 0:
        raise AssertionError(process.stderr)
    return out


def make_fx_workpaper(root: Path) -> Path:
    folder = root / "cop-fx"
    folder.mkdir()
    markdown = folder / "workpaper.md"
    pdf = folder / "workpaper.pdf"
    source = folder / "source-proof.json"
    path = folder / "workpaper.json"
    markdown.write_text("# Synthetic COP year-end proof\n", encoding="utf-8")
    pdf.write_bytes(b"%PDF-1.4\nsynthetic COP year-end proof\n")
    source.write_text('{"synthetic":"COP year-end source proof"}\n', encoding="utf-8")
    payload = {
        "skill": "get-year-end-fx-rate",
        "currency": "COP",
        "year": 2025,
        "year_end_date": "2025-12-31",
        "rate": "4000",
        "rate_direction": "foreign-per-usd",
        "foreign_per_usd": "4000",
        "usd_per_foreign": "0.00025",
        "source": {
            "title": "Synthetic year-end table",
            "url": "https://example.test/synthetic-cop-year-end",
            "retrieved": "2026-01-05",
            "year_end_confirmed": True,
        },
        "proof": {
            "workpaper_json": str(path),
            "workpaper_md": str(markdown),
            "workpaper_pdf": str(pdf),
            "workpaper_pdf_sha256": sha256_file(pdf),
            "saved_files": [{
                "filename": source.name,
                "packet_relative_path": source.name,
                "path": str(source),
                "sha256": sha256_file(source),
            }],
            "limitations": [],
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


with tempfile.TemporaryDirectory(prefix="fbar-postmortem-") as temporary:
    work = Path(temporary)

    full_date = work / "full-date.pdf"
    draw_table_pdf(
        full_date,
        currency="EUR",
        headers=[(40, "Fecha"), (150, "Descripción"), (330, "Débito"), (420, "Crédito"), (520, "Saldo")],
        rows=[
            [(40, "13/05/2025"), (150, "Factura 700001"), (330, "0.00"), (420, "25.00"), (520, "1025.00")],
            [(40, "31/12/2025"), (150, "Cierre 800002"), (330, "5.00"), (420, "0.00"), (520, "1020.00")],
        ],
    )
    full_preflight, full_preflight_data, full_preflight_process = preflight(work, "full-date", [full_date])
    full_handoff = full_preflight if full_preflight_data.get("status") == "ready-for-domain-extraction" else reviewed_handoff(work, "full-date", full_preflight, full_preflight_data)
    _full_account_path, full_account, full_extract = extract(work, "full-date", [full_date], full_handoff)
    full_observed = {
        row["date"]: Decimal(str(row["native_balance"]))
        for row in full_account.get("daily_ledger", [])
        if row.get("evidence_class") == "transaction"
    }
    check(
        "F03 full DD/MM/YYYY geometry selects only the final Saldo column",
        full_preflight_process.returncode == 0 and full_extract.returncode == 0
        and full_observed == {"2025-05-13": Decimal("1025"), "2025-12-31": Decimal("1020")},
        full_extract.stderr,
    )

    iso_results: dict[str, dict[str, Decimal]] = {}
    for currency in ("EUR", "USD"):
        path = work / f"iso-{currency.lower()}.pdf"
        draw_table_pdf(
            path,
            currency=currency,
            headers=[(40, "Date"), (170, "Description"), (335, "Debit"), (420, "Credit"), (515, "Balance")],
            rows=[
                [(40, "2025-01-01 09:00:00"), (170, "Order 700001"), (335, "0.00"), (420, "200.00"), (515, "1200.00")],
                [(40, "2025-12-31 17:00:00"), (170, "Order 800002"), (335, "75.00"), (420, "0.00"), (515, "1125.00")],
            ],
        )
        pf, pf_data, _ = preflight(work, f"iso-{currency}", [path])
        handoff = pf if pf_data.get("status") == "ready-for-domain-extraction" else reviewed_handoff(work, f"iso-{currency}", pf, pf_data)
        _account_path, account, process = extract(work, f"iso-{currency}", [path], handoff)
        iso_results[currency] = {
            row["date"]: Decimal(str(row["native_balance"]))
            for row in account.get("daily_ledger", [])
            if row.get("evidence_class") == "transaction"
        }
        check(f"F04/F05 {currency} ISO timestamp table extracts", process.returncode == 0 and len(iso_results[currency]) == 2, process.stderr)
    check("F04/F05 cross-currency structured parser parity", iso_results["EUR"] == iso_results["USD"], str(iso_results))

    ambiguous_pdf = work / "ambiguous-full-date.pdf"
    draw_table_pdf(
        ambiguous_pdf,
        currency="USD",
        headers=[(40, "Date"), (170, "Description"), (335, "Debit"), (420, "Credit"), (515, "Balance")],
        rows=[[(40, "04/05/2025"), (170, "Ambiguous date"), (335, "0.00"), (420, "10.00"), (515, "1010.00")]],
    )
    ambiguous_pf, ambiguous_pf_data, _ = preflight(work, "ambiguous", [ambiguous_pdf])
    ambiguous_handoff = ambiguous_pf if ambiguous_pf_data.get("status") == "ready-for-domain-extraction" else reviewed_handoff(work, "ambiguous", ambiguous_pf, ambiguous_pf_data)
    _ambiguous_path, ambiguous_data, ambiguous_process = extract(work, "ambiguous", [ambiguous_pdf], ambiguous_handoff)
    check(
        "H1 ambiguous full numeric dates are refused without source-bound disambiguation",
        ambiguous_process.returncode == 0 and ambiguous_data.get("status") == "parser-coverage-defect"
        and ambiguous_data.get("parser_coverage", {}).get("status") == "defect",
        ambiguous_process.stdout + ambiguous_process.stderr,
    )

    reconciled_pdf = work / "reconciled.pdf"
    draw_table_pdf(
        reconciled_pdf,
        currency="USD",
        opening="1000.00",
        closing="1125.00",
        headers=[(40, "Date"), (170, "Description"), (380, "Debit"), (500, "Credit")],
        rows=[
            [(40, "2025-01-05"), (170, "Deposit 700001"), (500, "200.00")],
            [(40, "2025-02-10"), (170, "Purchase 800002"), (380, "75.00")],
        ],
    )
    recon_pf, recon_pf_data, _ = preflight(work, "reconciled", [reconciled_pdf])
    recon_handoff = recon_pf if recon_pf_data.get("status") == "ready-for-domain-extraction" else reviewed_handoff(work, "reconciled", recon_pf, recon_pf_data)
    _recon_path, recon_account, recon_process = extract(work, "reconciled", [reconciled_pdf], recon_handoff)
    reconciliation_profiles = recon_account.get("parser_coverage", {}).get("profiles", [])
    check(
        "F02 opening plus signed movements reconciles and stays diagnostic",
        recon_process.returncode == 0
        and recon_account.get("evidence_status", {}).get("class") == "diagnostic-reconstructed"
        and any(profile.get("reconciliation", {}).get("status") == "passed" for profile in reconciliation_profiles),
        json.dumps(reconciliation_profiles),
    )

    broken_pdf = work / "reconciliation-broken.pdf"
    draw_table_pdf(
        broken_pdf,
        currency="USD",
        opening="1000.00",
        closing="1125.00",
        headers=[(40, "Date"), (170, "Description"), (380, "Debit"), (500, "Credit")],
        rows=[
            [(40, "2025-01-05"), (170, "Deposit 700001"), (500, "201.00")],
            [(40, "2025-02-10"), (170, "Purchase 800002"), (380, "75.00")],
        ],
    )
    broken_pf, broken_pf_data, _ = preflight(work, "broken", [broken_pdf])
    broken_handoff = broken_pf if broken_pf_data.get("status") == "ready-for-domain-extraction" else reviewed_handoff(work, "broken", broken_pf, broken_pf_data)
    _broken_path, broken_account, broken_process = extract(work, "broken", [broken_pdf], broken_handoff)
    check(
        "H5 reconciliation mismatch emits parser-coverage-defect",
        broken_process.returncode == 0 and broken_account.get("status") == "parser-coverage-defect"
        and broken_account.get("parser_coverage", {}).get("status") == "defect",
        broken_process.stderr,
    )

    certificate = work / "certificate.pdf"
    doc = canvas.Canvas(str(certificate))
    doc.drawString(40, 800, "Northwind Ledger Cooperative Account Certificate")
    doc.drawString(40, 780, "Account Number: 86428642")  # privacy-gate: allow (synthetic account fixture)
    doc.drawString(40, 760, "Currency USD")
    doc.drawString(40, 740, "Certificate issued August 1 2026")
    doc.drawString(40, 720, "Current balance 0.00 USD")
    doc.save()
    cert_pf, cert_pf_data, cert_process = preflight(work, "certificate", [certificate])
    daily_csv = work / "daily.csv"
    with daily_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "native_balance"], lineterminator="\n")
        writer.writeheader()
        current = date(2025, 1, 1)
        end = date(2025, 12, 31)
        while current <= end:
            writer.writerow({"date": current.isoformat(), "native_balance": "9600.00"})
            current += timedelta(days=1)
    daily = attest(work, "daily-9600", certificate, cert_pf, cert_pf_data, "user-attested-daily", daily_csv=daily_csv)
    max_only = attest(work, "max-only-500", certificate, cert_pf, cert_pf_data, "user-attested-maximum", maximum="500")
    zero = attest(work, "zero-all-year", certificate, cert_pf, cert_pf_data, "user-attested-zero")

    boundary_answers: dict[str, str] = {}
    for tag, native_value in (("below", "9999.99"), ("equal", "10000.00"), ("above", "10000.01")):
        boundary_csv = work / f"boundary-{tag}.csv"
        with boundary_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "native_balance"], lineterminator="\n")
            writer.writeheader()
            current = date(2025, 1, 1)
            end = date(2025, 12, 31)
            while current <= end:
                writer.writerow({"date": current.isoformat(), "native_balance": native_value})
                current += timedelta(days=1)
        boundary_ledger = attest(
            work, f"boundary-{tag}", certificate, cert_pf, cert_pf_data,
            "user-attested-daily", daily_csv=boundary_csv,
        )
        boundary_summary = work / f"boundary-{tag}-summary.json"
        boundary_process = run([
            sys.executable, "-B", str(FBAR), "aggregate", "--account-ledger", str(boundary_ledger),
            "--out", str(boundary_summary),
        ])
        boundary_answers[tag] = (
            str(read_json(boundary_summary).get("daily_threshold", {}).get("answer"))
            if boundary_process.returncode == 0 and boundary_summary.exists() else "error"
        )
    check(
        "F10 threshold comparison is strictly greater than $10,000",
        boundary_answers == {"below": "no", "equal": "no", "above": "yes"},
        str(boundary_answers),
    )

    summary = work / "interval-summary.json"
    aggregate = run([
        sys.executable, "-B", str(FBAR), "aggregate", "--account-ledger", str(daily), str(max_only),
        "--out", str(summary),
    ])
    summary_data = read_json(summary) if summary.exists() else {}
    check(
        "F08 undated maximum produces lower/upper bounds and sensitivity",
        cert_process.returncode == 0 and aggregate.returncode == 0
        and summary_data.get("daily_threshold", {}).get("answer") == "review-required"
        and summary_data.get("daily_threshold", {}).get("answer_lower_bound") == "no"
        and summary_data.get("daily_threshold", {}).get("answer_upper_bound") == "yes",
        aggregate.stderr,
    )
    zero_summary = work / "zero-summary.json"
    zero_aggregate = run([
        sys.executable, "-B", str(FBAR), "aggregate", "--account-ledger", str(daily), str(zero),
        "--out", str(zero_summary),
    ])
    zero_data = read_json(zero_summary) if zero_summary.exists() else {}
    check(
        "F09 user-attested zero stays non-formal and supports a bounded no",
        zero_aggregate.returncode == 0 and zero_data.get("daily_threshold", {}).get("answer") == "no"
        and read_json(zero).get("evidence_status", {}).get("formal_eligibility") is False,
        zero_aggregate.stderr,
    )
    manifest = summary.with_name(summary.stem + "-postflight.json")
    packet_files = [summary, manifest, summary.with_suffix(".csv"), summary.with_suffix(".pdf"), daily, max_only]

    def packet_copy(tag: str) -> Path:
        root = work / tag
        root.mkdir()
        for source in packet_files:
            shutil.copy2(source, root / source.name)
        return root

    def packet_verify(root: Path) -> subprocess.CompletedProcess[str]:
        return run([
            sys.executable, "-B", str(FBAR), "verify-packet",
            "--summary", str(root / summary.name), "--manifest", str(root / manifest.name),
        ])

    relocated = packet_copy("postflight-relocated")
    verify_pass = packet_verify(relocated)
    check(
        "M11A postflight verifies after packet relocation",
        verify_pass.returncode == 0 and "Packet integrity: pass" in verify_pass.stdout,
        verify_pass.stdout + verify_pass.stderr,
    )
    collision_target = relocated / summary.with_suffix(".csv").name
    collision_hash = sha256_file(collision_target)
    collision = run([
        sys.executable, "-B", str(FBAR), "verify-packet",
        "--summary", str(relocated / summary.name), "--manifest", str(relocated / manifest.name),
        "--out", str(collision_target),
    ])
    check(
        "M11A2 verification output cannot overwrite a bound packet artifact",
        collision.returncode == 2 and "collides with input" in collision.stderr
        and sha256_file(collision_target) == collision_hash,
        collision.stdout + collision.stderr,
    )

    ledger_case = packet_copy("postflight-ledger-mutation")
    ledger_target = ledger_case / max_only.name
    ledger_target.write_text(ledger_target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    ledger_fail = packet_verify(ledger_case)
    check(
        "M11B postflight detects ledger byte mutation",
        ledger_fail.returncode == 2 and "account-ledger-binding" in ledger_fail.stdout,
        ledger_fail.stdout + ledger_fail.stderr,
    )

    csv_case = packet_copy("postflight-csv-mutation")
    csv_target = csv_case / summary.with_suffix(".csv").name
    csv_target.write_text(csv_target.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    csv_fail = packet_verify(csv_case)
    check(
        "M11C postflight detects replaced CSV",
        csv_fail.returncode == 2 and "output-binding-combined_csv" in csv_fail.stdout,
        csv_fail.stdout + csv_fail.stderr,
    )

    pdf_case = packet_copy("postflight-pdf-missing")
    (pdf_case / summary.with_suffix(".pdf").name).unlink()
    pdf_fail = packet_verify(pdf_case)
    check(
        "M11D postflight detects missing PDF",
        pdf_fail.returncode == 2 and "summary-artifact-summary_pdf" in pdf_fail.stdout,
        pdf_fail.stdout + pdf_fail.stderr,
    )

    missing_binding_case = packet_copy("postflight-binding-omission")
    missing_manifest_path = missing_binding_case / manifest.name
    missing_manifest = read_json(missing_manifest_path)
    missing_manifest["account_ledger_bindings"] = missing_manifest["account_ledger_bindings"][:-1]
    missing_manifest_path.write_text(json.dumps(missing_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    missing_binding_fail = packet_verify(missing_binding_case)
    check(
        "M11E postflight rejects an omitted ledger binding",
        missing_binding_fail.returncode == 2 and "account-ledger-binding-set" in missing_binding_fail.stdout,
        missing_binding_fail.stdout + missing_binding_fail.stderr,
    )

    unsafe_case = packet_copy("postflight-unsafe-path")
    unsafe_manifest_path = unsafe_case / manifest.name
    unsafe_manifest = read_json(unsafe_manifest_path)
    unsafe_manifest["account_ledger_bindings"][0]["path"] = "../outside.json"
    unsafe_manifest_path.write_text(json.dumps(unsafe_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    unsafe_fail = packet_verify(unsafe_case)
    check(
        "M11F postflight rejects unsafe packet-relative paths",
        unsafe_fail.returncode == 2 and "account-ledger-binding" in unsafe_fail.stdout,
        unsafe_fail.stdout + unsafe_fail.stderr,
    )

    semantic_case = packet_copy("postflight-semantic-rebind")
    semantic_ledger_path = semantic_case / daily.name
    semantic_ledger = read_json(semantic_ledger_path)
    for row in semantic_ledger.get("daily_ledger", []):
        row["native_balance"] = "9700"
        row["usd_balance"] = "9700.00"
        row["threshold_usd_value"] = "9700.000000"
    semantic_ledger_path.write_text(json.dumps(semantic_ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    semantic_summary_path = semantic_case / summary.name
    semantic_summary = read_json(semantic_summary_path)
    refreshed_ledger = None
    for section in (
        semantic_summary.get("accounts", []),
        semantic_summary.get("fincen_max_value_view", {}).get("account_maxima", []),
    ):
        for item in section:
            if item.get("account_id") == "daily-9600":
                item["ledger_binding"] = refreshed_binding(semantic_ledger_path, item.get("ledger_binding"))
                refreshed_ledger = item["ledger_binding"]
    semantic_summary_path.write_text(json.dumps(semantic_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    semantic_manifest_path = semantic_case / manifest.name
    semantic_manifest = read_json(semantic_manifest_path)
    for index, item in enumerate(semantic_manifest.get("account_ledger_bindings", [])):
        if item.get("account_id") == "daily-9600":
            semantic_manifest["account_ledger_bindings"][index] = refreshed_ledger
    semantic_manifest["summary_binding"] = refreshed_binding(
        semantic_summary_path, semantic_manifest.get("summary_binding")
    )
    semantic_manifest_path.write_text(json.dumps(semantic_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    semantic_fail = packet_verify(semantic_case)
    check(
        "M11G postflight recomputes semantics after coordinated rebinding",
        semantic_fail.returncode == 2 and "semantic-recompute" in semantic_fail.stdout,
        semantic_fail.stdout + semantic_fail.stderr,
    )

    fx_workpaper = make_fx_workpaper(work)
    cop_max = attest(
        work,
        "cop-max-only",
        certificate,
        cert_pf,
        cert_pf_data,
        "user-attested-maximum",
        maximum="500000",
        currency="COP",
        fx_workpaper=fx_workpaper,
    )
    fx_summary = work / "fx-summary.json"
    fx_aggregate = run([
        sys.executable, "-B", str(FBAR), "aggregate", "--account-ledger", str(cop_max),
        "--out", str(fx_summary), "--packet-root", str(work),
    ])
    fx_manifest = work / "fx-summary-postflight.json"
    fx_case = work / "postflight-fx-proof-mutation"
    fx_case.mkdir()
    for source in (fx_summary, fx_manifest, fx_summary.with_suffix(".csv"), fx_summary.with_suffix(".pdf"), cop_max):
        shutil.copy2(source, fx_case / source.name)
    shutil.copytree(fx_workpaper.parent, fx_case / fx_workpaper.parent.name)
    fx_verify_pass = run([
        sys.executable, "-B", str(FBAR), "verify-packet",
        "--summary", str(fx_case / fx_summary.name), "--manifest", str(fx_case / fx_manifest.name),
    ])
    fx_pdf = fx_case / fx_workpaper.parent.name / "workpaper.pdf"
    fx_pdf.write_bytes(fx_pdf.read_bytes() + b"tampered")
    fx_verify_fail = run([
        sys.executable, "-B", str(FBAR), "verify-packet",
        "--summary", str(fx_case / fx_summary.name), "--manifest", str(fx_case / fx_manifest.name),
    ])
    check(
        "M11H postflight verifies nested FX proof and detects PDF mutation",
        fx_aggregate.returncode == 0 and fx_verify_pass.returncode == 0
        and fx_verify_fail.returncode == 2 and "semantic-recompute" in fx_verify_fail.stdout,
        fx_aggregate.stderr + fx_verify_pass.stdout + fx_verify_pass.stderr + fx_verify_fail.stdout + fx_verify_fail.stderr,
    )


for name, passed, detail in results:
    print(f"{'PASS' if passed else 'FAIL'}  {name} {detail if not passed else ''}")
failed = sum(not passed for _name, passed, _detail in results)
print(f"\n{len(results) - failed}/{len(results)} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
