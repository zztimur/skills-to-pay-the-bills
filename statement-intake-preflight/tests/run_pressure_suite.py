#!/usr/bin/env python3
"""Adversarial pressure suite for statement-intake-preflight (real-PDF, end-to-end).

This is the committed graduation of the audit red-team corpus. Every failure
mode a past skill-forge audit round surfaced is reproduced here as a real PDF
driven through the actual CLI, so a future edit that regresses one is caught
before it ships. It complements the in-script `self-test` (which covers the same
findings on a fast, dependency-free *synthetic* path): this suite additionally
exercises PDF text extraction, argument parsing, exit codes, CSV output, and
filesystem edges that the synthetic path cannot reach.

Requires reportlab + pdfplumber -- test-only dependencies, exactly like
`smoke-test`. When either is unavailable the suite SKIPS cleanly (exit 0) so a
dependency-free CI runner is never failed; run it locally before shipping a
change:

    python3 statement-intake-preflight/tests/run_pressure_suite.py

Exit code: 0 if every scenario passes or the suite skips; 1 if any scenario
fails. Fixtures are synthetic; account-like numbers are marked for privacy-gate.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:  # test-only deps; skip cleanly (and survive a broken native ABI) if absent.
    from reportlab.pdfgen import canvas
    import pdfplumber  # noqa: F401
except BaseException as exc:  # noqa: BLE001 - a broken cryptography ABI panics, not just ImportError.
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise
    print(f"SKIP: pressure suite needs reportlab + pdfplumber ({type(exc).__name__}); not run.")
    raise SystemExit(0)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE_ROOT / "scripts" / "statement_intake_preflight.py"
WORK = Path(tempfile.mkdtemp(prefix="preflight-pressure-"))
results: list[tuple[str, bool, str]] = []


def make_pdf(name: str, lines: list[str], *, draw_logo: bool = False) -> Path:
    path = WORK / name
    doc = canvas.Canvas(str(path))
    if draw_logo:
        doc.circle(68, 770, 18, fill=1)
    y = 800
    for line in lines:
        if y < 60:
            doc.showPage()
            y = 800
        doc.drawString(40, y, line)
        y -= 14
    doc.save()
    return path


def run(tag: str, pdfs: list[str], year: str = "2025", scope: str = "one-account", extra=None):
    out = WORK / f"{tag}.json"
    cmd = [sys.executable, str(SCRIPT), "preflight", "--pdf", *pdfs,
           "--tax-year", year, "--scope", scope, "--out", str(out)]
    if extra:
        cmd += extra
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = None
    if out.exists():
        try:
            data = json.loads(out.read_text())
        except json.JSONDecodeError:
            pass
    return proc, data


def run_handoff(tag: str, source: Path, gate_codes: list[str], extra=None):
    out = WORK / f"{tag}.json"
    cmd = [sys.executable, str(SCRIPT), "review-handoff", "--input", str(source), "--out", str(out)]
    for code in gate_codes:
        cmd += ["--accept-gate", code]
    cmd.append("--user-review-confirmed")
    if extra:
        cmd += extra
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = None
    if out.exists():
        try:
            data = json.loads(out.read_text())
        except json.JSONDecodeError:
            pass
    return proc, data


def gates_of(data) -> list[str]:
    return [g["code"] for g in data["review_gates"]] if data else []


def coverage_review_of(data) -> dict:
    coverage = data.get("coverage_hints", {}) if isinstance(data, dict) else {}
    return coverage.get("period_coverage_review", {}) if isinstance(coverage, dict) else {}


def calendar_gaps_of(data) -> list:
    gaps = coverage_review_of(data).get("calendar_gaps", [])
    return gaps if isinstance(gaps, list) else []


def gate_message_of(data, code: str) -> str:
    if not isinstance(data, dict):
        return ""
    for gate in data.get("review_gates", []):
        if isinstance(gate, dict) and gate.get("code") == code:
            return str(gate.get("message", ""))
    return ""


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


# --------------------------------------------------------------------------- #
# Happy path and end-to-end basics
# --------------------------------------------------------------------------- #

p = make_pdf("happy.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Currency USD",
    "Closing balance 1,234.56 USD",
])
proc, d = run("happy", [str(p)])
check("HAPPY-1 clean statement is ready with no gates",
      d and d["status"] == "ready-for-domain-extraction" and not gates_of(d),
      f"status={d['status'] if d else None} gates={gates_of(d)}")
fingerprint = d["statement_files"][0] if d and d.get("statement_files") else {}
check("HAPPY-1A real PDF has byte-size and SHA-256 fingerprints",
      isinstance(fingerprint.get("content_bytes"), int) and fingerprint["content_bytes"] == p.stat().st_size
      and isinstance(fingerprint.get("content_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint["content_sha256"]) is not None,
      f"bytes={fingerprint.get('content_bytes')} sha256={fingerprint.get('content_sha256')}")

p = make_pdf("es.pdf", [
    "Banco Ejemplo",
    "Extracto de cuenta",
    "Cuenta Nro. 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Periodo: 1 de enero 2025 al 31 de enero 2025",
    "Moneda: COP",
    "Saldo final 1.234.567,89 COP",
])
proc, d = run("es", [str(p)])
check("HAPPY-2 full-Spanish statement -> ready, COP, account found",
      d and d["currency"]["code"] == "COP" and d["account_hints"] == ["55556666"]
      and d["status"] == "ready-for-domain-extraction",
      f"currency={d['currency']['code'] if d else '?'} hints={d['account_hints'] if d else '?'}")

# --------------------------------------------------------------------------- #
# Currency corroboration -- codes confirm only with an adjacent amount or label
# --------------------------------------------------------------------------- #

p = make_pdf("cur-try.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Currency USD",
    "Closing balance 100.00 USD",
    "PLEASE TRY OUR NEW MOBILE APP TODAY",
])
proc, d = run("cur-try", [str(p)])
check("CUR-1 all-caps prose 'TRY' is not confirmed as a currency",
      d and d["currency"]["code"] == "USD" and "mixed-currencies" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("cur-clock.pdf", [
    "Example Bank", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Currency USD", "Closing balance 100.00 USD",
    "Call TRY 24/7 online banking anytime",
])
proc, d = run("cur-clock", [str(p)])
check("CUR-2 'TRY 24/7' clock fragment does not confirm a currency",
      d and d["currency"]["code"] == "USD", f"currency={d['currency'] if d else '?'}")

p = make_pdf("cur-euro.pdf", [
    "Example Bank", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Closing balance €1.234,56",
])
proc, d = run("cur-euro", [str(p)])
check("CUR-3 euro symbol resolves to EUR", d and d["currency"]["code"] == "EUR",
      f"currency={d['currency'] if d else '?'}")

p = make_pdf("cur-glued.pdf", [
    "Example Bank", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Closing balance USD1,234.56",
])
proc, d = run("cur-glued", [str(p)])
check("CUR-4 glued 'USD1,234.56' still identifies USD", d and d["currency"]["code"] == "USD",
      f"currency={d['currency'] if d else '?'}")

p = make_pdf("cur-usd.pdf", [
    "Banco Ejemplo", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Saldo final US$ 1,234.56",
])
proc, d = run("cur-usd", [str(p)])
check("CUR-5 'US$' resolves to USD, not ambiguous-dollar",
      d and d["currency"]["code"] == "USD" and "ambiguous-dollar" not in gates_of(d),
      f"currency={d['currency']['code'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("cur-brl.pdf", [
    "Banco Exemplo", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Saldo final R$ 1.234,56",
])
proc, d = run("cur-brl", [str(p)])
check("CUR-6 'R$' resolves to BRL, not ambiguous-dollar",
      d and d["currency"]["code"] == "BRL" and "ambiguous-dollar" not in gates_of(d),
      f"currency={d['currency']['code'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("cur-chf.pdf", [
    "Beispiel Bank AG", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Saldo CHF 1'234.56",
])
proc, d = run("cur-chf", [str(p)])
check("CUR-7 Swiss apostrophe \"CHF 1'234.56\" confirms CHF", d and d["currency"]["code"] == "CHF",
      f"currency={d['currency'] if d else '?'}")

p = make_pdf("cur-mxn.pdf", [
    "Banco Ejemplo", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Saldo final MXN$ 1,234.56",
])
proc, d = run("cur-mxn", [str(p)])
check("CUR-8 'MXN$' confirms MXN, no ambiguous-dollar",
      d and d["currency"]["code"] == "MXN" and "ambiguous-dollar" not in gates_of(d),
      f"currency={d['currency']['code'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("cur-usdt.pdf", [
    "Example Bank", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Currency USD", "Closing balance 1,234.56 USD",
    "Crypto desk reference USDT 999,999.99 not part of this account",
])
proc, d = run("cur-usdt", [str(p)])
check("CUR-9 'USDT 999,999.99' does not confirm a second currency",
      d and d["currency"]["code"] == "USD" and "mixed-currencies" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("cur-disclosure.pdf", [
    "Example Bank", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Currency USD", "Closing balance 1,234.56 USD",
    "Foreign currency conversion fees apply to transactions in COP and MXN",
])
proc, d = run("cur-disclosure", [str(p)])
check("CUR-10 distant disclosure boilerplate does not confirm COP/MXN",
      d and d["currency"]["code"] == "USD" and "mixed-currencies" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("cur-serial.pdf", [
    "Example Bank", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Currency USD", "Closing balance 1,234.56 USD",
    "Reference S/ 0099887 processed on 01/15",
])
proc, d = run("cur-serial", [str(p)])
check("CUR-11 'S/ 0099887' serial does not falsely confirm PEN",
      d and d["currency"]["code"] == "USD" and "mixed-currencies" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Account detection -- identifiers only, aliases collapse, counterparty skipped
# --------------------------------------------------------------------------- #

p = make_pdf("acc-clean.pdf", [
    "Example Bank", "Monthly Account Statement", "Account Summary",
    "Account holder JUAN PEREZ GARCIA",
    "Account ID 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD",
])
proc, d = run("acc-clean", [str(p)])
check("ACC-1 only the real number is a hint (no titles/holder names)",
      d and d["account_hints"] == ["12345678"] and "possible-mixed-accounts" not in gates_of(d),
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("acc-masked.pdf", [
    "Example Bank Monthly Statement",
    "Account No. ****1234",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 100.00 USD",
])
proc, d = run("acc-masked", [str(p)])
check("ACC-2 masked account header -> one clean hint",  # privacy-gate: allow (test description)
      d and d["account_hints"] == ["****1234"] and d["status"] == "ready-for-domain-extraction",
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("acc-street.pdf", [
    "Example Bank", "123 Main Street Suite 400",  # privacy-gate: allow (synthetic address)
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Currency USD",
])
proc, d = run("acc-street", [str(p)])
check("ACC-3 a street number is not captured as an account",
      d and d["account_hints"] == ["12345678"] and "possible-mixed-accounts" not in gates_of(d),
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("acc-plural.pdf", [
    "Example Bank Monthly Statement",
    "Account Nos. 11112222 and 33334444",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD",
])
proc, d = run("acc-plural", [str(p)])
check("ACC-4 'Account Nos. X and Y' surfaces both or gates mixed",
      d and (len(d["account_hints"]) == 2 or "possible-mixed-accounts" in gates_of(d)),
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("acc-spaced.pdf", [
    "Example Bank Monthly Statement",
    "Account No. 5555 6666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD",
    "Questions about account 55556666 call us anytime",  # privacy-gate: allow (synthetic account fixture)
])
proc, d = run("acc-spaced", [str(p)])
check("ACC-5 spaced header + compact footer = one account, no mixed gate",
      d and "possible-mixed-accounts" not in gates_of(d),
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("acc-mask-end.pdf", [
    "Example Bank Monthly Statement",
    "Account No. ****6666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD",
    "Your account ending in 6666 earned 0.05% APY",  # privacy-gate: allow (synthetic account fixture)
])
proc, d = run("acc-mask-end", [str(p)])
check("ACC-6 masked header + 'ending in' footer = one account, no mixed gate",
      d and "possible-mixed-accounts" not in gates_of(d),
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("acc-sepa.pdf", [
    "Beispiel Bank AG",
    "Account No. 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency EUR",
    "01/05/2025 SEPA transfer to IBAN DE89 3704 0044 0532 0130 00 rent 850.00 EUR",  # privacy-gate: allow (synthetic IBAN)
    "01/12/2025 SEPA transfer to IBAN GB82 WEST 1234 5698 7654 32 invoice 120.00 EUR",  # privacy-gate: allow (synthetic IBAN)
])
proc, d = run("acc-sepa", [str(p)])
check("ACC-7 counterparty IBANs on SEPA lines don't flag mixed accounts",
      d and "possible-mixed-accounts" not in gates_of(d),
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("acc-iban.pdf", [
    "Beispiel Bank AG",
    "Account No: DE89 3704 0044 0532 0130 00",  # privacy-gate: allow (synthetic IBAN)
    "IBAN: DE89370400440532013000",  # privacy-gate: allow (synthetic IBAN)
    "Statement period January 1 2025 to January 31 2025", "Currency EUR", "Closing balance 1,234.56 EUR",
])
proc, d = run("acc-iban", [str(p)])
check("ACC-8 same IBAN under a label and the IBAN keyword = one account",
      d and "possible-mixed-accounts" not in gates_of(d),
      f"hints={d['account_hints'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Institution normalization -- same bank stays one, distinct banks gate
# --------------------------------------------------------------------------- #

long_alpha = ["Alpha Bank N.A.", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
              "Statement period January 1 2025 to January 31 2025", "Currency USD"]
long_alpha += [f"01/{(i % 28) + 1:02d}/2025 card purchase ref {i:04d} 10.00 balance 90.00" for i in range(1, 120)]
a = make_pdf("inst-alpha.pdf", long_alpha)
b = make_pdf("inst-beta.pdf", ["Beta Banco S.A.", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
             "Statement period February 1 2025 to February 28 2025", "Currency USD"])
proc, d = run("inst-mixed", [str(a), str(b)], scope="one-institution")
check("INST-1 two banks (first >80 lines) trip possible-mixed-institutions",
      d and "possible-mixed-institutions" in gates_of(d), f"gates={gates_of(d)}")

a = make_pdf("inst-jan.pdf", ["Example Bank Monthly Statement January 2025", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
             "Statement period January 1 2025 to January 31 2025", "Currency USD"])
b = make_pdf("inst-feb.pdf", ["Example Bank Monthly Statement February 2025", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
             "Statement period February 1 2025 to February 28 2025", "Currency USD"])
proc, d = run("inst-same", [str(a), str(b)], scope="one-institution")
check("INST-2 same bank across months does not trip mixed-institutions",
      d and "possible-mixed-institutions" not in gates_of(d), f"gates={gates_of(d)}")

a = make_pdf("inst-us.pdf", ["U.S. Bank", "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 100.00 USD"])
b = make_pdf("inst-mt.pdf", ["M&T Bank", "Statement period February 1 2025 to February 28 2025", "Currency USD", "Closing balance 200.00 USD"])
proc, d = run("inst-short", [str(a), str(b)], scope="one-institution")
check("INST-3 'U.S. Bank' vs 'M&T Bank' trip mixed-institutions (no initial collision)",
      d and "possible-mixed-institutions" in gates_of(d), f"gates={gates_of(d)}")

a = make_pdf("inst-cv.pdf", ["Banco Ejemplo S.A. de C.V.", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Statement period January 1 2025 to January 31 2025", "Currency MXN", "Saldo final 1,234.56 MXN"])
b = make_pdf("inst-bare.pdf", ["Banco Ejemplo", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Statement period February 1 2025 to February 28 2025", "Currency MXN", "Saldo final 2,345.67 MXN"])
proc, d = run("inst-latam", [str(a), str(b)], scope="one-institution")
check("INST-4 'S.A. de C.V.' vs bare name = one institution",
      d and "possible-mixed-institutions" not in gates_of(d), f"gates={gates_of(d)}")

a = make_pdf("inst-addr.pdf", [
    "JOHN Q CUSTOMER", "12 Bank Street", "Springfield",  # privacy-gate: allow (synthetic address)
    "Example Bank N.A.", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD"])
b = make_pdf("inst-addr-b.pdf", ["Example Bank N.A.", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Statement period February 1 2025 to February 28 2025", "Currency USD"])
proc, d = run("inst-addr", [str(a), str(b)], scope="one-institution")
check("INST-5 a street address above the masthead is not an institution",  # privacy-gate: allow (test description)
      d and "possible-mixed-institutions" not in gates_of(d), f"gates={gates_of(d)}")

p = make_pdf("inst-wise.pdf", ["Wise Account Statement", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
             "Statement period January 1 2025 to January 31 2025", "Currency EUR", "Closing balance 100.00 EUR"])
proc, d = run("inst-wise", [str(p)], scope="one-institution")
check("INST-6 'Wise Account Statement' header yields an institution hint",
      d and d["institution_hints"] and "unknown-institution" not in gates_of(d),
      f"hints={d['institution_hints'] if d else '?'}")

p = make_pdf("inst-brand66.pdf", ["Marca66 S.A.", "Extracto de cuenta",
             "Cuenta Nro. 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Periodo: 1 de enero 2025 al 31 de enero 2025", "Moneda: COP", "Saldo final 1.234.567,89 COP"])
proc, d = run("inst-brand66", [str(p)], scope="one-institution")
check("INST-7 one-token alphanumeric brand 'Marca66' is recognized as an institution",
      d and d["institution_hints"] and "unknown-institution" not in gates_of(d),
      f"hints={d['institution_hints'] if d else '?'}")

for token in ("Page1", "Report2025", "Jan2025", "Q1FY2025"):
    p = make_pdf(f"inst-{token}.pdf", [token, "Monthly Statement", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
                                         "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 100.00 USD"])
    proc, d = run(f"inst-{token}", [str(p)], scope="one-institution")
    check(f"INST-7A boilerplate alphanumeric header {token!r} requires issuer review",
          d and d["institution_hints"] == [] and "unknown-institution" in gates_of(d),
          f"hints={d['institution_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("TrustedBank-logo.pdf", ["Monthly Statement", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
                                        "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 100.00 USD"],
             draw_logo=True)
proc, d = run("inst-logo-filename", [str(p)], scope="one-institution")
check("INST-7B graphic logo and filename alone require issuer review",
      d and d["institution_hints"] == [] and "unknown-institution" in gates_of(d),
      f"hints={d['institution_hints'] if d else '?'} gates={gates_of(d)}")

p = make_pdf("inst-page1-required.pdf", ["Page1", "Monthly Statement", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
                                           "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 100.00 USD"])
proc, d = run("inst-page1-required", [str(p)], scope="one-account", extra=["--require-institution"])
check("INST-7C boilerplate alphanumeric header cannot satisfy FBAR issuer requirement",
      d and d["institution_hints"] == [] and "unknown-institution" in gates_of(d),
      f"hints={d['institution_hints'] if d else '?'} gates={gates_of(d)}")

a = make_pdf("inst-acc.pdf", ["Banco Bogota Ejemplo", "Cuenta Nro. 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Periodo: 1 de enero 2025 al 31 de enero 2025", "Moneda: COP"])
b = make_pdf("inst-acc-b.pdf", ["Banco Bogotá Ejemplo", "Cuenta Nro. 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Periodo: 1 de febrero 2025 al 28 de febrero 2025", "Moneda: COP"])
proc, d = run("inst-accent", [str(a), str(b)], scope="one-institution")
check("INST-8 accent drift ('Bogota' vs 'Bogotá') does not split one bank",
      d and "possible-mixed-institutions" not in gates_of(d), f"gates={gates_of(d)}")

p = make_pdf("inst-banking-corp.pdf", ["First Banking Corporation", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
             "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 100.00 USD"])
proc, d = run("inst-banking-corp", [str(p)], scope="one-institution")
check("INST-9 'Banking Corporation' entity name is recognized as an institution",
      d and d["institution_hints"] and "unknown-institution" not in gates_of(d),
      f"hints={d['institution_hints'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Year detection -- boilerplate years excluded, real out-of-year gates
# --------------------------------------------------------------------------- #

p = make_pdf("yr-copyright.pdf", [
    "Example Bank Monthly Statement", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 1,234.56 USD",
    "(c) 2019 Example Bancorp. All rights reserved. Member FDIC.",
])
proc, d = run("yr-copyright", [str(p)])
check("YR-1 copyright footer year does not trip mixed-years",
      d and "mixed-years" not in gates_of(d) and d["status"] == "ready-for-domain-extraction",
      f"gates={gates_of(d)} years={d['coverage_hints']['detected_years'] if d else '?'}")

p = make_pdf("yr-since.pdf", [
    "Example Bank - Serving customers since 1904", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 1,234.56 USD",
])
proc, d = run("yr-since", [str(p)])
check("YR-2 'since 1904' heritage year does not trip mixed-years",
      d and "mixed-years" not in gates_of(d), f"gates={gates_of(d)} years={d['coverage_hints']['detected_years'] if d else '?'}")

p = make_pdf("yr-mixed.pdf", [
    "Example Bank", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period December 1 2024 to January 31 2025", "Currency USD",
])
proc, d = run("yr-mixed", [str(p)])
check("YR-3 a genuine out-of-year period still trips mixed-years",
      d and "mixed-years" in gates_of(d), f"gates={gates_of(d)} years={d['coverage_hints']['detected_years'] if d else '?'}")

p = make_pdf("yr-since-date.pdf", [
    "Example Bank Monthly Statement", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Account activity since 01.01.2025 through 31.01.2025", "Currency USD", "Closing balance 1,234.56 USD",
])
proc, d = run("yr-since-date", [str(p)])
check("YR-4 temporal 'since 01.01.2025' keeps the year (not heritage-suppressed)",
      d and 2025 in d["coverage_hints"]["detected_years"] and "unknown-year-coverage" not in gates_of(d),
      f"years={d['coverage_hints']['detected_years'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Period detection -- numeric ranges with no month name
# --------------------------------------------------------------------------- #

p = make_pdf("per-numeric.pdf", [
    "Beispiel Bank AG Kontoauszug", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Zeitraum 01.01.2025 - 31.01.2025", "Currency EUR", "Endsaldo 1.234,56 EUR",
])
proc, d = run("per-numeric", [str(p)])
check("PER-1 numeric period '01.01.2025 - 31.01.2025' is detected",
      d and len(d["coverage_hints"]["detected_periods"]) > 0,
      f"periods={d['coverage_hints']['detected_periods'] if d else '?'}")

p = make_pdf("per-narrative-noise.pdf", [
    "Example Bank Monthly Statement", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD",
    "Movimiento 654321 publicado en Enero 2025 importe COP 10,000",
    "Payment reference 765432 issued in March 2025 amount USD 125.00",
])
proc, d = run("per-narrative-noise", [str(p)])
detected_periods = d["coverage_hints"].get("detected_periods", []) if d else []
check("PER-1A transaction month/year narratives stay out of detected_periods",
      detected_periods == ["Statement period January 1 2025 to January 31 2025"],
      f"periods={detected_periods}")

# --------------------------------------------------------------------------- #
# Source-aware period evidence and statement-set coverage
# --------------------------------------------------------------------------- #

def quarterly_period_pdf(name: str, start: str, end: str, opening: str = "") -> Path:
    lines = [
        "Example Bank Quarterly Statement",
        "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
        f"Statement period {start} to {end}",
        "Currency USD",
    ]
    if opening:
        lines.append(opening)
    return make_pdf(name, lines)


q1 = quarterly_period_pdf(
    "period-q1.pdf", "January 1 2025", "March 31 2025", "Opening balance as of 31/12/2024"
)
q2 = quarterly_period_pdf("period-q2.pdf", "April 1 2025", "June 30 2025")
q3 = quarterly_period_pdf("period-q3.pdf", "July 1 2025", "September 30 2025")
q4 = quarterly_period_pdf("period-q4.pdf", "October 1 2025", "December 31 2025")
proc, d = run("period-contextual", [str(q1), str(q2), str(q3), str(q4)])
contextual_evidence = d["coverage_hints"].get("contextual_date_evidence", []) if d else []
check("PER-2 a prior-year opening balance stays contextual, not mixed-year coverage",
      d and "mixed-years" not in gates_of(d) and d["coverage_hints"].get("statement_period_years") == [2025]
      and any(isinstance(item, dict) and item.get("date") == "2024-12-31"
              and item.get("classification") == "opening-or-prior-balance"
              and isinstance(item.get("source_ref"), dict) and item["source_ref"].get("page") == 1
              for item in contextual_evidence),
      f"gates={gates_of(d)} coverage={d['coverage_hints'] if d else '?'}")

boundary_q1 = make_pdf("period-boundary-q1.pdf", [
    "Banco Ejemplo S.A.",
    "Cuenta 55556666",  # privacy-gate: allow (synthetic account fixture)
    "DESDE: 2024/12/31 HASTA: 2025/03/31",
    "Moneda COP",
])
boundary_q2 = make_pdf("period-boundary-q2.pdf", [
    "Banco Ejemplo S.A.",
    "Cuenta 55556666",  # privacy-gate: allow (synthetic account fixture)
    "DESDE: 2025/03/31 HASTA: 2025/06/30",
    "Moneda COP",
])
boundary_q3 = make_pdf("period-boundary-q3.pdf", [
    "Banco Ejemplo S.A.",
    "Cuenta 55556666",  # privacy-gate: allow (synthetic account fixture)
    "DESDE: 2025/06/30 HASTA: 2025/09/30",
    "Moneda COP",
])
boundary_q4 = make_pdf("period-boundary-q4.pdf", [
    "Banco Ejemplo S.A.",
    "Cuenta 55556666",  # privacy-gate: allow (synthetic account fixture)
    "DESDE: 2025/09/30 HASTA: 2025/12/31",
    "Moneda COP",
])
proc, d = run("period-year-boundary", [str(boundary_q1), str(boundary_q2), str(boundary_q3), str(boundary_q4)])
boundary_evidence = d["coverage_hints"].get("contextual_date_evidence", []) if d else []
check("PER-2A a prior Dec 31 statement boundary is contextual, not mixed-year coverage",
      d and "mixed-years" not in gates_of(d) and d["coverage_hints"].get("statement_period_years") == [2025]
      and any(isinstance(item, dict) and item.get("date") == "2024-12-31"
              and item.get("classification") == "tax-year-boundary-opening"
              and isinstance(item.get("source_ref"), dict) and item["source_ref"].get("page") == 1
              for item in boundary_evidence),
      f"gates={gates_of(d)} coverage={d['coverage_hints'] if d else '?'}")

cross_year = quarterly_period_pdf("period-cross-year.pdf", "December 1 2024", "January 31 2025")
proc, d = run("period-cross-year", [str(cross_year)])
check("PER-3 an actual 2024-2025 statement period still trips mixed-years",
      d and "mixed-years" in gates_of(d), f"gates={gates_of(d)}")

expected_missing_q2 = [{"start": "2025-04-01", "end": "2025-06-30"}]
proc, d = run("period-missing-q2", [str(q1), str(q3), str(q4)])
check("PER-4 omitted Q2 reports the exact April-through-June coverage gap",
      d and d["status"] == "review-required"
      and calendar_gaps_of(d) == expected_missing_q2
      and coverage_review_of(d).get("intervals_detected") == 3
      and "possible-missing-statement-period" in gates_of(d)
      and "2025-04-01 through 2025-06-30" in gate_message_of(d, "possible-missing-statement-period"),
      f"gaps={calendar_gaps_of(d)} gates={gates_of(d)}")

expected_missing_q4 = [{"start": "2025-10-01", "end": "2025-12-31"}]
proc, d = run("period-missing-q4", [str(q1), str(q2), str(q3)])
check("PER-5 omitted Q4 reports the exact October-through-December coverage gap",
      d and d["status"] == "review-required"
      and calendar_gaps_of(d) == expected_missing_q4
      and coverage_review_of(d).get("intervals_detected") == 3
      and "possible-missing-statement-period" in gates_of(d)
      and "2025-10-01 through 2025-12-31" in gate_message_of(d, "possible-missing-statement-period"),
      f"gaps={calendar_gaps_of(d)} gates={gates_of(d)}")

# COP statements use Spanish abbreviated months and hyphenated dates.
# Keep the fixture synthetic while exercising the real PDF text-extraction path.
def spanish_abbrev_quarterly_pdf(name: str, start: str, end: str) -> Path:
    return make_pdf(name, [
        "Banco Ejemplo S.A.",
        "Cuenta 55556666",  # privacy-gate: allow (synthetic account fixture)
        f"DESDE: {start}",
        f"HASTA: {end}",
        "Moneda COP",
    ])

spanish_q1 = spanish_abbrev_quarterly_pdf("spanish-period-q1.pdf", "01-Ene-2025", "31-Mar-2025")
spanish_q2 = spanish_abbrev_quarterly_pdf("spanish-period-q2.pdf", "01-Abr-2025", "30-Jun-2025")
spanish_q3 = spanish_abbrev_quarterly_pdf("spanish-period-q3.pdf", "01-Jul-2025", "30-Sep-2025")
spanish_q4 = spanish_abbrev_quarterly_pdf("spanish-period-q4.pdf", "01-Oct-2025", "31-Dic-2025")
proc, d = run("spanish-period-complete", [str(spanish_q1), str(spanish_q2), str(spanish_q3), str(spanish_q4)])
check("PER-6 Spanish abbreviated quarterly dates produce 2025 period coverage",
      d and d["coverage_hints"].get("statement_period_years") == [2025]
      and not d["coverage_hints"].get("period_coverage_review", {}).get("calendar_gaps"),
      f"coverage={d['coverage_hints'] if d else '?'}")

proc, d = run("spanish-period-missing-q2", [str(spanish_q1), str(spanish_q3), str(spanish_q4)])
check("PER-7 Spanish abbreviated omitted Q2 retains the exact coverage gap",
      d and calendar_gaps_of(d) == expected_missing_q2
      and "possible-missing-statement-period" in gates_of(d),
      f"gaps={calendar_gaps_of(d)} gates={gates_of(d)}")

proc, d = run("spanish-period-missing-q4", [str(spanish_q1), str(spanish_q2), str(spanish_q3)])
check("PER-8 Spanish abbreviated omitted Q4 retains the exact coverage gap",
      d and calendar_gaps_of(d) == expected_missing_q4
      and "possible-missing-statement-period" in gates_of(d),
      f"gaps={calendar_gaps_of(d)} gates={gates_of(d)}")

institution_noise = make_pdf("institution-noise.pdf", [
    "Marca66 S.A.",
    "Extracto de cuenta",
    "DESDE: 2025/01/01 HASTA: 2025/01/31",
    "Moneda COP",
    "defensor@marca66.example; www.marca66.example",
    "PAGO PSE BANCO TERCERO S.A. -150,000.00 1,500,707.44",
])
proc, d = run("institution-noise", [str(institution_noise)], scope="one-institution")
check("INST-10 footer and counterparty banks do not pollute issuer hints",
      d and d["institution_hints"] == ["Marca66 S.A."]
      and "possible-mixed-institutions" not in gates_of(d),
      f"hints={d['institution_hints'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Reviewed handoff resolutions -- user input stays separate from raw evidence
# --------------------------------------------------------------------------- #

resolution_pdf = make_pdf("resolution-review.pdf", [
    "Example Bank Statement",
    "Statement period January 1 2025 to March 31 2025",
    "Historic reference 31/12/2024",
    "Closing balance $100.00",
])
proc, d = run("resolution-review-preflight", [str(resolution_pdf)])
resolution_source = WORK / "resolution-review-preflight.json"
resolution_gates = gates_of(d)
proc, handoff = run_handoff(
    "resolution-review-handoff",
    resolution_source,
    resolution_gates,
    [
        "--confirm-statement-year", "2025",
        "--classify-contextual-year", "2024",
        "--confirm-currency", "COP",
        "--confirm-one-account",
    ],
)
resolutions = handoff.get("user_resolutions", {}) if handoff else {}
check("HANDOFF-1 structured year, currency, and one-account confirmations produce a reviewed handoff",
      proc.returncode == 0 and handoff and handoff.get("status") == "reviewed-for-domain-extraction"
      and isinstance(resolutions, dict)
      and resolutions.get("source_preflight_sha256")
      and isinstance(resolutions.get("statement_years"), dict)
      and resolutions["statement_years"].get("confirmed_years") == [2025]
      and isinstance(resolutions.get("currency"), dict) and resolutions["currency"].get("code") == "COP"
      and isinstance(resolutions.get("one_account"), dict) and resolutions["one_account"].get("account_identifier_provided") is False,
      f"exit={proc.returncode} stderr={proc.stderr.strip()} resolutions={resolutions}")

proc, handoff = run_handoff(
    "resolution-missing-currency",
    resolution_source,
    resolution_gates,
    ["--confirm-statement-year", "2025", "--classify-contextual-year", "2024", "--confirm-one-account"],
)
check("HANDOFF-2 unresolved currency cannot be accepted without an ISO confirmation",
      proc.returncode != 0 and handoff is None, f"exit={proc.returncode} stderr={proc.stderr.strip()}")

proc, handoff = run_handoff(
    "resolution-wrong-year",
    resolution_source,
    resolution_gates,
    [
        "--confirm-statement-year", "2024",
        "--classify-contextual-year", "2024",
        "--confirm-currency", "COP",
        "--confirm-one-account",
    ],
)
check("HANDOFF-3 a contradictory statement-year confirmation is rejected",
      proc.returncode != 0 and handoff is None, f"exit={proc.returncode} stderr={proc.stderr.strip()}")

# --------------------------------------------------------------------------- #
# Duplicate detection -- same path, symlink, relative alias, and byte-identical
# --------------------------------------------------------------------------- #

src = make_pdf("dup-src.pdf", [
    "Example Bank Monthly Statement", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025", "Currency USD", "Closing balance 1,234.56 USD",
])
proc, d = run("dup-same", [str(src), str(src)])
check("DUP-1 the same path twice trips duplicate-input", d and "duplicate-input" in gates_of(d), f"gates={gates_of(d)}")

copy = WORK / "dup-src-copy.pdf"
shutil.copyfile(src, copy)
proc, d = run("dup-copy", [str(src), str(copy)])
check("DUP-2 a byte-identical copy under a new name trips duplicate-content",
      d and "duplicate-content" in gates_of(d), f"gates={gates_of(d)}")

link = WORK / "dup-link.pdf"
if link.exists() or link.is_symlink():
    link.unlink()
try:
    link.symlink_to(src)
    proc, d = run("dup-link", [str(src), str(link)])
    check("DUP-3 a symlink alias of the same PDF trips duplicate-input",
          d and "duplicate-input" in gates_of(d), f"gates={gates_of(d)}")
except OSError as exc:  # pragma: no cover - platform without symlink support.
    check("DUP-3 a symlink alias of the same PDF trips duplicate-input", True, f"skipped: {exc}")

proc, d = run("dup-rel", [str(src), str(WORK / ".." / WORK.name / src.name)])
check("DUP-4 'dir/../dir/file.pdf' alias trips duplicate-input",
      d and "duplicate-input" in gates_of(d), f"gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Structural stops -- non-PDF, missing, unreadable, low-text
# --------------------------------------------------------------------------- #

txt = WORK / "note.txt"
txt.write_text("not a pdf")
proc, d = run("stop-nonpdf", [str(txt), str(WORK / "ghost.pdf")])
check("STOP-1 non-PDF and missing file raise stop gates",
      d and "non-pdf-input" in gates_of(d) and "missing-file" in gates_of(d), f"gates={gates_of(d)}")

broken = WORK / "broken.pdf"
broken.write_text("%PDF-1.4 not really a pdf")
proc, d = run("stop-unreadable", [str(broken)])
check("STOP-2 an unreadable .pdf raises unreadable-pdf (not a crash)",
      d and "unreadable-pdf" in gates_of(d) and proc.returncode == 0, f"gates={gates_of(d)} rc={proc.returncode}")

# --------------------------------------------------------------------------- #
# CLI and filesystem edges -- exit codes, arg validation, output-path safety
# --------------------------------------------------------------------------- #

proc, d = run("cli-stop-rc", [str(txt)])
check("CLI-1 exit code stays 0 on stop gates (agent must read the JSON)",
      proc.returncode == 0, f"rc={proc.returncode}")

proc, d = run("cli-review-rc", [str(src), str(src)], extra=["--exit-nonzero-on-review"])
check("CLI-2 --exit-nonzero-on-review returns 3 on a review gate",
      proc.returncode == 3, f"rc={proc.returncode}")

proc, d = run("cli-badyear", [str(src)], year="twenty")
check("CLI-3 a non-integer --tax-year fails cleanly (exit 2, no traceback)",
      proc.returncode == 2 and "Traceback" not in proc.stderr, f"rc={proc.returncode}")

proc, d = run("cli-bigyear", [str(src)], year="20025")
check("CLI-4 an out-of-range --tax-year is rejected (exit 2, no traceback)",
      proc.returncode == 2 and "Traceback" not in proc.stderr, f"rc={proc.returncode}")

out = WORK / "collide.json"
proc = subprocess.run(
    [sys.executable, str(SCRIPT), "preflight", "--pdf", str(src), "--tax-year", "2025",
     "--scope", "one-account", "--out", str(out), "--csv", str(out)],
    capture_output=True, text=True)
check("CLI-5 --csv == --out is refused (no silent clobber)",
      proc.returncode == 2 and "Traceback" not in proc.stderr, f"rc={proc.returncode}")

blocker = WORK / "blocker-file"
blocker.write_text("i am a file, not a directory")
proc = subprocess.run(
    [sys.executable, str(SCRIPT), "preflight", "--pdf", str(src), "--tax-year", "2025",
     "--scope", "one-account", "--out", str(blocker / "out.json")],
    capture_output=True, text=True)
check("CLI-6 --out beneath a file path fails cleanly (no traceback)",
      proc.returncode != 0 and "Traceback" not in proc.stderr, f"rc={proc.returncode}")

dep = subprocess.run([sys.executable, str(SCRIPT), "dependency-check"], capture_output=True, text=True)
check("CLI-7 dependency-check degrades cleanly (clear status, no traceback)",
      dep.returncode in (0, 1) and "Traceback (most recent call last)" not in dep.stderr,
      f"rc={dep.returncode} out={dep.stdout.strip()[:60]!r}")

# CSV formula-injection neutralization, verified on the real review CSV.
p = make_pdf("=cmd.pdf", [
    "=HYPERLINK(\"http://evil\") Bank", "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 2025", "Currency USD",
])
out = WORK / "csvcheck.json"
csv_out = WORK / "csvcheck.csv"
subprocess.run([sys.executable, str(SCRIPT), "preflight", "--pdf", str(p), "--tax-year", "2025",
                "--scope", "one-account", "--out", str(out), "--csv", str(csv_out)], capture_output=True, text=True)
csv_text = csv_out.read_text() if csv_out.exists() else ""
safe_formula_handling = "'=HYPERLINK" in csv_text or "=HYPERLINK" not in csv_text
check("CLI-8 formula-leading statement text is neutralized or excluded from the review CSV",
      safe_formula_handling, f"safe_formula_handling={safe_formula_handling}")

# --------------------------------------------------------------------------- #
# Scale -- a multi-page statement completes quickly with detectors intact
# --------------------------------------------------------------------------- #

big_lines = ["Example Bank Monthly Statement", "Account 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Statement period January 1 2025 to January 31 2025", "Currency USD"]
big = WORK / "scale.pdf"
doc = canvas.Canvas(str(big))
for i, line in enumerate(big_lines):
    doc.drawString(40, 800 - 14 * i, line)
for page in range(60):
    doc.showPage()
    for row in range(40):
        doc.drawString(40, 800 - 18 * row, f"01/{(row % 28) + 1:02d}/2025 card purchase ref {page:03d}{row:03d} 10.00 balance 90.00")
doc.save()
t0 = time.monotonic()
proc, d = run("scale", [str(big)])
elapsed = time.monotonic() - t0
check("SCALE-1 a 61-page statement completes <60s with detectors intact",
      d and elapsed < 60 and d["account_hints"] == ["55556666"] and d["currency"]["code"] == "USD",
      f"elapsed={elapsed:.1f}s pages={d['profile']['total_page_count'] if d else '?'}")

# --------------------------------------------------------------------------- #
# Round-7 currency corroboration (F1/F2/F3): the wrong-currency flip and its fuel
# --------------------------------------------------------------------------- #

# CURR-1 (the High): a Swiss-format CHF statement -- trailing-minus debits (the
# authentic Swiss convention), CHF never glued to an amount, and one routine
# card-FX disclosure that mentions USD. USD must NOT be handed off as the
# confirmed currency with no gate. Before the fix the bare "usd" alias flipped
# code to USD ungated; now USD earns no confirmation and the set fails safe.
p = make_pdf("swiss.pdf", [
    "Beispielbank Zurich Kontoauszug",
    "Konto 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period 01.01.2025 - 31.01.2025",
    "Alle Betraege in CHF",
    "Belastung 1'234.56-",
    "Gutschrift 987.65-",
    "Hinweis: Kartentransaktionen in USD werden umgerechnet",
])
proc, d = run("swiss", [str(p)])
flipped_ungated = bool(d) and d["currency"]["code"] == "USD" and not any("currenc" in g for g in gates_of(d))
check("CURR-1 Swiss CHF page + one USD disclosure word never hands off USD ungated",
      d is not None and not flipped_ungated,
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

# CURR-2 (no cry-wolf): a clean EUR statement (euro sign) whose only USD mention
# is a quoted exchange rate. It must stay single-currency EUR with no
# mixed-currencies gate -- the fix must not trade the flip for a false alarm on
# every European statement that prints an FX table.
p = make_pdf("eur-rate.pdf", [
    "Example Bank Europe Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Closing balance EUR 1.234,56",
    "Exchange rate USD/EUR 1.0845 applied to card transactions",
])
proc, d = run("eur-rate", [str(p)])
check("CURR-2 EUR statement quoting a USD/EUR rate stays single-currency EUR (no mixed gate)",
      d and d["currency"]["code"] == "EUR" and "mixed-currencies" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

# CURR-3 (F2 negatives): a statement whose figures are all accounting-negative
# parens must still confirm its currency from code-adjacency, not degrade to
# unknown-currency.
p = make_pdf("neg-paren.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Service charge (12.34) EUR",
    "Overdraft interest (1.234,56) EUR",
])
proc, d = run("neg-paren", [str(p)])
check("CURR-3 accounting-negative '(1.234,56) EUR' figures confirm EUR (no unknown-currency)",
      d and d["currency"]["code"] == "EUR" and "unknown-currency" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

# CURR-4 (F3 footnotes): every currency mention carries a superscript footnote
# marker. The marker is stripped before NFKC, so the code still confirms instead
# of folding to "USD1" and vanishing.
p = make_pdf("footnote.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Currency: USD¹",
    "Closing balance 1,234.56 USD¹",
])
proc, d = run("footnote", [str(p)])
check("CURR-4 footnoted 'USD¹' mentions confirm USD (no unknown-currency)",
      d and d["currency"]["code"] == "USD" and "unknown-currency" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

# CURR-5 (F1b label path): a statement that names its currency only with the
# German label "Währung" and never glues CHF to an amount. The label alone must
# confirm the code (amount-adjacency cannot, by construction).
p = make_pdf("de-label.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Währung CHF",
    "Saldo 1'234.56",
    "Belastung 500.00-",
])
proc, d = run("de-label", [str(p)])
check("CURR-5 German label 'Währung CHF' confirms CHF with no amount-adjacency",
      d and d["currency"]["code"] == "CHF",
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Round-7 coverage (F4/F5/F6): temporal years, -bank brands, German/French labels
# --------------------------------------------------------------------------- #

# COV-1 (F4): a statement whose only year-bearing line phrases the period as
# "since <ISO date>" must still yield year coverage, not a false
# unknown-year-coverage stop from over-suppressing the year next to "since".
p = make_pdf("since-iso.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Portfolio value since 2025-04-01",
    "Currency USD",
    "Closing balance 1,234.56 USD",
])
proc, d = run("since-iso", [str(p)])
check("COV-1 sole 'since 2025-04-01' line yields year coverage (no false unknown-year)",
      d and 2025 in d["coverage_hints"]["detected_years"] and "unknown-year-coverage" not in gates_of(d),
      f"years={d['coverage_hints']['detected_years'] if d else '?'} gates={gates_of(d)}")

# COV-2 (F4 guard): a real out-of-year period still trips mixed-years even when
# the same year also appears in a copyright footer -- suppression is per-token,
# so the period-line year survives and the gate fires.
p = make_pdf("mixed-year-footer.pdf", [
    "Example Bank",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period March 1 2024 to March 31 2024",
    "Currency USD",
    "(c) 2024 Example Bancorp. All rights reserved.",
])
proc, d = run("mixed-year-footer", [str(p)])
check("COV-2 a real 2024 period still trips mixed-years despite a 2024 footer",
      d and "mixed-years" in gates_of(d),
      f"years={d['coverage_hints']['detected_years'] if d else '?'} gates={gates_of(d)}")

# COV-3 (F5+F6+F1b): a German Kontoauszug resolves institution, account, and
# currency end-to-end, with no unknown-institution / unknown-account gate.
p = make_pdf("kontoauszug.pdf", [
    "Commerzbank Kontoauszug",
    "Konto 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Währung EUR",
    "Saldo 1.234,56",
    "Belastung 500.00-",
])
proc, d = run("kontoauszug", [str(p)], scope="one-institution")
gates = gates_of(d)
check("COV-3 German Kontoauszug resolves institution+account+currency end-to-end",
      d and d["account_hints"] == ["12345678"] and d["institution_hints"]
      and d["currency"]["code"] == "EUR"
      and "unknown-institution" not in gates and "unknown-account" not in gates,
      f"acct={d['account_hints'] if d else '?'} inst={d['institution_hints'] if d else '?'} "
      f"cur={d['currency']['code'] if d else '?'} gates={gates}")

# COV-4 (F5): a Dutch -bank brand ("Rabobank") is recognized as the institution.
p = make_pdf("rabobank.pdf", [
    "Rabobank Account Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Currency EUR",
    "Closing balance 1.234,56 EUR",
])
proc, d = run("rabobank", [str(p)], scope="one-institution")
check("COV-4 Dutch '-bank' brand (Rabobank) is recognized as the institution",
      d and d["institution_hints"] and "unknown-institution" not in gates_of(d),
      f"inst={d['institution_hints'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Round-8 fixes: copyright/heritage year leaks, devise/valuta labels, -bank nouns
# --------------------------------------------------------------------------- #

# R8-1 (finding B): a clean 2025 statement carrying a "© May 2019" copyright
# footer must NOT trip a false mixed-years gate -- the hard-copyright year is
# suppressed even with an intervening month.
p = make_pdf("copyright-month.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Currency USD",
    "Closing balance 1,234.56 USD",
    "© May 2019 Example Bank. All rights reserved.",
])
proc, d = run("copyright-month", [str(p)])
check("R8-1 a '© May 2019' footer does not trip mixed-years on a 2025 statement",
      d and "mixed-years" not in gates_of(d) and d["coverage_hints"]["detected_years"] == [2025],
      f"years={d['coverage_hints']['detected_years'] if d else '?'} gates={gates_of(d)}")

# R8-2 (finding A): an advisory-prose line using the English verb "devise" near a
# currency code must NOT flip an otherwise-USD statement to mixed-currencies.
p = make_pdf("devise-verb.pdf", [
    "Example Bank Wealth Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Currency USD",
    "Closing balance 12,345.67 USD",
    "Your advisor can devise a PLN allocation on request",
])
proc, d = run("devise-verb", [str(p)])
check("R8-2 an advisory 'devise a PLN' line does not trip false mixed-currencies",
      d and d["currency"]["code"] == "USD" and "mixed-currencies" not in gates_of(d),
      f"currency={d['currency'] if d else '?'} gates={gates_of(d)}")

# R8-3 (finding C): a '-bank' common noun in the header region ("Foodbank") with
# no banking context is not mined as the institution; the real header bank wins.
p = make_pdf("foodbank-noise.pdf", [
    "Commerzbank Kontoauszug",
    "Konto 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Währung EUR",
    "Saldo 1.234,56",
    "01.01.2025 Local Foodbank charity payment 25.00",
])
proc, d = run("foodbank-noise", [str(p)], scope="one-institution")
insts = [h.casefold() for h in (d["institution_hints"] if d else [])]
check("R8-3 a 'Foodbank' prose line does not pollute institution hints",
      d and not any("foodbank" in h for h in insts)
      and "possible-mixed-institutions" not in gates_of(d),
      f"inst={d['institution_hints'] if d else '?'} gates={gates_of(d)}")

# R9-1 (round-9 Z4): a comma-separated multi-year copyright footer is suppressed
# in full (transitively), so it does not trip a false mixed-years on a clean 2025
# statement -- the trailing year no longer leaks past the marker window.
p = make_pdf("copyright-list.pdf", [
    "Example Bank Monthly Statement",
    "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
    "Statement period January 1 2025 to January 31 2025",
    "Currency USD",
    "Closing balance 1,234.56 USD",
    "© 2019, 2020, 2021 Example Corporation. All rights reserved.",
])
proc, d = run("copyright-list", [str(p)])
check("R9-1 a '© 2019, 2020, 2021' copyright list does not trip mixed-years",
      d and "mixed-years" not in gates_of(d) and d["coverage_hints"]["detected_years"] == [2025],
      f"years={d['coverage_hints']['detected_years'] if d else '?'} gates={gates_of(d)}")

# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

shutil.rmtree(WORK, ignore_errors=True)
width = max(len(name) for name, _, _ in results)
failed = 0
for name, ok, detail in results:
    verdict = "PASS" if ok else "FAIL"
    print(f"{verdict}  {name:<{width}}  {'' if ok else detail}")
    if not ok:
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed, {failed} failed")
sys.exit(1 if failed else 0)
