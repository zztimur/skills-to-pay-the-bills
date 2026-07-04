#!/usr/bin/env python3
"""Build FBAR daily ledgers and aggregate threshold decisions."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP, getcontext
from pathlib import Path
from typing import Iterable

getcontext().prec = 28

SCHEMA_VERSION = "1.0"
THRESHOLD_USD = Decimal("10000")
MIN_TEXT_CHARS = 40

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
}

SYMBOL_TO_CODE = {
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}

AMBIGUOUS_CURRENCY_TERMS = {
    "peso",
    "dollar",
    "pound",
    "franc",
    "krone",
    "krona",
    "ruble",
}

BALANCE_TERMS = (
    "balance",
    "closing balance",
    "ending balance",
    "available balance",
    "current balance",
    "ledger balance",
    "running balance",
    "new balance",
    "saldo",
    "saldo final",
    "saldo disponible",
    "saldo actual",
    "saldo contable",
    "saldo anterior",
    "saldo inicial",
    "solde",
)

LOW_VALUE_TERMS = (
    "fee",
    "charge",
    "interest",
    "tax",
    "withholding",
    "payment due",
    "minimum payment",
)

ACCOUNT_PATTERNS = (
    re.compile(r"\b(?:account|acct|a/c)\s*(?:number|no\.?|#|id)?\s*[:#-]?\s*([A-Z0-9*Xx.\- ]{4,32})", re.I),
    re.compile(r"\b(?:cuenta|n[uú]mero de cuenta)\s*[:#-]?\s*([A-Z0-9*Xx.\- ]{4,32})", re.I),
    re.compile(r"\bIBAN\s*[:#-]?\s*([A-Z]{2}[A-Z0-9 ]{8,34})", re.I),
    re.compile(r"\b(?:ending in|ends in|termina en)\s*([0-9*Xx]{2,8})", re.I),
)

DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b"),
)

MONTHS = {
    "jan": 1,
    "january": 1,
    "ene": 1,
    "enero": 1,
    "feb": 2,
    "february": 2,
    "febrero": 2,
    "mar": 3,
    "march": 3,
    "marzo": 3,
    "apr": 4,
    "april": 4,
    "abr": 4,
    "abril": 4,
    "may": 5,
    "mayo": 5,
    "jun": 6,
    "june": 6,
    "junio": 6,
    "jul": 7,
    "july": 7,
    "julio": 7,
    "aug": 8,
    "august": 8,
    "ago": 8,
    "agosto": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "septiembre": 9,
    "oct": 10,
    "october": 10,
    "octubre": 10,
    "nov": 11,
    "november": 11,
    "noviembre": 11,
    "dec": 12,
    "december": 12,
    "dic": 12,
    "diciembre": 12,
}

MONTH_RE = "|".join(re.escape(k) for k in sorted(MONTHS, key=len, reverse=True))
MONTH_DATE_PATTERNS = (
    re.compile(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", re.I),
    re.compile(rf"\b(\d{{1,2}})\s+({MONTH_RE})\.?,?\s+(20\d{{2}})\b", re.I),
)

MONEY_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(\(?\s*-?\s*(?:[$€£¥]\s*)?\d[\d\s,.'’]*\d(?:[,.]\d{1,6})?\s*\)?)"
    r"(?![A-Za-z0-9])"
)


class FbarError(Exception):
    """User-correctable workflow error."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceRef:
    file: str
    page: int
    line: int
    text: str


@dataclass(frozen=True)
class BalanceCandidate:
    balance_date: date
    amount: Decimal
    currency: str
    confidence: str
    source: SourceRef
    notes: tuple[str, ...] = ()


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_currency(raw: str | None) -> str:
    if not raw:
        return "UNKNOWN"
    value = clean_text(raw).strip()
    upper = value.upper()
    if len(upper) == 3 and upper.isalpha():
        return upper
    lower = value.lower()
    if lower in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[lower]
    if lower in AMBIGUOUS_CURRENCY_TERMS:
        return "UNKNOWN"
    return upper if upper in CURRENCY_CODES else "UNKNOWN"


def decimal_from_text(raw: str) -> Decimal:
    value = clean_text(raw)
    negative = False
    if value.startswith("(") and value.endswith(")"):
        negative = True
    if "-" in value[:4]:
        negative = True
    value = re.sub(r"[A-Za-z$€£¥()\s'’]", "", value)
    value = value.replace("+", "").replace("-", "")

    if not value:
        raise InvalidOperation("empty decimal")

    comma = value.rfind(",")
    dot = value.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal_sep = "," if comma > dot else "."
        thousands_sep = "." if decimal_sep == "," else ","
        value = value.replace(thousands_sep, "")
        value = value.replace(decimal_sep, ".")
    elif "," in value:
        parts = value.split(",")
        if len(parts[-1]) in {1, 2, 3} and len(parts) == 2:
            value = parts[0].replace(".", "") + "." + parts[1]
        else:
            value = "".join(parts)
    elif "." in value:
        parts = value.split(".")
        if len(parts) == 2 and len(parts[-1]) in {1, 2, 3}:
            value = parts[0].replace(",", "") + "." + parts[1]
        else:
            value = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) <= 2 else "".join(parts)

    parsed = Decimal(value)
    return -parsed if negative else parsed


def fmt_decimal(value: Decimal | None, places: str = "0.01") -> str | None:
    if value is None:
        return None
    quantized = value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def calendar_dates(year: int) -> list[date]:
    current = date(year, 1, 1)
    end = date(year, 12, 31)
    days: list[date] = []
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def iso_day(value: date) -> str:
    return value.isoformat()


