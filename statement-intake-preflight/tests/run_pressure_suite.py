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


def make_pdf(name: str, lines: list[str]) -> Path:
    path = WORK / name
    doc = canvas.Canvas(str(path))
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


def gates_of(data) -> list[str]:
    return [g["code"] for g in data["review_gates"]] if data else []


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

p = make_pdf("inst-bancolombia.pdf", ["Bancolombia S.A.", "Extracto de cuenta",
             "Cuenta Nro. 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Periodo: 1 de enero 2025 al 31 de enero 2025", "Moneda: COP", "Saldo final 1.234.567,89 COP"])
proc, d = run("inst-bancolombia", [str(p)], scope="one-institution")
check("INST-7 one-token brand 'Bancolombia' is recognized as an institution",
      d and d["institution_hints"] and "unknown-institution" not in gates_of(d),
      f"hints={d['institution_hints'] if d else '?'}")

a = make_pdf("inst-acc.pdf", ["Banco Bogota Ejemplo", "Cuenta Nro. 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Periodo: 1 de enero 2025 al 31 de enero 2025", "Moneda: COP"])
b = make_pdf("inst-acc-b.pdf", ["Banco Bogotá Ejemplo", "Cuenta Nro. 55556666",  # privacy-gate: allow (synthetic account fixture)
             "Periodo: 1 de febrero 2025 al 28 de febrero 2025", "Moneda: COP"])
proc, d = run("inst-accent", [str(a), str(b)], scope="one-institution")
check("INST-8 accent drift ('Bogota' vs 'Bogotá') does not split one bank",
      d and "possible-mixed-institutions" not in gates_of(d), f"gates={gates_of(d)}")

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
neutralized = "'=HYPERLINK" in csv_text
check("CLI-8 a formula-leading CSV cell is neutralized with a leading quote",
      neutralized, f"neutralized={neutralized}")

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
