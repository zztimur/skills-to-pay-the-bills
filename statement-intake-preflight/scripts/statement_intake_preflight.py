#!/usr/bin/env python3
"""Preflight machine-readable bank statement PDFs for downstream workflows."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

try:
    import pdfplumber
except ImportError:  # pragma: no cover - exercised by users without deps.
    pdfplumber = None


SCHEMA_VERSION = "1.0"
MIN_TEXT_CHARS = 40
SUPPORTED_SCOPES = {"one-account", "one-institution"}

CURRENCY_CODES = {
    "AED",
    "ARS",
    "AUD",
    "BRL",
    "CAD",
    "CHF",
    "CLP",
    "CNY",
    "COP",
    "DKK",
    "EUR",
    "GBP",
    "HKD",
    "ILS",
    "INR",
    "JPY",
    "KRW",
    "MXN",
    "NOK",
    "NZD",
    "PEN",
    "PLN",
    "RUB",
    "SAR",
    "SEK",
    "SGD",
    "THB",
    "TRY",
    "TWD",
    "USD",
    "UYU",
    "ZAR",
}

CURRENCY_ALIASES = {
    "colombian peso": "COP",
    "colombia peso": "COP",
    "canadian dollar": "CAD",
    "mexican peso": "MXN",
    "argentine peso": "ARS",
    "chilean peso": "CLP",
    "peruvian sol": "PEN",
    "euro": "EUR",
    "british pound": "GBP",
    "pound sterling": "GBP",
    "swiss franc": "CHF",
    "japanese yen": "JPY",
    "chinese yuan": "CNY",
    "brazilian real": "BRL",
    "south african rand": "ZAR",
    "us dollar": "USD",
    "u.s. dollar": "USD",
    "united states dollar": "USD",
    "usd": "USD",
}

ACCOUNT_PATTERNS = (
    re.compile(r"\b(?:account|acct|a/c)\s*(?:number|no\.?|#|id)?\s*[:#-]?\s*([A-Z0-9*Xx.\- ]{4,32})", re.I),
    re.compile(r"\b(?:cuenta|n[uú]mero de cuenta)\s*[:#-]?\s*([A-Z0-9*Xx.\- ]{4,32})", re.I),
    re.compile(r"\bIBAN\s*[:#-]?\s*([A-Z]{2}[A-Z0-9 ]{8,34})", re.I),
    re.compile(r"\b(?:ending in|ends in|termina en)\s*([0-9*Xx]{2,8})", re.I),
)

TITLE_TERMS = (
    "statement",
    "extracto",
    "movimientos",
    "account summary",
    "resumen",
    "period",
    "periodo",
    "cuenta",
)

INSTITUTION_TERMS = (
    "bank",
    "banco",
    "banque",
    "credit union",
    "brokerage",
    "financial",
    "fiduciary",
    "trust",
    "global66",
    "wise",
    "revolut",
)

MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
ISO_CURRENCY_RE = re.compile(r"\b[A-Z]{3}\b")


class PreflightError(Exception):
    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def stable_unique(values: Iterable[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = clean_line(str(value))
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if limit is not None and len(out) >= limit:
            break
    return out


def normalize_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def add_gate(gates: list[dict[str, str]], code: str, message: str, severity: str = "review") -> None:
    if not any(gate.get("code") == code and gate.get("message") == message for gate in gates):
        gates.append({"code": code, "severity": severity, "message": message})


def split_lines(text: str) -> list[str]:
    return [clean_line(line) for line in text.splitlines() if clean_line(line)]


def load_pdf_files(paths: list[str]) -> list[dict[str, object]]:
    if pdfplumber is None:
        raise PreflightError("pdfplumber is required for preflight. Use a Python environment that has pdfplumber installed.")

    files: list[dict[str, object]] = []
    for raw_path in paths:
        path = Path(raw_path)
        warnings: list[str] = []
        pages: list[dict[str, object]] = []
        if path.suffix.lower() != ".pdf":
            warnings.append(f"{path.name} is not a PDF.")
            files.append(file_profile(path, [], warnings, is_pdf=False))
            continue
        if not path.exists():
            warnings.append(f"{path} was not found.")
            files.append(file_profile(path, [], warnings, is_pdf=True))
            continue
        try:
            with pdfplumber.open(str(path)) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    pages.append({"page": index, "text": text})
        except Exception as exc:  # pragma: no cover - depends on malformed PDF internals.
            warnings.append(f"{path.name} could not be read as a PDF: {exc}")
        files.append(file_profile(path, pages, warnings, is_pdf=True))
    return files


def file_profile(path: Path, pages: list[dict[str, object]], warnings: list[str], is_pdf: bool) -> dict[str, object]:
    text = "\n".join(str(page.get("text", "")) for page in pages)
    lines = split_lines(text)
    char_count = len(text.strip())
    if is_pdf and char_count < MIN_TEXT_CHARS:
        warnings.append(f"{path.name} has little machine-readable text; scanned/image-only PDFs are out of scope for v1.")
    return {
        "file": str(path),
        "resolved_file": normalize_path(path),
        "is_pdf": is_pdf,
        "page_count": len(pages),
        "character_count": char_count,
        "lines": lines,
        "warnings": stable_unique(warnings),
    }


def detect_years(lines: Iterable[str]) -> list[int]:
    years: set[int] = set()
    for line in lines:
        for match in YEAR_RE.finditer(line):
            years.add(int(match.group(1)))
    return sorted(years)


def detect_statement_titles(lines: Iterable[str]) -> list[str]:
    titles: list[str] = []
    for line in lines:
        low = line.casefold()
        if any(term in low for term in TITLE_TERMS):
            titles.append(line)
    return stable_unique(titles, limit=12)


def detect_periods(lines: Iterable[str]) -> list[str]:
    periods: list[str] = []
    for line in lines:
        low = line.casefold()
        has_period_term = any(term in low for term in ("period", "periodo", "from", "to", "desde", "hasta", "statement date"))
        has_month = any(month in low for month in MONTH_NAMES)
        has_year = bool(YEAR_RE.search(line))
        if (has_period_term and has_year) or (has_month and has_year):
            periods.append(line)
    return stable_unique(periods, limit=20)


def detect_account_hints(lines: Iterable[str]) -> list[str]:
    hints: list[str] = []
    for line in lines:
        for pattern in ACCOUNT_PATTERNS:
            for match in pattern.finditer(line):
                hint = clean_line(match.group(1))
                hint = re.sub(r"\s{2,}", " ", hint)
                if hint and len(hint) >= 2:
                    hints.append(hint)
    return stable_unique(hints, limit=20)


def detect_institution_hints(lines: Iterable[str]) -> list[str]:
    hints: list[str] = []
    for line in list(lines)[:80]:
        low = line.casefold()
        if any(term in low for term in INSTITUTION_TERMS):
            if not any(skip in low for skip in ("statement", "extracto", "account number", "cuenta no")):
                hints.append(line)
    return stable_unique(hints, limit=12)


def detect_currency(lines: Iterable[str]) -> dict[str, object]:
    codes: list[str] = []
    symbol_markers: set[str] = set()
    alias_evidence: list[str] = []
    joined = "\n".join(lines)
    for match in ISO_CURRENCY_RE.finditer(joined):
        token = match.group(0).upper()
        if token in CURRENCY_CODES:
            codes.append(token)
    low = joined.casefold()
    for alias, code in CURRENCY_ALIASES.items():
        if alias in low:
            codes.append(code)
            alias_evidence.append(alias)
    if "$" in joined:
        symbol_markers.add("$")
    for symbol, code in (("\\u20ac", "EUR"), ("\\u00a3", "GBP"), ("\\u00a5", "JPY")):
        if symbol.encode("utf-8").decode("unicode_escape") in joined:
            symbol_markers.add(symbol)
            codes.append(code)
    unique_codes = sorted(set(codes))
    if len(unique_codes) == 1:
        code = unique_codes[0]
    elif len(unique_codes) > 1:
        code = "MIXED"
    else:
        code = "UNKNOWN"
    return {
        "code": code,
        "candidates": unique_codes,
        "symbol_markers": sorted(symbol_markers),
        "alias_evidence": stable_unique(alias_evidence),
        "ambiguous_dollar": "$" in symbol_markers and not unique_codes,
    }


def build_preflight(files: list[dict[str, object]], tax_year: int, scope: str, out_path: Path, csv_path: Path) -> dict[str, object]:
    if scope not in SUPPORTED_SCOPES:
        raise PreflightError(f"Unsupported scope {scope!r}. Use one-account or one-institution.")

    warnings: list[str] = []
    gates: list[dict[str, str]] = []
    all_lines: list[str] = []
    statement_files: list[dict[str, object]] = []
    for item in files:
        file_warnings = [str(warning) for warning in item.get("warnings", [])]
        warnings.extend(file_warnings)
        if not item.get("is_pdf", True):
            add_gate(gates, "non-pdf-input", f"{Path(str(item.get('file'))).name} is not a PDF.", "stop")
        if any("was not found" in warning for warning in file_warnings):
            add_gate(gates, "missing-file", f"{item.get('file')} was not found.", "stop")
        if int(item.get("character_count", 0)) < MIN_TEXT_CHARS:
            add_gate(gates, "low-text-pdf", f"{Path(str(item.get('file'))).name} has little machine-readable text.", "stop")
        lines = [str(line) for line in item.get("lines", [])]
        all_lines.extend(lines)
        years = detect_years(lines)
        statement_files.append(
            {
                "file": item.get("file"),
                "resolved_file": item.get("resolved_file"),
                "is_pdf": item.get("is_pdf"),
                "page_count": item.get("page_count"),
                "character_count": item.get("character_count"),
                "detected_years": years,
                "detected_periods": detect_periods(lines),
                "statement_titles": detect_statement_titles(lines),
                "currency": detect_currency(lines),
                "account_hints": detect_account_hints(lines),
                "institution_hints": detect_institution_hints(lines),
                "warnings": file_warnings,
            }
        )

    detected_years = sorted({year for item in statement_files for year in item.get("detected_years", [])})
    outside_years = [year for year in detected_years if year != tax_year]
    if outside_years:
        message = f"Detected year(s) outside requested tax year {tax_year}: {', '.join(str(year) for year in outside_years)}."
        warnings.append(message)
        add_gate(gates, "mixed-years", message)
    if not detected_years:
        warnings.append("No statement years were detected; verify the PDFs belong to the requested tax year.")
        add_gate(gates, "unknown-year-coverage", "No statement years were detected; verify statement periods manually.")

    currency = detect_currency(all_lines)
    if currency["ambiguous_dollar"]:
        message = "Currency uses '$' but no unambiguous ISO code or currency name was found."
        warnings.append(message)
        add_gate(gates, "ambiguous-dollar", message)
    if currency["code"] == "UNKNOWN":
        message = "No account currency marker was found; downstream extraction may need an explicit account currency."
        warnings.append(message)
        add_gate(gates, "unknown-currency", message)
    if currency["code"] == "MIXED":
        message = f"Multiple currency candidates were found: {', '.join(currency['candidates'])}."
        warnings.append(message)
        add_gate(gates, "mixed-currencies", message)

    account_hints = detect_account_hints(all_lines)
    if scope == "one-account" and len(account_hints) > 1:
        message = f"Multiple account hints found; verify this is one account: {', '.join(account_hints[:8])}."
        warnings.append(message)
        add_gate(gates, "possible-mixed-accounts", message)
    if scope == "one-account" and not account_hints:
        message = "No account number/designation hint was found; verify this is one account."
        warnings.append(message)
        add_gate(gates, "unknown-account", message)

    institution_hints = detect_institution_hints(all_lines)
    normalized_institutions = {re.sub(r"[^a-z0-9]+", " ", hint.casefold()).strip() for hint in institution_hints}
    if scope == "one-institution" and len(normalized_institutions) > 1:
        message = f"Multiple institution hints found; verify this is one institution: {', '.join(institution_hints[:6])}."
        warnings.append(message)
        add_gate(gates, "possible-mixed-institutions", message)
    if scope == "one-institution" and not institution_hints:
        message = "No institution hint was found in early statement text; verify the institution manually."
        warnings.append(message)
        add_gate(gates, "unknown-institution", message)

    title_values = stable_unique((title for item in statement_files for title in item.get("statement_titles", [])), limit=20)
    period_values = stable_unique((period for item in statement_files for period in item.get("detected_periods", [])), limit=30)
    primary_institution = most_common_hint(institution_hints)
    status = "review-required" if gates else "ready-for-domain-extraction"

    return {
        "schema_version": SCHEMA_VERSION,
        "skill": "statement-intake-preflight",
        "status": status,
        "tax_year": tax_year,
        "scope": scope,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "profile": {
            "primary_institution": primary_institution,
            "statement_titles": title_values,
            "statement_file_count": len(statement_files),
            "total_page_count": sum(int(item.get("page_count") or 0) for item in statement_files),
            "total_character_count": sum(int(item.get("character_count") or 0) for item in statement_files),
        },
        "statement_files": statement_files,
        "currency": currency,
        "account_hints": account_hints,
        "institution_hints": institution_hints,
        "coverage_hints": {
            "requested_tax_year": tax_year,
            "detected_years": detected_years,
            "outside_requested_years": outside_years,
            "detected_periods": period_values,
            "low_text_files": [
                str(item.get("file"))
                for item in statement_files
                if int(item.get("character_count") or 0) < MIN_TEXT_CHARS
            ],
        },
        "warnings": stable_unique(warnings),
        "review_gates": gates,
        "artifacts": {"review_csv": str(csv_path)},
    }


def most_common_hint(hints: list[str]) -> str:
    if not hints:
        return ""
    normalized = [re.sub(r"[^a-z0-9]+", " ", hint.casefold()).strip() for hint in hints]
    counts = Counter(normalized)
    target = counts.most_common(1)[0][0]
    for hint, normalized_hint in zip(hints, normalized, strict=True):
        if normalized_hint == target:
            return hint
    return hints[0]


def review_csv_path(out_path: Path) -> Path:
    return out_path.with_name(f"{out_path.stem}-review.csv")


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_review_csv(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "file",
        "is_pdf",
        "page_count",
        "character_count",
        "detected_years",
        "detected_periods",
        "statement_titles",
        "currency_code",
        "currency_candidates",
        "account_hints",
        "institution_hints",
        "warnings",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in data.get("statement_files", []):
            if not isinstance(item, dict):
                continue
            currency = item.get("currency") if isinstance(item.get("currency"), dict) else {}
            writer.writerow(
                {
                    "file": item.get("file"),
                    "is_pdf": item.get("is_pdf"),
                    "page_count": item.get("page_count"),
                    "character_count": item.get("character_count"),
                    "detected_years": "; ".join(str(year) for year in item.get("detected_years", [])),
                    "detected_periods": "; ".join(str(period) for period in item.get("detected_periods", [])),
                    "statement_titles": "; ".join(str(title) for title in item.get("statement_titles", [])),
                    "currency_code": currency.get("code") if isinstance(currency, dict) else "",
                    "currency_candidates": "; ".join(str(code) for code in currency.get("candidates", []) if isinstance(currency, dict)),
                    "account_hints": "; ".join(str(hint) for hint in item.get("account_hints", [])),
                    "institution_hints": "; ".join(str(hint) for hint in item.get("institution_hints", [])),
                    "warnings": "; ".join(str(warning) for warning in item.get("warnings", [])),
                }
            )


def command_preflight(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    csv_path = Path(args.csv) if args.csv else review_csv_path(out_path)
    files = load_pdf_files(args.pdf)
    data = build_preflight(files, int(args.tax_year), args.scope, out_path, csv_path)
    write_json(out_path, data)
    write_review_csv(csv_path, data)
    print(f"Wrote preflight JSON: {out_path}")
    print(f"Wrote review CSV: {csv_path}")
    print(f"Status: {data['status']}")
    gates = data.get("review_gates", [])
    if gates:
        print("Review gates:")
        for gate in gates:
            if isinstance(gate, dict):
                print(f"- {gate.get('code')}: {gate.get('message')}")
    return 0


def command_dependency_check(_args: argparse.Namespace) -> int:
    if pdfplumber is None:
        print("pdfplumber missing")
        return 1
    print("pdfplumber ok")
    return 0


def synthetic_file(name: str, text: str, warnings: list[str] | None = None, is_pdf: bool = True) -> dict[str, object]:
    return file_profile(Path(name), [{"page": 1, "text": text}], warnings or [], is_pdf=is_pdf)


def command_self_test(_args: argparse.Namespace) -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clean = build_preflight(
            [
                synthetic_file(
                    "clean.pdf",
                    "Example Bank\nAccount number 1234\nStatement period January 1 2025 to January 31 2025\nCurrency USD\nClosing balance 100.00",
                )
            ],
            2025,
            "one-account",
            root / "clean.json",
            root / "clean-review.csv",
        )
        if clean["status"] != "ready-for-domain-extraction":
            failures.append(f"clean-status: expected ready, got {clean['status']}")
        if clean["currency"]["code"] != "USD":  # type: ignore[index]
            failures.append(f"clean-currency: expected USD, got {clean['currency']}")
        if clean["account_hints"] != ["1234"]:  # type: ignore[index]
            failures.append(f"clean-account: expected account hint 1234, got {clean['account_hints']}")

        mixed_year = build_preflight(
            [synthetic_file("mixed-year.pdf", "Example Bank\nAccount number 1234\nStatement period December 2024 to January 2025\nCurrency USD")],
            2025,
            "one-account",
            root / "mixed-year.json",
            root / "mixed-year-review.csv",
        )
        if not any(gate.get("code") == "mixed-years" for gate in mixed_year["review_gates"]):  # type: ignore[index]
            failures.append("mixed-year: expected mixed-years gate")

        mixed_currency = build_preflight(
            [synthetic_file("mixed-currency.pdf", "Example Bank\nAccount number 1234\nStatement period January 2025\nCurrency USD\nCurrency COP")],
            2025,
            "one-account",
            root / "mixed-currency.json",
            root / "mixed-currency-review.csv",
        )
        if not any(gate.get("code") == "mixed-currencies" for gate in mixed_currency["review_gates"]):  # type: ignore[index]
            failures.append("mixed-currency: expected mixed-currencies gate")

        dollar = build_preflight(
            [synthetic_file("dollar.pdf", "Example Bank\nAccount number 1234\nStatement period January 2025\nClosing balance $100.00")],
            2025,
            "one-account",
            root / "dollar.json",
            root / "dollar-review.csv",
        )
        if not any(gate.get("code") == "ambiguous-dollar" for gate in dollar["review_gates"]):  # type: ignore[index]
            failures.append("dollar: expected ambiguous-dollar gate")

        accounts = build_preflight(
            [synthetic_file("accounts.pdf", "Example Bank\nAccount number 1234\nAccount number 9999\nStatement period January 2025\nCurrency USD")],
            2025,
            "one-account",
            root / "accounts.json",
            root / "accounts-review.csv",
        )
        if not any(gate.get("code") == "possible-mixed-accounts" for gate in accounts["review_gates"]):  # type: ignore[index]
            failures.append("accounts: expected possible-mixed-accounts gate")

        low_text = build_preflight(
            [synthetic_file("scan.pdf", "", [], is_pdf=True)],
            2025,
            "one-account",
            root / "scan.json",
            root / "scan-review.csv",
        )
        if not any(gate.get("code") == "low-text-pdf" for gate in low_text["review_gates"]):  # type: ignore[index]
            failures.append("low-text: expected low-text-pdf gate")

        write_json(root / "clean.json", clean)
        write_review_csv(root / "clean-review.csv", clean)
        if not (root / "clean-review.csv").exists():
            failures.append("review-csv: expected review CSV to be written")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Self-test passed: 7 preflight cases")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Preflight one statement PDF set.")
    preflight.add_argument("--pdf", nargs="+", required=True, help="Statement PDFs for one tax year and one scope.")
    preflight.add_argument("--tax-year", type=int, required=True, help="Calendar/tax year to verify.")
    preflight.add_argument("--scope", choices=sorted(SUPPORTED_SCOPES), required=True, help="Expected downstream scope.")
    preflight.add_argument("--out", required=True, help="Output preflight JSON path.")
    preflight.add_argument("--csv", help="Optional review CSV path.")
    preflight.set_defaults(func=command_preflight)

    dependency = subparsers.add_parser("dependency-check", help="Check extraction dependency availability.")
    dependency.set_defaults(func=command_dependency_check)

    self_test = subparsers.add_parser("self-test", help="Run deterministic preflight tests.")
    self_test.set_defaults(func=command_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