def parse_line_dates(line: str) -> list[tuple[date, str, str]]:
    dates: list[tuple[date, str, str]] = []
    for match in DATE_PATTERNS[0].finditer(line):
        year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        try:
            dates.append((date(year, month, day), "high", "ISO/date-first numeric date"))
        except ValueError:
            continue

    for match in DATE_PATTERNS[1].finditer(line):
        first, second, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        confidence = "high"
        note = "numeric date"
        if first > 12 and second <= 12:
            day, month = first, second
            note = "interpreted as DD/MM/YYYY because first component is greater than 12"
        elif second > 12 and first <= 12:
            month, day = first, second
            note = "interpreted as MM/DD/YYYY because second component is greater than 12"
        else:
            month, day = first, second
            confidence = "low"
            note = "ambiguous numeric date interpreted as MM/DD/YYYY"
        try:
            dates.append((date(year, month, day), confidence, note))
        except ValueError:
            continue

    for match in MONTH_DATE_PATTERNS[0].finditer(line):
        month = MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        year = int(match.group(3))
        try:
            dates.append((date(year, month, day), "high", "month-name date"))
        except ValueError:
            continue

    for match in MONTH_DATE_PATTERNS[1].finditer(line):
        day = int(match.group(1))
        month = MONTHS[match.group(2).lower()]
        year = int(match.group(3))
        try:
            dates.append((date(year, month, day), "high", "month-name date"))
        except ValueError:
            continue

    unique: dict[str, tuple[date, str, str]] = {}
    for item in dates:
        unique[item[0].isoformat()] = item
    return list(unique.values())


def parse_money_values(line: str) -> list[tuple[Decimal, str]]:
    values: list[tuple[Decimal, str]] = []
    for match in MONEY_RE.finditer(line):
        token = clean_text(match.group(1))
        if re.fullmatch(r"20\d{2}", token):
            continue
        if len(re.sub(r"\D", "", token)) > 16:
            continue
        try:
            values.append((decimal_from_text(token), token))
        except (InvalidOperation, ValueError):
            continue
    return values


def infer_currency(text: str, override: str | None, warnings: list[str]) -> str:
    if override:
        code = normalize_currency(override)
        if code == "UNKNOWN":
            warnings.append(f"Could not normalize supplied account currency '{override}'.")
        return code

    upper_text = text.upper()
    found_codes = sorted({code for code in CURRENCY_CODES if re.search(rf"\b{code}\b", upper_text)})

    lowered = text.lower()
    for alias, code in CURRENCY_ALIASES.items():
        if alias in lowered and code not in found_codes:
            found_codes.append(code)

    found_symbols = sorted({SYMBOL_TO_CODE[symbol] for symbol in SYMBOL_TO_CODE if symbol in text})
    if "$" in text and not found_codes:
        warnings.append("Currency uses '$' but no unambiguous ISO code was found; supply --account-currency.")
        return "UNKNOWN"

    all_found = sorted(set(found_codes + found_symbols))
    non_usd_or_all = [code for code in all_found if code != "USD"] or all_found
    if len(non_usd_or_all) == 1:
        return non_usd_or_all[0]
    if len(non_usd_or_all) > 1:
        warnings.append(f"Multiple currency markers found: {', '.join(non_usd_or_all)}.")
        return "MIXED"

    warnings.append("No account currency marker found; supply --account-currency.")
    return "UNKNOWN"


def extract_account_hints(text: str) -> list[str]:
    hints: list[str] = []
    for pattern in ACCOUNT_PATTERNS:
        for match in pattern.finditer(text):
            hint = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
            if len(re.sub(r"[^A-Za-z0-9]", "", hint)) >= 3:
                hints.append(hint[:48])
    return sorted(set(hints))


def load_pdf_lines(pdf_paths: list[str]) -> tuple[list[tuple[SourceRef, str]], list[dict[str, object]], str, list[str]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-specific.
        raise FbarError("pdfplumber is required for extract-account. Install/use a runtime with pdfplumber.", 2) from exc

    all_lines: list[tuple[SourceRef, str]] = []
    file_profiles: list[dict[str, object]] = []
    full_text_parts: list[str] = []
    warnings: list[str] = []

    for raw_path in pdf_paths:
        path = Path(raw_path)
        if path.suffix.lower() != ".pdf":
            warnings.append(f"{path.name} is not a PDF.")
        if not path.exists():
            raise FbarError(f"Statement PDF not found: {raw_path}", 2)

        page_count = 0
        char_count = 0
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            for page_index, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                char_count += len(page_text)
                full_text_parts.append(page_text)
                for line_index, line in enumerate(page_text.splitlines(), start=1):
                    clean_line = clean_text(line)
                    if clean_line:
                        ref = SourceRef(str(path), page_index, line_index, clean_line)
                        all_lines.append((ref, clean_line))

        if char_count < MIN_TEXT_CHARS:
            warnings.append(f"{path.name} has little machine-readable text; scanned/image-only PDFs are out of scope for v1.")
        file_profiles.append({"path": str(path), "pages": page_count, "text_chars": char_count})

    return all_lines, file_profiles, "\n".join(full_text_parts), warnings


def extract_balance_candidates(
    lines: list[tuple[SourceRef, str]],
    tax_year: int,
    currency: str,
) -> tuple[list[BalanceCandidate], list[str]]:
    warnings: list[str] = []
    candidates: list[BalanceCandidate] = []
    page_text: dict[tuple[str, int], str] = defaultdict(str)
    for ref, line in lines:
        page_text[(ref.file, ref.page)] += " " + line.lower()

    outside_year = 0
    low_confidence = 0
    for ref, line in lines:
        lower = line.lower()
        date_hits = parse_line_dates(line)
        if not date_hits:
            continue
        money_values = parse_money_values(line)
        if not money_values:
            continue

        has_balance_term = any(term in lower for term in BALANCE_TERMS)
        header_has_balance = any(term in page_text[(ref.file, ref.page)] for term in BALANCE_TERMS)
        if not has_balance_term and not (header_has_balance and len(money_values) >= 3):
            continue
        if any(term in lower for term in LOW_VALUE_TERMS) and not has_balance_term:
            continue

        amount, token = money_values[-1]
        for parsed_date, date_confidence, date_note in date_hits:
            if parsed_date.year != tax_year:
                outside_year += 1
                continue
            confidence = "high" if has_balance_term and date_confidence == "high" else "medium"
            notes = [f"Selected last monetary value on line: {token}", date_note]
            if date_confidence == "low":
                confidence = "low"
                low_confidence += 1
            if not has_balance_term:
                confidence = "medium" if confidence == "high" else confidence
                notes.append("Line inferred from a page/table containing balance language.")
            candidates.append(
                BalanceCandidate(
                    balance_date=parsed_date,
                    amount=amount,
                    currency=currency,
                    confidence=confidence,
                    source=ref,
                    notes=tuple(notes),
                )
            )

    if outside_year:
        warnings.append(f"Ignored {outside_year} balance candidate date(s) outside the requested tax year.")
    if low_confidence:
        warnings.append(f"{low_confidence} balance candidate date(s) used ambiguous numeric date interpretation.")
    if not candidates:
        warnings.append("No balance candidates were extracted from the statement text.")
    return candidates, warnings


def build_daily_rows(tax_year: int, currency: str, candidates: list[BalanceCandidate]) -> tuple[list[dict[str, object]], dict[str, object], list[str]]:
    warnings: list[str] = []
    by_day: dict[date, list[BalanceCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_day[candidate.balance_date].append(candidate)

    last_balance: Decimal | None = None
    last_ref: SourceRef | None = None
    rows: list[dict[str, object]] = []
    observed_days = 0
    carried_days = 0
    missing_days = 0
    low_confidence_days = 0

    for day in calendar_dates(tax_year):
        day_candidates = by_day.get(day, [])
        if day_candidates:
            selected = max(day_candidates, key=lambda item: item.amount)
            last_balance = selected.amount
            last_ref = selected.source
            observed_days += 1
            if selected.confidence == "low":
                low_confidence_days += 1
            rows.append(
                {
                    "date": iso_day(day),
                    "currency": currency,
                    "native_balance": fmt_decimal(selected.amount),
                    "usd_balance": None,
                    "threshold_usd_value": None,
                    "balance_source": "observed",
                    "confidence": selected.confidence,
                    "source_refs": [source_ref_to_string(selected.source)],
                    "notes": list(selected.notes),
                }
            )
        elif last_balance is not None:
            carried_days += 1
            rows.append(
                {
                    "date": iso_day(day),
                    "currency": currency,
                    "native_balance": fmt_decimal(last_balance),
                    "usd_balance": None,
                    "threshold_usd_value": None,
                    "balance_source": "carried-forward",
                    "confidence": "medium",
                    "source_refs": [source_ref_to_string(last_ref)] if last_ref else [],
                    "notes": ["No same-day balance found; carried forward most recent observed account balance."],
                }
            )
        else:
            missing_days += 1
            rows.append(
                {
                    "date": iso_day(day),
                    "currency": currency,
                    "native_balance": None,
                    "usd_balance": None,
                    "threshold_usd_value": None,
                    "balance_source": "missing-opening-coverage",
                    "confidence": "missing",
                    "source_refs": [],
                    "notes": ["No opening or prior balance was available for this day."],
                }
            )

    if missing_days:
        warnings.append(f"{missing_days} day(s) lack opening/prior balance coverage; do not return a confident daily-threshold no.")
    if low_confidence_days:
        warnings.append(f"{low_confidence_days} day(s) have low-confidence balances and need review.")

    coverage = {
        "year": tax_year,
        "calendar_days": len(rows),
        "observed_days": observed_days,
        "carried_forward_days": carried_days,
        "missing_days": missing_days,
        "complete_year": missing_days == 0,
        "low_confidence_days": low_confidence_days,
    }
    return rows, coverage, warnings


def source_ref_to_string(ref: SourceRef | None) -> str:
    if ref is None:
        return ""
    return f"{Path(ref.file).name}:p{ref.page}:l{ref.line}"


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FbarError(f"File not found: {path}", 2) from exc
    except json.JSONDecodeError as exc:
        raise FbarError(f"Could not parse JSON file {path}: {exc}", 2) from exc


def account_csv_path(out_path: Path, confirmed: bool = False) -> Path:
    suffix = "confirmed" if confirmed else "review"
    return out_path.with_name(out_path.stem + f"-{suffix}.csv")


def combined_csv_path(out_path: Path) -> Path:
    return out_path.with_suffix(".csv")


def summary_pdf_path(out_path: Path) -> Path:
    return out_path.with_suffix(".pdf")


def write_account_csv(path: Path, account_data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = account_data.get("daily_ledger", [])
    if not isinstance(rows, list):
        raise FbarError("Account JSON has no daily_ledger list.", 3)
    fields = [
        "date",
        "account_id",
        "institution",
        "currency",
        "native_balance",
        "usd_balance",
        "threshold_usd_value",
        "balance_source",
        "confidence",
        "source_refs",
        "notes",
    ]
    account = account_data.get("account", {})
    if not isinstance(account, dict):
        account = {}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                continue
            writer.writerow(
                {
                    "date": row.get("date"),
                    "account_id": account.get("account_id"),
                    "institution": account.get("institution"),
                    "currency": row.get("currency"),
                    "native_balance": row.get("native_balance"),
                    "usd_balance": row.get("usd_balance"),
                    "threshold_usd_value": row.get("threshold_usd_value"),
                    "balance_source": row.get("balance_source"),
                    "confidence": row.get("confidence"),
                    "source_refs": "; ".join(row.get("source_refs", []) if isinstance(row.get("source_refs"), list) else []),
                    "notes": "; ".join(row.get("notes", []) if isinstance(row.get("notes"), list) else []),
                }
            )


def command_extract_account(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    warnings: list[str] = []
    lines, file_profiles, full_text, pdf_warnings = load_pdf_lines(args.pdf)
    warnings.extend(pdf_warnings)

    currency = infer_currency(full_text, args.account_currency, warnings)
    account_hints = extract_account_hints(full_text)
    if len(account_hints) > 1:
        warnings.append(f"Multiple account hints found; verify this is one account: {', '.join(account_hints[:8])}.")
    if not account_hints:
        warnings.append("No account number/designation hint was found; verify this is one account.")
    if currency in {"UNKNOWN", "MIXED"}:
        warnings.append("Account currency is not confirmed; do not confirm this ledger until resolved.")

    candidates, candidate_warnings = extract_balance_candidates(lines, args.tax_year, currency)
    warnings.extend(candidate_warnings)
    daily_rows, coverage, coverage_warnings = build_daily_rows(args.tax_year, currency, candidates)
    warnings.extend(coverage_warnings)

    account_id = args.account_id or make_account_id(args.institution, account_hints, args.tax_year)
    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "skill": "fbar-threshold-check",
        "status": "extracted-review-required",
        "tax_year": args.tax_year,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "account": {
            "account_id": account_id,
            "institution": args.institution or infer_institution(full_text),
            "currency": currency,
            "account_number_hints": account_hints,
        },
        "statement_files": file_profiles,
        "coverage": coverage,
        "fx": {
            "required": currency not in {"USD", "UNKNOWN", "MIXED"},
            "workpaper_json": None,
            "workpaper": None,
        },
        "warnings": sorted(set(warnings)),
        "daily_ledger": daily_rows,
        "artifacts": {},
    }
    csv_path = Path(args.csv) if args.csv else account_csv_path(out_path)
    data["artifacts"] = {"review_csv": str(csv_path)}
    write_json(out_path, data)
    write_account_csv(csv_path, data)
    print(f"Wrote account JSON: {out_path}")
    print(f"Wrote review CSV: {csv_path}")
    if warnings:
        print("Review warnings:")
        for warning in sorted(set(warnings)):
            print(f"- {warning}")
    return 0


def make_account_id(institution: str | None, account_hints: list[str], year: int) -> str:
    base = institution or (account_hints[0] if account_hints else "account")
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base.lower()).strip("-") or "account"
    hint = re.sub(r"[^a-zA-Z0-9]+", "", account_hints[0])[-4:] if account_hints else ""
    return f"{base}-{hint or year}"


def infer_institution(full_text: str) -> str | None:
    for line in full_text.splitlines()[:12]:
        cleaned = clean_text(line)
        if 3 <= len(cleaned) <= 80 and not re.search(r"\d{2,}", cleaned):
            if not any(term in cleaned.lower() for term in ("statement", "extracto", "period", "account", "cuenta")):
                return cleaned
    return None


def require_account_data(path: Path) -> dict[str, object]:
    data = load_json(path)
    if data.get("skill") != "fbar-threshold-check":
        raise FbarError(f"{path} is not an fbar-threshold-check account ledger.", 2)
    if "daily_ledger" not in data:
        raise FbarError(f"{path} has no daily_ledger.", 2)
    return data


def as_decimal(value: object, label: str) -> Decimal:
    if value is None or value == "":
        raise FbarError(f"Missing decimal value for {label}.", 2)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise FbarError(f"Could not parse decimal value for {label}: {value}", 2) from exc


def validate_fx_workpaper(path: Path, currency: str, year: int) -> dict[str, object]:
    workpaper = load_json(path)
    if workpaper.get("skill") != "get-yearly-fx-rate":
        raise FbarError("FX workpaper must come from get-yearly-fx-rate.", 2)
    if str(workpaper.get("currency", "")).upper() != currency:
        raise FbarError(f"FX workpaper currency {workpaper.get('currency')} does not match account currency {currency}.", 2)
    if int(workpaper.get("year", 0)) != year:
        raise FbarError(f"FX workpaper year {workpaper.get('year')} does not match tax year {year}.", 2)

    source = workpaper.get("source")
    proof = workpaper.get("proof")
    if not isinstance(source, dict):
        raise FbarError("FX workpaper is missing source metadata.", 2)
    if not isinstance(proof, dict):
        raise FbarError("FX workpaper is missing proof metadata.", 2)
    if not source.get("title") or not source.get("url") or not source.get("retrieved"):
        raise FbarError("FX workpaper source must include title, URL, and retrieval date.", 2)
    if not proof.get("workpaper_json") and not proof.get("workpaper_pdf") and not proof.get("saved_files"):
        raise FbarError("FX workpaper proof must include retained artifact paths.", 2)

    foreign_per_usd = workpaper.get("foreign_per_usd")
    usd_per_foreign = workpaper.get("usd_per_foreign")
    if not foreign_per_usd and not usd_per_foreign:
        raise FbarError("FX workpaper must include foreign_per_usd or usd_per_foreign.", 2)

    if not is_fbar_compatible_workpaper(workpaper):
        raise FbarError(
            "FX workpaper appears to be yearly-average only. Complete/update get-yearly-fx-rate so the workpaper is explicitly FBAR/year-end compatible before confirming this account.",
            2,
        )
    return workpaper


def is_fbar_compatible_workpaper(workpaper: dict[str, object]) -> bool:
    if workpaper.get("fbar_compatible") is True:
        return True

    source = workpaper.get("source", {})
    if not isinstance(source, dict):
        source = {}
    caveats = workpaper.get("caveats", [])
    proof = workpaper.get("proof", {})

    values: list[str] = []
    for key in ("rate_kind", "method", "rate_type", "conversion_context", "use_case", "rate_context"):
        value = workpaper.get(key)
        if value is not None:
            values.append(str(value))
    for key in ("title", "category", "note", "url"):
        value = source.get(key)
        if value is not None:
            values.append(str(value))
    if isinstance(proof, dict):
        for key in ("note", "source_kind"):
            value = proof.get(key)
            if value is not None:
                values.append(str(value))
    if isinstance(caveats, list):
        values.extend(str(item) for item in caveats)

    text = " ".join(values).lower()
    positive_terms = (
        "fbar",
        "fincen",
        "year-end",
        "year end",
        "year_end",
        "last day",
        "12/31",
        "12-31",
        "december 31",
        "treasury financial management service",
        "financial management service",
        "treasury reporting",
        "fms",
    )
    average_terms = (
        "yearly average",
        "yearly-average",
        "annual average",
        "annual-average",
        "period average",
        "average exchange rate",
    )
    has_positive = any(term in text for term in positive_terms)
    has_only_average = any(term in text for term in average_terms) and not has_positive
    return has_positive and not has_only_average


def command_confirm_account(args: argparse.Namespace) -> int:
    if not args.balances_confirmed:
        raise FbarError("Pass --balances-confirmed only after reviewing the account JSON/CSV with the user.", 2)

    input_path = Path(args.input)
    out_path = Path(args.out)
    data = require_account_data(input_path)
    coverage = data.get("coverage", {})
    if not isinstance(coverage, dict):
        raise FbarError("Account ledger has no coverage object.", 2)
    if int(coverage.get("missing_days", 0)) > 0:
        raise FbarError("Account ledger has missing days; do not confirm until opening/prior balance coverage is resolved.", 2)
    if int(coverage.get("low_confidence_days", 0)) > 0:
        raise FbarError("Account ledger has low-confidence balance days; resolve or regenerate before confirmation.", 2)

    account = data.get("account", {})
    if not isinstance(account, dict):
        raise FbarError("Account ledger has no account object.", 2)
    currency = str(account.get("currency") or "UNKNOWN").upper()
    tax_year = int(data.get("tax_year", 0))
    if currency in {"UNKNOWN", "MIXED"}:
        raise FbarError("Account currency is not confirmed; regenerate with --account-currency or split the account.", 2)

    fx_workpaper: dict[str, object] | None = None
    if currency != "USD":
        if not args.fx_workpaper_json:
            raise FbarError("Non-USD account requires --fx-workpaper-json from get-yearly-fx-rate.", 2)
        fx_workpaper = validate_fx_workpaper(Path(args.fx_workpaper_json), currency, tax_year)
    elif args.fx_workpaper_json:
        raise FbarError("Do not pass FX workpaper for USD accounts.", 2)

    rows = data.get("daily_ledger", [])
    if not isinstance(rows, list):
        raise FbarError("Account ledger daily_ledger is not a list.", 2)

    for row in rows:
        if not isinstance(row, dict):
            continue
        native = as_decimal(row.get("native_balance"), f"{row.get('date')} native_balance")
        usd = convert_to_usd(native, currency, fx_workpaper)
        threshold_value = usd if usd > 0 else Decimal("0")
        row["usd_balance"] = fmt_decimal(usd)
        row["threshold_usd_value"] = fmt_decimal(threshold_value)
        if native < 0:
            notes = row.get("notes")
            if not isinstance(notes, list):
                notes = []
            notes.append("Negative balance treated as zero for threshold and maximum-value calculations.")
            row["notes"] = notes

    data["status"] = "confirmed"
    data["confirmed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    data["confirmation"] = {
        "balances_confirmed": True,
        "fx_confirmed": currency == "USD" or fx_workpaper is not None,
    }
    if fx_workpaper is not None:
        data["fx"] = {
            "required": True,
            "workpaper_json": str(Path(args.fx_workpaper_json)),
            "workpaper": summarize_fx_workpaper(fx_workpaper),
        }
    else:
        data["fx"] = {"required": False, "workpaper_json": None, "workpaper": None}

    csv_path = Path(args.csv) if args.csv else account_csv_path(out_path, confirmed=True)
    artifacts = data.get("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    artifacts["confirmed_csv"] = str(csv_path)
    data["artifacts"] = artifacts
    write_json(out_path, data)
    write_account_csv(csv_path, data)
    print(f"Wrote confirmed account JSON: {out_path}")
    print(f"Wrote confirmed CSV: {csv_path}")
    return 0


def summarize_fx_workpaper(workpaper: dict[str, object]) -> dict[str, object]:
    source = workpaper.get("source", {})
    proof = workpaper.get("proof", {})
    return {
        "skill": workpaper.get("skill"),
        "currency": workpaper.get("currency"),
        "year": workpaper.get("year"),
        "rate": workpaper.get("rate"),
        "rate_direction": workpaper.get("rate_direction"),
        "foreign_per_usd": workpaper.get("foreign_per_usd"),
        "usd_per_foreign": workpaper.get("usd_per_foreign"),
        "source": source if isinstance(source, dict) else {},
        "proof": proof if isinstance(proof, dict) else {},
    }


def convert_to_usd(native: Decimal, currency: str, workpaper: dict[str, object] | None) -> Decimal:
    if currency == "USD":
        return native
    if workpaper is None:
        raise FbarError("Missing FX workpaper for non-USD conversion.", 2)
    if workpaper.get("foreign_per_usd"):
        rate = as_decimal(workpaper.get("foreign_per_usd"), "foreign_per_usd")
        if rate == 0:
            raise FbarError("FX foreign_per_usd rate cannot be zero.", 2)
        return native / rate
    rate = as_decimal(workpaper.get("usd_per_foreign"), "usd_per_foreign")
    return native * rate


def load_confirmed_account(path: Path) -> dict[str, object]:
    data = require_account_data(path)
    if data.get("status") != "confirmed":
        raise FbarError(f"{path} is not confirmed. Run confirm-account first.", 2)
    coverage = data.get("coverage", {})
    if isinstance(coverage, dict) and int(coverage.get("missing_days", 0)) > 0:
        raise FbarError(f"{path} has incomplete coverage and cannot be aggregated.", 2)
    rows = data.get("daily_ledger", [])
    if not isinstance(rows, list):
        raise FbarError(f"{path} has no daily_ledger list.", 2)
    for row in rows:
        if isinstance(row, dict) and row.get("threshold_usd_value") is None:
            raise FbarError(f"{path} has unconverted rows. Run confirm-account again.", 2)
    return data


def command_aggregate(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    accounts = [load_confirmed_account(Path(path)) for path in args.account_ledger]
    if not accounts:
        raise FbarError("At least one --account-ledger is required.", 2)

    tax_years = {int(account.get("tax_year", 0)) for account in accounts}
    if len(tax_years) != 1:
        raise FbarError("All account ledgers must have the same tax year.", 2)
    tax_year = tax_years.pop()
    days = [iso_day(day) for day in calendar_dates(tax_year)]

    account_labels = unique_account_labels(accounts)
    account_rows: dict[str, dict[str, dict[str, object]]] = {}
    account_summaries: list[dict[str, object]] = []
    warnings: list[str] = []
    fx_workpapers: list[object] = []

    for label, account_data in zip(account_labels, accounts, strict=True):
        rows = account_data["daily_ledger"]
        assert isinstance(rows, list)
        by_date = {str(row.get("date")): row for row in rows if isinstance(row, dict)}
        account_rows[label] = by_date
        account = account_data.get("account", {})
        if not isinstance(account, dict):
            account = {}
        values = [as_decimal(row.get("threshold_usd_value"), f"{label} {row.get('date')} threshold") for row in by_date.values()]
        max_value = max(values) if values else Decimal("0")
        max_whole = ceil_nonnegative(max_value)
        account_summaries.append(
            {
                "account_id": label,
                "institution": account.get("institution"),
                "currency": account.get("currency"),
                "max_usd_value": fmt_decimal(max_value),
                "max_usd_value_whole_dollars": max_whole,
                "source_json": account_data.get("artifacts", {}),
            }
        )
        fx = account_data.get("fx", {})
        if isinstance(fx, dict) and fx.get("workpaper_json"):
            fx_workpapers.append(fx.get("workpaper_json"))
        if account_data.get("warnings"):
            warnings.extend(f"{label}: {warning}" for warning in account_data.get("warnings", []) if isinstance(warning, str))

    combined_rows: list[dict[str, object]] = []
    over_limit_dates: list[str] = []
    max_combined = Decimal("0")
    max_combined_date: str | None = None

    for day in days:
        combined = Decimal("0")
        row: dict[str, object] = {"date": day}
        for label in account_labels:
            account_day = account_rows[label].get(day)
            if account_day is None:
                raise FbarError(f"Account {label} is missing day {day}.", 2)
            value = as_decimal(account_day.get("threshold_usd_value"), f"{label} {day} threshold")
            row[label] = fmt_decimal(value)
            combined += value
        row["combined_usd_value"] = fmt_decimal(combined)
        row["over_10000"] = combined > THRESHOLD_USD
        if combined > THRESHOLD_USD:
            over_limit_dates.append(day)
        if combined > max_combined:
            max_combined = combined
            max_combined_date = day
        combined_rows.append(row)

    aggregate_max_whole = sum(int(item["max_usd_value_whole_dollars"]) for item in account_summaries)
    csv_path = Path(args.csv) if args.csv else combined_csv_path(out_path)
    pdf_path = Path(args.pdf) if args.pdf else summary_pdf_path(out_path)

    summary: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "skill": "fbar-threshold-check",
        "status": "aggregated",
        "tax_year": tax_year,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "account_count": len(accounts),
        "daily_threshold": {
            "threshold_usd": "10000",
            "exceeded": bool(over_limit_dates),
            "over_limit_dates": over_limit_dates,
            "max_combined_usd_value": fmt_decimal(max_combined),
            "max_combined_date": max_combined_date,
            "records_complete": True,
        },
        "fincen_max_value_view": {
            "threshold_usd": "10000",
            "exceeded": aggregate_max_whole > 10000,
            "aggregate_account_max_whole_dollars": aggregate_max_whole,
            "account_maxima": account_summaries,
        },
        "accounts": account_summaries,
        "fx_workpapers": fx_workpapers,
        "warnings": sorted(set(warnings)),
        "artifacts": {
            "combined_csv": str(csv_path),
            "summary_pdf": str(pdf_path),
        },
    }

    write_combined_csv(csv_path, account_labels, combined_rows)
    write_summary_pdf(pdf_path, summary)
    write_json(out_path, summary)

    print(f"Daily threshold exceeded: {'yes' if over_limit_dates else 'no'}")
    print(f"FinCEN max-value view exceeded: {'yes' if aggregate_max_whole > 10000 else 'no'}")
    if over_limit_dates:
        print(f"Over-limit dates: {', '.join(over_limit_dates[:20])}{' ...' if len(over_limit_dates) > 20 else ''}")
    print(f"Wrote final JSON: {out_path}")
    print(f"Wrote final CSV: {csv_path}")
    print(f"Wrote summary PDF: {pdf_path}")
    return 0


def unique_account_labels(accounts: list[dict[str, object]]) -> list[str]:
    labels: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    for account_data in accounts:
        account = account_data.get("account", {})
        if not isinstance(account, dict):
            account = {}
        base = str(account.get("account_id") or f"account-{len(labels) + 1}")
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-") or f"account-{len(labels) + 1}"
        counts[base] += 1
        labels.append(base if counts[base] == 1 else f"{base}-{counts[base]}")
    return labels


def ceil_nonnegative(value: Decimal) -> int:
    if value <= 0:
        return 0
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def write_combined_csv(path: Path, account_labels: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["date", *account_labels, "combined_usd_value", "over_10000"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def write_summary_pdf(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    daily = summary["daily_threshold"]
    max_view = summary["fincen_max_value_view"]
    assert isinstance(daily, dict)
    assert isinstance(max_view, dict)
    lines = [
        f"FBAR Threshold Check - {summary['tax_year']}",
        "",
        f"Daily threshold: {'YES' if daily['exceeded'] else 'NO'}",
        f"Maximum combined daily USD value: {daily.get('max_combined_usd_value')} on {daily.get('max_combined_date')}",
        f"FinCEN maximum-value view: {'YES' if max_view['exceeded'] else 'NO'}",
        f"Aggregate account maximums, rounded up to whole dollars: {max_view.get('aggregate_account_max_whole_dollars')}",
        "",
        "Over-$10,000 dates:",
    ]
    over_dates = daily.get("over_limit_dates", [])
    if isinstance(over_dates, list) and over_dates:
        lines.extend(str(item) for item in over_dates[:60])
        if len(over_dates) > 60:
            lines.append(f"... {len(over_dates) - 60} additional date(s); see CSV/JSON.")
    else:
        lines.append("None in reviewed records.")

    lines.extend(["", "Accounts:"])
    accounts = summary.get("accounts", [])
    if isinstance(accounts, list):
        for account in accounts:
            if isinstance(account, dict):
                lines.append(
                    f"- {account.get('account_id')}: max USD {account.get('max_usd_value')} "
                    f"(whole dollars {account.get('max_usd_value_whole_dollars')})"
                )

    warnings = summary.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings[:20])
        if len(warnings) > 20:
            lines.append(f"... {len(warnings) - 20} additional warning(s); see JSON.")

    lines.extend(
        [
            "",
            "This is a support summary, not an official FBAR filing and not tax/legal advice.",
        ]
    )
    path.write_bytes(simple_pdf_bytes(lines, title=f"FBAR Threshold Check {summary['tax_year']}"))


def simple_pdf_bytes(lines: list[str], title: str) -> bytes:
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
        else:
            wrapped.extend(textwrap.wrap(line, width=88) or [""])
    pages = [wrapped[index : index + 48] for index in range(0, len(wrapped), 48)] or [[]]

    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    page_ids: list[int] = []
    next_id = 4
    for page_lines in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        content = render_pdf_text_stream(page_lines)
        objects[content_id] = (
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    output = bytearray()
    output.extend(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for obj_id in range(1, max(objects) + 1):
        offsets[obj_id] = len(output)
        output.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        output.extend(objects[obj_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {max(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for obj_id in range(1, max(objects) + 1):
        output.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))
    escaped_title = pdf_escape(title)
    output.extend(
        (
            f"trailer\n<< /Size {max(objects) + 1} /Root 1 0 R /Info << /Title ({escaped_title}) >> >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def render_pdf_text_stream(lines: list[str]) -> bytes:
    parts = ["BT", "/F1 11 Tf", "72 740 Td", "14 TL"]
    for index, line in enumerate(lines):
        if index:
            parts.append("T*")
        parts.append(f"({pdf_escape(line)}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("ascii")


def pdf_escape(text: object) -> str:
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def command_dependency_check(_args: argparse.Namespace) -> int:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("pdfplumber missing")
        return 1
    print("pdfplumber ok")
    return 0


def command_self_test(_args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        test_build_daily_rows()
        test_leap_year()
        test_fx_guardrails(root)
        test_confirm_and_aggregate(root)
        test_maxima_disagreement(root)
    print("self-test ok")
    return 0


def synthetic_account(
    root: Path,
    name: str,
    year: int,
    currency: str,
    start_balance: Decimal,
    changes: dict[str, Decimal],
    fx_workpaper: Path | None = None,
) -> Path:
    candidates = [
        BalanceCandidate(
            balance_date=date(year, 1, 1),
            amount=start_balance,
            currency=currency,
            confidence="high",
            source=SourceRef(f"{name}.pdf", 1, 1, "synthetic opening balance"),
            notes=("synthetic self-test row",),
        )
    ]
    for raw_day, value in changes.items():
        candidates.append(
            BalanceCandidate(
                balance_date=date.fromisoformat(raw_day),
                amount=value,
                currency=currency,
                confidence="high",
                source=SourceRef(f"{name}.pdf", 1, 2, "synthetic balance"),
                notes=("synthetic self-test row",),
            )
        )
    rows, coverage, warnings = build_daily_rows(year, currency, candidates)
    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "skill": "fbar-threshold-check",
        "status": "extracted-review-required",
        "tax_year": year,
        "account": {"account_id": name, "institution": "Self Test Bank", "currency": currency, "account_number_hints": [name]},
        "statement_files": [],
        "coverage": coverage,
        "fx": {"required": currency != "USD", "workpaper_json": None, "workpaper": None},
        "warnings": warnings,
        "daily_ledger": rows,
        "artifacts": {},
    }
    extracted = root / f"{name}.json"
    confirmed = root / f"{name}-confirmed.json"
    write_json(extracted, data)
    confirm_args = argparse.Namespace(
        input=str(extracted),
        out=str(confirmed),
        csv=None,
        balances_confirmed=True,
        fx_workpaper_json=str(fx_workpaper) if fx_workpaper else None,
    )
    command_confirm_account(confirm_args)
    return confirmed


def make_fbar_fx_workpaper(root: Path, currency: str = "COP", year: int = 2025) -> Path:
    path = root / f"{currency.lower()}-{year}-workpaper.json"
    data = {
        "skill": "get-yearly-fx-rate",
        "currency": currency,
        "year": year,
        "rate": "4000",
        "rate_direction": "foreign-per-usd",
        "foreign_per_usd": "4000",
        "usd_per_foreign": "0.00025",
        "rate_kind": "FBAR year-end Treasury/FMS compatible rate",
        "source": {
            "title": "Example Treasury year-end source",
            "url": "https://example.test/fbar",
            "retrieved": "2026-07-04",
            "category": "FBAR year-end source",
            "note": "Source supports FBAR year-end conversion.",
        },
        "proof": {
            "workpaper_json": str(path),
            "workpaper_pdf": str(root / "proof.pdf"),
            "saved_files": [{"path": str(root / "proof.json"), "sha256": "abc"}],
        },
    }
    write_json(path, data)
    return path


def make_yearly_average_workpaper(root: Path) -> Path:
    path = root / "yearly-average-workpaper.json"
    data = {
        "skill": "get-yearly-fx-rate",
        "currency": "CAD",
        "year": 2025,
        "rate": "1.37",
        "rate_direction": "foreign-per-usd",
        "foreign_per_usd": "1.37",
        "usd_per_foreign": "0.729927",
        "source": {
            "title": "IRS Yearly average currency exchange rates",
            "url": "https://www.irs.gov/",
            "retrieved": "2026-07-04",
            "category": "IRS yearly average table",
            "note": "IRS yearly average table row.",
        },
        "proof": {
            "workpaper_json": str(path),
            "workpaper_pdf": str(root / "proof.pdf"),
            "saved_files": [{"path": str(root / "proof.html"), "sha256": "abc"}],
        },
    }
    write_json(path, data)
    return path


def test_build_daily_rows() -> None:
    candidates = [
        BalanceCandidate(date(2025, 1, 1), Decimal("100"), "USD", "high", SourceRef("a.pdf", 1, 1, "")),
        BalanceCandidate(date(2025, 1, 1), Decimal("150"), "USD", "high", SourceRef("a.pdf", 1, 2, "")),
        BalanceCandidate(date(2025, 1, 3), Decimal("125"), "USD", "high", SourceRef("a.pdf", 1, 3, "")),
    ]
    rows, coverage, warnings = build_daily_rows(2025, "USD", candidates)
    assert len(rows) == 365
    assert rows[0]["native_balance"] == "150"
    assert rows[1]["native_balance"] == "150"
    assert rows[2]["native_balance"] == "125"
    assert coverage["complete_year"] is True
    assert not warnings


def test_leap_year() -> None:
    candidates = [BalanceCandidate(date(2024, 1, 1), Decimal("1"), "USD", "high", SourceRef("a.pdf", 1, 1, ""))]
    rows, coverage, _warnings = build_daily_rows(2024, "USD", candidates)
    assert len(rows) == 366
    assert coverage["calendar_days"] == 366


def test_fx_guardrails(root: Path) -> None:
    valid = make_fbar_fx_workpaper(root)
    data = validate_fx_workpaper(valid, "COP", 2025)
    assert data["foreign_per_usd"] == "4000"
    invalid = make_yearly_average_workpaper(root)
    try:
        validate_fx_workpaper(invalid, "CAD", 2025)
    except FbarError as exc:
        assert "yearly-average" in str(exc) or "yearly-average" in exc.args[0]
    else:
        raise AssertionError("yearly-average-only workpaper should be rejected")


def test_confirm_and_aggregate(root: Path) -> None:
    fx = make_fbar_fx_workpaper(root)
    usd = synthetic_account(root, "usd-a", 2025, "USD", Decimal("5000"), {"2025-06-01": Decimal("8000")})
    cop = synthetic_account(root, "cop-b", 2025, "COP", Decimal("4000000"), {"2025-06-01": Decimal("12000000")}, fx)
    out = root / "summary.json"
    command_aggregate(argparse.Namespace(account_ledger=[str(usd), str(cop)], out=str(out), csv=None, pdf=None))
    summary = load_json(out)
    assert summary["daily_threshold"]["exceeded"] is True  # type: ignore[index]
    assert "2025-06-01" in summary["daily_threshold"]["over_limit_dates"]  # type: ignore[index]
    assert (root / "summary.csv").exists()
    assert (root / "summary.pdf").read_bytes().startswith(b"%PDF")


def test_maxima_disagreement(root: Path) -> None:
    a = synthetic_account(root, "max-a", 2025, "USD", Decimal("8000"), {"2025-01-02": Decimal("0")})
    b = synthetic_account(root, "max-b", 2025, "USD", Decimal("0"), {"2025-01-02": Decimal("8000"), "2025-01-03": Decimal("0")})
    out = root / "max-summary.json"
    command_aggregate(argparse.Namespace(account_ledger=[str(a), str(b)], out=str(out), csv=None, pdf=None))
    summary = load_json(out)
    assert summary["daily_threshold"]["exceeded"] is False  # type: ignore[index]
    assert summary["fincen_max_value_view"]["exceeded"] is True  # type: ignore[index]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract-account", help="Extract one account's daily FBAR ledger from machine-readable PDFs.")
    extract.add_argument("--pdf", nargs="+", required=True, help="Statement PDFs for exactly one account and one tax year.")
    extract.add_argument("--tax-year", type=int, required=True, help="Calendar year to check.")
    extract.add_argument("--out", required=True, help="Output account JSON path.")
    extract.add_argument("--csv", help="Optional review CSV path.")
    extract.add_argument("--account-id", help="Optional stable local account identifier.")
    extract.add_argument("--institution", help="Optional institution label.")
    extract.add_argument("--account-currency", help="Optional ISO currency code when statement text is ambiguous.")
    extract.set_defaults(func=command_extract_account)

    confirm = subparsers.add_parser("confirm-account", help="Confirm reviewed account ledger and convert to USD when needed.")
    confirm.add_argument("--input", required=True, help="Extracted account JSON.")
    confirm.add_argument("--out", required=True, help="Confirmed account JSON.")
    confirm.add_argument("--csv", help="Optional confirmed CSV path.")
    confirm.add_argument("--balances-confirmed", action="store_true", help="Required after review of the account ledger.")
    confirm.add_argument("--fx-workpaper-json", help="get-yearly-fx-rate workpaper JSON for non-USD accounts.")
    confirm.set_defaults(func=command_confirm_account)

    aggregate = subparsers.add_parser("aggregate", help="Aggregate confirmed account ledgers into final FBAR threshold artifacts.")
    aggregate.add_argument("--account-ledger", nargs="+", required=True, help="Confirmed account JSON ledgers.")
    aggregate.add_argument("--out", required=True, help="Final summary JSON path.")
    aggregate.add_argument("--csv", help="Optional final combined daily CSV path.")
    aggregate.add_argument("--pdf", help="Optional final human summary PDF path.")
    aggregate.set_defaults(func=command_aggregate)

    dependency = subparsers.add_parser("dependency-check", help="Check extraction dependency availability.")
    dependency.set_defaults(func=command_dependency_check)

    self_test = subparsers.add_parser("self-test", help="Run deterministic ledger, FX, aggregation, and PDF tests.")
    self_test.set_defaults(func=command_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FbarError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
