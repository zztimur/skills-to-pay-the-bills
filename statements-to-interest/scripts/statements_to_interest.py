#!/usr/bin/env python3
"""Extract foreign bank statement interest and generate an IRS-oriented packet."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

try:
    import pdfplumber
except ImportError:  # pragma: no cover - exercised by users without deps.
    pdfplumber = None

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError:  # pragma: no cover - exercised by users without deps.
    colors = None


SCHEMA_VERSION = "1.0"
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
    "PLN",
    "RUB",
    "SAR",
    "SEK",
    "SGD",
    "THB",
    "TRY",
    "TWD",
    "USD",
    "ZAR",
}

SYMBOL_TO_CODE = {
    "$": "USD",
    "\u20ac": "EUR",
    "\u00a3": "GBP",
    "\u00a5": "JPY",
}

FX_METHOD_LABELS = {
    "irs-yearly-average": "IRS yearly average exchange rate",
    "get-yearly-fx-rate": "get-yearly-fx-rate published yearly average workpaper",
    "posted-daily-spot": "posted daily spot exchange rate",
    "posted-yearly-average": "published yearly average exchange rate",
    "user-rate": "user-provided exchange rate",
}

SELF_CALCULATED_AVERAGE_TERMS = (
    "arithmetic average",
    "calculated average",
    "calculated from daily",
    "computed average",
    "computed from daily",
    "daily series average",
    "daily values",
    "365 daily",
    "average of daily",
    "averaged daily",
    "agent-calculated",
    "self-calculated",
)

POSITIVE_INTEREST_TERMS = (
    "interest credited",
    "interest credit",
    "interest earned",
    "credit interest",
    "deposit interest",
    "savings interest",
    "interest income",
    "interest paid to you",
    "intereses acreditados",
    "interes acreditado",
    "intereses abonados",
    "intereses ganados",
    "rendimientos",
    "rendimento",
    "juros creditados",
    "juros recebidos",
    "zinsen",
    "zinsertrag",
    "interet credite",
    "interets credites",
    "interet gagne",
    "renteinntekt",
    "rente indtaegt",
    "faiz geliri",
    "bunga diterima",
)

GENERIC_INTEREST_TERMS = (
    "interest",
    "interes",
    "intereses",
    "interesse",
    "interet",
    "interets",
    "juros",
    "zins",
    "zinsen",
    "rente",
    "faiz",
    "bunga",
)

EXCLUDE_TERMS = (
    "interest charged",
    "debit interest",
    "overdraft interest",
    "loan interest",
    "margin interest",
    "credit card interest",
    "interest expense",
    "interest fee",
    "fee",
    "charge",
    "charged",
    "commission",
    "comision",
    "comissao",
    "cargo",
    "cobro",
    "debito",
    "debit",
    "impuesto",
    "tax",
    "withholding",
    "withheld",
    "retencion",
    "retencao",
    "iva",
    "vat",
)

PERIOD_TERMS = (
    "statement period",
    "periodo",
    "period",
    "periode",
    "extracto",
    "from",
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

DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b"),
)

MONTH_DATE_PATTERNS = (
    re.compile(
        r"\b("
        + "|".join(re.escape(k) for k in sorted(MONTHS, key=len, reverse=True))
        + r")\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2})\s+("
        + "|".join(re.escape(k) for k in sorted(MONTHS, key=len, reverse=True))
        + r")\.?,?\s+(20\d{2})\b",
        re.IGNORECASE,
    ),
)

AMOUNT_RE = re.compile(
    r"(?<![\w/])"
    r"(?:[A-Z]{3}\s*)?"
    r"[\+\-]?"
    r"\(?\s*[$\u20ac\u00a3\u00a5]?\s*"
    r"\d[\d\s,.]*"
    r"\)?"
    r"(?:\s*(?:CR|DR|Cr|Dr))?"
    r"(?:\s*[A-Z]{3})?"
)

ACCOUNT_CURRENCY_PATTERNS = (
    re.compile(r"\bmovimientos\s+de\s+cuenta\s+en\s+([A-Z]{3})\b", re.IGNORECASE),
    re.compile(r"\baccount\s+(?:currency|in)\s*:?\s*([A-Z]{3})\b", re.IGNORECASE),
    re.compile(r"\bcurrency\s*:?\s*([A-Z]{3})\b", re.IGNORECASE),
    re.compile(r"\bmoneda\s*:?\s*([A-Z]{3})\b", re.IGNORECASE),
    re.compile(r"\bcuenta\s+en\s+([A-Z]{3})\b", re.IGNORECASE),
)


@dataclass
class PageText:
    file: Path
    page_number: int
    text: str


@dataclass
class AmountCandidate:
    value: Decimal
    token: str
    start: int
    end: int
    kind: str


def normalize_text(value: str) -> str:
    replacements = {
        "\u00e1": "a",
        "\u00e9": "e",
        "\u00ed": "i",
        "\u00f3": "o",
        "\u00fa": "u",
        "\u00f1": "n",
        "\u00fc": "u",
        "\u00c1": "a",
        "\u00c9": "e",
        "\u00cd": "i",
        "\u00d3": "o",
        "\u00da": "u",
        "\u00d1": "n",
        "\u00dc": "u",
        "\u00e0": "a",
        "\u00e8": "e",
        "\u00ec": "i",
        "\u00f2": "o",
        "\u00f9": "u",
        "\u00e7": "c",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return re.sub(r"\s+", " ", value).strip().lower()


def compact_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def text_contains_label(haystack: str, label: str) -> bool:
    normalized_label = normalize_text(label)
    normalized_haystack = normalize_text(haystack)
    if normalized_label and normalized_label in normalized_haystack:
        return True
    compact_label = compact_identifier(label)
    return bool(compact_label and compact_label in compact_identifier(haystack))


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_currency_code(value: str | None, label: str = "currency") -> str | None:
    if not value:
        return None
    code = value.strip().upper()
    if code not in CURRENCY_CODES:
        raise SystemExit(f"Unsupported {label}: {value}. Add it to CURRENCY_CODES before relying on it.")
    return code


def load_pdf_text(path: Path) -> list[PageText]:
    if pdfplumber is None:
        raise SystemExit("pdfplumber is required. Run with a Python environment that has pdfplumber installed.")
    if not path.exists():
        raise SystemExit(f"PDF not found: {path}")
    pages: list[PageText] = []
    try:
        with pdfplumber.open(path) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                pages.append(PageText(path, index, text))
    except Exception as exc:  # noqa: BLE001 - user-facing CLI error.
        raise SystemExit(f"Could not read PDF {path}: {exc}") from exc
    total_chars = sum(len(p.text.strip()) for p in pages)
    if total_chars < MIN_TEXT_CHARS:
        raise SystemExit(
            f"{path} appears to have little machine-readable text. "
            "v1 supports text PDFs only; ask for a text PDF, CSV, or pasted rows."
        )
    return pages


def make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    dates: list[date] = []
    for match in DATE_PATTERNS[0].finditer(text):
        maybe = make_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if maybe:
            dates.append(maybe)
    for match in DATE_PATTERNS[1].finditer(text):
        first = int(match.group(1))
        second = int(match.group(2))
        year = int(match.group(3))
        if first > 12:
            month, day = second, first
        elif second > 12:
            month, day = first, second
        else:
            month, day = first, second
        maybe = make_date(year, month, day)
        if maybe:
            dates.append(maybe)
    for pattern in MONTH_DATE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            if groups[0].lower().strip(".") in MONTHS:
                month = MONTHS[groups[0].lower().strip(".")]
                day = int(groups[1])
                year = int(groups[2])
            else:
                day = int(groups[0])
                month = MONTHS[groups[1].lower().strip(".")]
                year = int(groups[2])
            maybe = make_date(year, month, day)
            if maybe:
                dates.append(maybe)
    return dates


def strip_dates(text: str) -> str:
    cleaned = text
    for pattern in DATE_PATTERNS + MONTH_DATE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return cleaned


TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def strip_dates_and_times(text: str) -> str:
    return TIME_RE.sub(" ", strip_dates(text))


def parse_amount_token(token: str) -> Decimal | None:
    original = token
    token = token.strip()
    if not token or not re.search(r"\d", token):
        return None
    negative = False
    if "(" in token and ")" in token:
        negative = True
    if re.search(r"\bDR\b", token, re.IGNORECASE):
        negative = True
    token = re.sub(r"\b(?:CR|DR)\b", "", token, flags=re.IGNORECASE)
    for code in CURRENCY_CODES:
        token = re.sub(rf"\b{code}\b", "", token, flags=re.IGNORECASE)
    token = token.replace("$", "").replace("\u20ac", "").replace("\u00a3", "").replace("\u00a5", "")
    token = token.replace("(", "").replace(")", "").replace("+", "").strip()
    if token.startswith("-"):
        negative = True
        token = token[1:]
    token = token.replace(" ", "")
    if not token:
        return None
    comma = token.rfind(",")
    dot = token.rfind(".")
    if comma != -1 and dot != -1:
        decimal_sep = "," if comma > dot else "."
        thousands_sep = "." if decimal_sep == "," else ","
        token = token.replace(thousands_sep, "")
        token = token.replace(decimal_sep, ".")
    elif comma != -1:
        tail = token.split(",")[-1]
        if len(tail) in (2, 3):
            token = token.replace(".", "")
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
    elif dot != -1:
        tail = token.split(".")[-1]
        if len(tail) not in (2, 3):
            token = token.replace(".", "")
    token = re.sub(r"[^0-9.]", "", token)
    if token.count(".") > 1:
        pieces = token.split(".")
        token = "".join(pieces[:-1]) + "." + pieces[-1]
    try:
        value = Decimal(token)
    except InvalidOperation:
        return None
    if negative:
        value = -value
    if abs(value) >= Decimal("1000000000000"):
        return None
    if original.strip().isdigit() and len(original.strip()) == 4 and original.strip().startswith("20"):
        return None
    return value


def explicit_currency_codes(text: str) -> list[str]:
    return sorted({code for code in CURRENCY_CODES if re.search(rf"\b{code}\b", text, re.IGNORECASE)})


def detect_currency(text: str, account_currency: str | None = None) -> str | None:
    codes = explicit_currency_codes(text)
    if codes:
        if account_currency and account_currency in codes:
            return account_currency
        return codes[0]
    if account_currency and any(symbol in text for symbol in SYMBOL_TO_CODE):
        return account_currency
    for code in sorted(CURRENCY_CODES):
        if re.search(rf"\b{code}\b", text, re.IGNORECASE):
            return code
    for symbol, code in SYMBOL_TO_CODE.items():
        if symbol in text:
            return code
    return None


def currencies_in_text(text: str) -> list[str]:
    found: set[str] = set()
    found.update(explicit_currency_codes(text))
    for symbol, code in SYMBOL_TO_CODE.items():
        if symbol in text:
            found.add(code)
    return sorted(found)


def detect_account_currency(lines: Iterable[str]) -> tuple[str | None, str, list[str]]:
    evidence_by_currency: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        clean = re.sub(r"\s+", " ", line).strip()
        for pattern in ACCOUNT_CURRENCY_PATTERNS:
            match = pattern.search(clean)
            if not match:
                continue
            code = normalize_currency_code(match.group(1), "account currency")
            if code:
                evidence_by_currency[code].append(clean)
    if not evidence_by_currency:
        return None, "not-detected", []
    if len(evidence_by_currency) == 1:
        code = next(iter(evidence_by_currency))
        return code, "statement-text", sorted(set(evidence_by_currency[code]))
    evidence = []
    for code, lines_for_code in sorted(evidence_by_currency.items()):
        evidence.extend(f"{code}: {line}" for line in sorted(set(lines_for_code)))
    return None, "mixed-statement-text", evidence


def statement_title_candidates(lines: Iterable[str]) -> list[str]:
    titles: set[str] = set()
    for line in lines:
        clean = re.sub(r"\s+", " ", line).strip()
        norm = normalize_text(clean)
        if any(term in norm for term in ("movimientos de cuenta", "account statement", "bank statement", "statement of account")):
            titles.add(clean)
    return sorted(titles)


def detect_periods(lines: Iterable[str]) -> list[str]:
    periods: list[str] = []
    for line in lines:
        norm = normalize_text(line)
        if any(term in norm for term in PERIOD_TERMS):
            dates = find_dates(line)
            if len(dates) >= 2:
                periods.append(f"{dates[0].isoformat()} to {dates[1].isoformat()}")
            elif len(dates) == 1:
                periods.append(dates[0].strftime("%Y-%m"))
    return sorted(set(periods))


def ignored_numeric_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in DATE_PATTERNS + MONTH_DATE_PATTERNS + (TIME_RE,):
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def overlaps_any(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and end > other_start for other_start, other_end in spans)


def token_has_currency_marker(token: str) -> bool:
    if any(symbol in token for symbol in SYMBOL_TO_CODE):
        return True
    if re.search(r"\b(?:CR|DR)\b", token, re.IGNORECASE):
        return True
    return any(re.search(rf"\b{code}\b", token, re.IGNORECASE) for code in CURRENCY_CODES)


def token_has_decimal_cents(token: str) -> bool:
    cleaned = token
    for code in CURRENCY_CODES:
        cleaned = re.sub(rf"\b{code}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:CR|DR)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.translate(str.maketrans("", "", "$\u20ac\u00a3\u00a5()+-"))
    cleaned = cleaned.replace(" ", "").strip()
    return bool(re.search(r"\d[0-9,.]*[,.]\d{2}$", cleaned))


def token_looks_like_identifier(token: str) -> bool:
    if token_has_currency_marker(token) or token_has_decimal_cents(token):
        return False
    digits = re.sub(r"\D", "", token)
    return len(digits) >= 6


def line_amount_candidates(line: str) -> list[AmountCandidate]:
    ignored_spans = ignored_numeric_spans(line)
    marked_amounts: list[AmountCandidate] = []
    decimal_amounts: list[AmountCandidate] = []
    plain_amounts: list[AmountCandidate] = []
    for match in AMOUNT_RE.finditer(line):
        if overlaps_any(match.span(), ignored_spans):
            continue
        token = match.group(0)
        value = parse_amount_token(token)
        if value is not None:
            candidate = AmountCandidate(value, token, match.start(), match.end(), "plain")
            if token_has_currency_marker(token):
                candidate.kind = "marked"
                marked_amounts.append(candidate)
            elif token_has_decimal_cents(token):
                candidate.kind = "decimal"
                decimal_amounts.append(candidate)
            elif not token_looks_like_identifier(token):
                plain_amounts.append(candidate)
    if marked_amounts:
        return marked_amounts
    if decimal_amounts:
        return decimal_amounts
    return plain_amounts


def line_amounts(line: str) -> list[Decimal]:
    return [candidate.value for candidate in line_amount_candidates(line)]


def interest_term_anchor(line: str) -> int:
    lowered = line.lower()
    matches: list[tuple[int, int]] = []
    for term in POSITIVE_INTEREST_TERMS + GENERIC_INTEREST_TERMS:
        index = lowered.find(term.lower())
        if index != -1:
            matches.append((index, index + len(term)))
    if not matches:
        return 0
    return min(matches, key=lambda item: (item[0], -item[1]))[1]


def select_interest_amount(line: str, candidates: list[AmountCandidate]) -> tuple[AmountCandidate, str]:
    anchor = interest_term_anchor(line)
    after_interest_term = [candidate for candidate in candidates if candidate.start >= anchor]
    if after_interest_term:
        return after_interest_term[0], "selected the first monetary value after the interest term"
    return candidates[0], "selected the first monetary value on the line"


def is_interest_line(line: str) -> tuple[bool, str]:
    norm = normalize_text(line)
    has_positive = any(term in norm for term in POSITIVE_INTEREST_TERMS)
    has_generic = any(term in norm for term in GENERIC_INTEREST_TERMS)
    has_exclude = any(term in norm for term in EXCLUDE_TERMS)
    if has_exclude:
        return False, "excluded because the line appears to be a fee, tax, withholding, debit interest, or interest expense"
    if has_positive:
        return True, "positive interest term"
    if has_generic:
        return True, "generic interest term; review confidence"
    return False, ""


def extract_rows_from_pages(
    pages: list[PageText],
    institution: str,
    tax_year: int,
    account_currency_override: str | None = None,
) -> tuple[list[dict], list[dict], list[str], dict]:
    rows: list[dict] = []
    excluded: list[dict] = []
    warnings: list[str] = []
    all_text = "\n".join(p.text for p in pages)
    all_lines = [line.strip() for page in pages for line in page.text.splitlines() if line.strip()]
    currencies = currencies_in_text(all_text)
    detected_account_currency, account_currency_basis, account_currency_evidence = detect_account_currency(all_lines)
    account_currency = account_currency_override or detected_account_currency
    if account_currency_override:
        account_currency_basis = "user-override"
        account_currency_evidence = [f"Account currency supplied by user/agent: {account_currency_override}"]
    default_currency = account_currency or (currencies[0] if len(currencies) == 1 else None)
    periods = detect_periods(all_lines)
    titles = statement_title_candidates(all_lines)
    scope_years = sorted({d.year for p in periods for d in find_dates(p)})

    institution_label_basis = "pdf-text"
    if not text_contains_label(all_text, institution):
        file_context = "\n".join(str(page.file) for page in pages)
        if text_contains_label(file_context, institution):
            institution_label_basis = "file-path-fallback"
            warnings.append(
                f"Institution label '{institution}' was not found in the PDF text layer for "
                f"{pages[0].file.name}, but it appears in the file path. Verify the statements "
                "are all from one institution."
            )
        else:
            raise SystemExit(
                f"{pages[0].file} does not appear to contain institution name '{institution}'. "
                "Use one bank per run and pass the exact institution name visible on the statements."
            )

    for page in pages:
        for line in page.text.splitlines():
            line = line.strip()
            if not line:
                continue
            is_interest, reason = is_interest_line(line)
            norm = normalize_text(line)
            if not is_interest and any(term in norm for term in GENERIC_INTEREST_TERMS):
                excluded.append(
                    {
                        "source_file": str(page.file),
                        "page": page.page_number,
                        "evidence_text": line,
                        "reason": reason or "interest-like line excluded",
                    }
                )
                continue
            if not is_interest:
                continue
            dates = find_dates(line)
            amount_candidates = line_amount_candidates(line)
            positives = [candidate for candidate in amount_candidates if candidate.value > 0]
            if not positives:
                excluded.append(
                    {
                        "source_file": str(page.file),
                        "page": page.page_number,
                        "evidence_text": line,
                        "reason": "interest-like line has no positive amount",
                    }
                )
                continue
            selected_candidate, amount_selection_note = select_interest_amount(line, positives)
            selected = selected_candidate.value
            selected_date = dates[0] if dates else None
            if selected_date and selected_date.year != tax_year:
                excluded.append(
                    {
                        "source_file": str(page.file),
                        "page": page.page_number,
                        "evidence_text": line,
                        "reason": f"interest-like line date {selected_date.isoformat()} is outside tax year {tax_year}",
                    }
                )
                continue
            line_currency = detect_currency(line, account_currency=account_currency) or default_currency or "UNKNOWN"
            confidence = "high" if reason == "positive interest term" and selected_date else "medium"
            notes: list[str] = [reason]
            if not selected_date:
                notes.append("missing transaction date; review statement context before relying on this row")
                confidence = "low"
            if len(positives) > 1:
                notes.append(f"multiple positive monetary values found; {amount_selection_note}")
                confidence = "medium" if confidence == "high" else confidence
            if account_currency and line_currency == account_currency and not explicit_currency_codes(line):
                notes.append(f"currency inferred from statement account currency {account_currency}")
            if line_currency == "UNKNOWN":
                notes.append("currency not detected")
                confidence = "low"
            row = {
                "date": selected_date.isoformat() if selected_date else "",
                "statement_period": selected_date.strftime("%Y-%m") if selected_date else (periods[0] if len(periods) == 1 else ""),
                "description": clean_description(line),
                "amount_foreign": money(selected),
                "currency": line_currency,
                "source_file": str(page.file),
                "page": page.page_number,
                "evidence_text": line,
                "confidence": confidence,
                "notes": "; ".join(notes),
            }
            rows.append(row)

    row_years = sorted({int(r["date"][:4]) for r in rows if r.get("date")})
    outside_years = [year for year in set(scope_years + row_years) if year != tax_year]
    if outside_years:
        raise SystemExit(
            f"Detected statement or interest years outside tax year {tax_year}: {outside_years}. "
            "Split statements by tax year and run again."
        )
    if len(currencies) > 1 and account_currency:
        warnings.append(
            f"Multiple currency markers were detected in the statement text; counted rows default to account currency "
            f"{account_currency} unless a row has an explicit currency code."
        )
    elif len(currencies) > 1:
        warnings.append(
            "Multiple currency markers were detected. Verify all counted interest rows use one currency before reporting."
        )
    meta = {
        "periods": periods,
        "statement_titles": titles,
        "currency_candidates": currencies,
        "account_currency": account_currency or "",
        "account_currency_basis": account_currency_basis,
        "account_currency_evidence": account_currency_evidence,
        "institution_label_basis": institution_label_basis,
        "character_count": len(all_text),
        "page_count": len(pages),
    }
    return rows, excluded, warnings, meta


def clean_description(line: str) -> str:
    text = re.sub(r"\s+", " ", line).strip()
    return text[:220]


def foreign_totals(rows: list[dict]) -> dict[str, str]:
    totals: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        currency = row.get("currency") or "UNKNOWN"
        try:
            totals[currency] += Decimal(str(row.get("amount_foreign", "0")))
        except InvalidOperation:
            continue
    return {currency: money(amount) for currency, amount in sorted(totals.items())}


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "date",
        "statement_period",
        "description",
        "amount_foreign",
        "currency",
        "source_file",
        "page",
        "evidence_text",
        "confidence",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def command_extract(args: argparse.Namespace) -> int:
    tax_year = int(args.tax_year)
    account_currency_override = normalize_currency_code(args.account_currency, "account currency")
    pdf_paths = [Path(p) for p in args.pdf]
    all_rows: list[dict] = []
    all_excluded: list[dict] = []
    all_warnings: list[str] = []
    statement_files: list[dict] = []
    for pdf_path in pdf_paths:
        pages = load_pdf_text(pdf_path)
        rows, excluded, warnings, meta = extract_rows_from_pages(pages, args.institution, tax_year, account_currency_override)
        all_rows.extend(rows)
        all_excluded.extend(excluded)
        all_warnings.extend(warnings)
        statement_files.append(
            {
                "file": str(pdf_path),
                "page_count": meta["page_count"],
                "character_count": meta["character_count"],
                "detected_periods": meta["periods"],
                "statement_titles": meta["statement_titles"],
                "currency_candidates": meta["currency_candidates"],
                "account_currency": meta["account_currency"],
                "account_currency_basis": meta["account_currency_basis"],
                "account_currency_evidence": meta["account_currency_evidence"],
                "institution_label_basis": meta["institution_label_basis"],
            }
        )
    currencies = sorted({row.get("currency") or "UNKNOWN" for row in all_rows})
    if len(currencies) > 1:
        all_warnings.append("Counted interest rows include more than one currency. Split or review before reporting.")
    account_currencies = sorted({item["account_currency"] for item in statement_files if item.get("account_currency")})
    title_values = sorted({title for item in statement_files for title in item.get("statement_titles", [])})
    period_values = sorted({period for item in statement_files for period in item.get("detected_periods", [])})
    institution_label_sources = sorted({item.get("institution_label_basis", "") for item in statement_files if item.get("institution_label_basis")})
    analysis = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "institution": args.institution,
        "tax_year": tax_year,
        "institution_profile": {
            "institution": args.institution,
            "tax_year": tax_year,
            "account_currency": account_currencies[0] if len(account_currencies) == 1 else ("MIXED" if account_currencies else ""),
            "statement_titles": title_values,
            "detected_periods": period_values,
            "institution_label_sources": institution_label_sources,
            "statement_file_count": len(statement_files),
        },
        "scope": {
            "one_institution_only": True,
            "one_tax_year_only": True,
            "text_pdfs_only": True,
        },
        "statement_files": statement_files,
        "rows": sorted(all_rows, key=lambda row: (row.get("date") or "9999-99-99", row.get("source_file", ""))),
        "excluded_candidates": all_excluded,
        "warnings": sorted(set(all_warnings)),
        "totals": {
            "row_count": len(all_rows),
            "foreign_total_by_currency": foreign_totals(all_rows),
        },
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    csv_path = Path(args.csv) if args.csv else out_path.with_name("interest-items.csv")
    write_csv(analysis["rows"], csv_path)
    print(f"Wrote analysis: {out_path}")
    print(f"Wrote CSV: {csv_path}")
    if analysis["warnings"]:
        print("Warnings:")
        for warning in analysis["warnings"]:
            print(f"- {warning}")
    return 0


def parse_decimal(value: str, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SystemExit(f"Invalid {label}: {value}") from exc
    if parsed <= 0:
        raise SystemExit(f"{label} must be greater than zero.")
    return parsed


def escape(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def display_file_name(value: object) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    return Path(text).name or text


def make_styles():
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    styles["Normal"].leading = 12
    styles.add(
        ParagraphStyle(
            name="HeroTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            textColor=colors.white,
            fontSize=22,
            leading=26,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeroSubtitle",
            parent=styles["Normal"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#E6F3F3"),
            fontSize=9,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionTitle",
            parent=styles["Heading2"],
            textColor=colors.HexColor("#0F3D3E"),
            fontSize=12,
            leading=15,
            spaceBefore=8,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["Normal"],
            fontSize=7,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Cell",
            parent=styles["Normal"],
            fontSize=7,
            leading=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TableHeader",
            parent=styles["Cell"],
            textColor=colors.white,
            fontName="Helvetica-Bold",
        )
    )
    styles.add(
        ParagraphStyle(
            name="KpiCard",
            parent=styles["Normal"],
            alignment=TA_CENTER,
            fontSize=8,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BoxText",
            parent=styles["Normal"],
            fontSize=8,
            leading=11,
        )
    )
    return styles


def as_paragraphs(rows: list[list[object]], styles, header: bool = True) -> list[list[object]]:
    converted: list[list[object]] = []
    for index, row in enumerate(rows):
        style = styles["TableHeader"] if header and index == 0 else styles["Cell"]
        converted.append([Paragraph(escape(cell), style) for cell in row])
    return converted


def add_table(story: list, rows: list[list[object]], widths: list[float], styles, header: bool = True) -> None:
    table = Table(as_paragraphs(rows, styles, header=header), colWidths=widths, repeatRows=1 if header else 0)
    table_style = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D6E4E5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        table_style.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3D3E")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
            ]
        )
    table.setStyle(TableStyle(table_style))
    story.append(table)
    story.append(Spacer(1, 0.16 * inch))


def add_hero(story: list, styles, institution: str, tax_year: object) -> None:
    title = "Foreign Bank Interest Support Packet"
    subtitle = f"{institution or 'Institution not recorded'} - {tax_year or 'tax year not recorded'}"
    hero = Table(
        [
            [Paragraph(escape(title), styles["HeroTitle"])],
            [
                Paragraph(
                    escape(f"{subtitle}. For U.S. tax return support only; not an official IRS form."),
                    styles["HeroSubtitle"],
                )
            ],
        ],
        colWidths=[7.4 * inch],
    )
    hero.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0F3D3E")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#0A2C2D")),
                ("TOPPADDING", (0, 0), (-1, 0), 13),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 13),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(hero)
    story.append(Spacer(1, 0.18 * inch))


def add_kpi_cards(story: list, items: list[tuple[str, str]], styles) -> None:
    cells = []
    for label, value in items:
        cells.append(
            Paragraph(
                f'<font color="#667085">{escape(label)}</font><br/><font size="15" color="#0F3D3E"><b>{escape(value)}</b></font>',
                styles["KpiCard"],
            )
        )
    card_width = 7.4 * inch / max(len(items), 1)
    table = Table([cells], colWidths=[card_width for _item in items])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6E4E5")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D6E4E5")),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.18 * inch))


def add_note_box(story: list, title: str, lines: list[str], styles, tone: str = "info") -> None:
    palette = {
        "warning": ("#FFF8E6", "#F4C95D", "#7A4F00"),
        "info": ("#F0F7F7", "#B8D8D8", "#0F3D3E"),
    }
    background, border, title_color = palette.get(tone, palette["info"])
    body = f'<font color="{title_color}"><b>{escape(title)}</b></font>'
    if lines:
        body = f"{body}<br/>" + "<br/>".join(escape(line) for line in lines)
    box = Table([[Paragraph(body, styles["BoxText"])]], colWidths=[7.4 * inch])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(background)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(border)),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(box)
    story.append(Spacer(1, 0.14 * inch))


def page_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D6E4E5"))
    canvas.setLineWidth(0.25)
    canvas.line(0.55 * inch, 0.5 * inch, 7.95 * inch, 0.5 * inch)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(0.55 * inch, 0.35 * inch, "Generated support packet - not an official IRS form.")
    canvas.drawRightString(7.95 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def load_fx_rates_json(path: Path, fallback_method: str | None, fallback_source: str, fallback_direction: str) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"FX rates JSON not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FX rates JSON is invalid: {path} ({exc.msg} at line {exc.lineno}, column {exc.colno})") from None

    default_method = fallback_method or "user-rate"
    default_source = fallback_source
    default_direction = fallback_direction
    rates_payload = payload
    if isinstance(payload, dict) and "rates" in payload:
        default_method = payload.get("method") or default_method
        default_source = payload.get("source") or default_source
        default_direction = payload.get("rate_direction") or default_direction
        rates_payload = payload.get("rates")

    records: list[tuple[str, object]]
    if isinstance(rates_payload, dict):
        records = list(rates_payload.items())
    elif isinstance(rates_payload, list):
        records = []
        for item in rates_payload:
            if not isinstance(item, dict) or "date" not in item:
                raise SystemExit("FX rates JSON list entries must be objects with date and rate fields.")
            records.append((str(item["date"]), item))
    else:
        raise SystemExit("FX rates JSON must be a date-to-rate object, a rates object, or a list of date/rate objects.")

    loaded: dict[str, dict[str, object]] = {}
    for date_key, raw_value in records:
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date_key):
            raise SystemExit(f"FX rate key must be an ISO date, got: {date_key}")
        if isinstance(raw_value, dict):
            if "rate" not in raw_value:
                raise SystemExit(f"FX rate entry for {date_key} must include a rate field.")
            rate_value = raw_value["rate"]
            method = raw_value.get("method") or default_method
            source = raw_value.get("source") or default_source
            direction = raw_value.get("rate_direction") or default_direction
        else:
            rate_value = raw_value
            method = default_method
            source = default_source
            direction = default_direction
        if direction not in {"foreign-per-usd", "usd-per-foreign"}:
            raise SystemExit(f"Unsupported rate_direction for {date_key}: {direction}")
        loaded[date_key] = {
            "rate": parse_decimal(str(rate_value), f"FX rate for {date_key}"),
            "method": str(method),
            "source": str(source),
            "rate_direction": str(direction),
        }
    return loaded


def reject_self_calculated_posted_average(args: argparse.Namespace) -> None:
    if args.fx_method != "posted-yearly-average":
        return
    evidence = normalize_text(" ".join([getattr(args, "fx_source", "") or "", getattr(args, "fx_confirmation_note", "") or ""]))
    matched = [term for term in SELF_CALCULATED_AVERAGE_TERMS if term in evidence]
    if matched:
        raise SystemExit(
            "Do not use a self-calculated daily-series average as --fx-method posted-yearly-average. "
            "Use an official/published annual average from a bank, tax authority, central bank, or FX provider, "
            "or ask the user/preparer for a custom rate and use --fx-method user-rate."
        )


def load_fx_workpaper(path: Path, expected_currency: str, expected_year: int | str) -> dict[str, object]:
    try:
        workpaper = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"FX workpaper JSON not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FX workpaper JSON is invalid: {path} ({exc.msg} at line {exc.lineno}, column {exc.colno})") from None
    if not isinstance(workpaper, dict):
        raise SystemExit(f"FX workpaper JSON must be an object: {path}")
    if workpaper.get("skill") != "get-yearly-fx-rate":
        raise SystemExit("FX workpaper must be generated by the get-yearly-fx-rate skill.")

    currency = str(workpaper.get("currency", "")).upper()
    if currency != expected_currency:
        raise SystemExit(f"FX workpaper currency {currency or 'UNKNOWN'} does not match statement currency {expected_currency}.")
    year = str(workpaper.get("year", ""))
    if year != str(expected_year):
        raise SystemExit(f"FX workpaper year {year or 'UNKNOWN'} does not match statement tax year {expected_year}.")

    source = workpaper.get("source")
    proof = workpaper.get("proof")
    if not isinstance(source, dict) or not isinstance(proof, dict):
        raise SystemExit("FX workpaper is missing source/proof metadata.")
    if not proof.get("workpaper_json") or not proof.get("workpaper_pdf"):
        raise SystemExit("FX workpaper must include workpaper_json and workpaper_pdf proof paths.")
    if not proof.get("saved_files"):
        raise SystemExit("FX workpaper must include at least one saved source proof file.")

    # get-yearly-fx-rate normalizes all accepted workpapers to this direction.
    rate = parse_decimal(str(workpaper.get("foreign_per_usd", "")), "FX workpaper foreign_per_usd")
    normalized = dict(workpaper)
    normalized["_fx_rate_decimal"] = rate
    normalized["_fx_source_label"] = fx_workpaper_source_label(workpaper)
    return normalized


def fx_workpaper_source_label(workpaper: dict[str, object]) -> str:
    source = workpaper.get("source", {})
    if not isinstance(source, dict):
        return "get-yearly-fx-rate workpaper"
    title = source.get("title") or "get-yearly-fx-rate workpaper"
    url = source.get("url") or ""
    retrieved = source.get("retrieved") or ""
    note = source.get("note") or ""
    parts = [str(title)]
    if url:
        parts.append(str(url))
    if retrieved:
        parts.append(f"retrieved {retrieved}")
    if note:
        parts.append(str(note))
    return "; ".join(parts)


def fx_workpaper_artifacts(workpaper: dict[str, object]) -> list[dict[str, str]]:
    proof = workpaper.get("proof", {})
    if not isinstance(proof, dict):
        return []
    artifacts: list[dict[str, str]] = []
    for key, label in (
        ("workpaper_pdf", "FX workpaper PDF"),
        ("workpaper_md", "FX workpaper Markdown"),
        ("workpaper_json", "FX workpaper JSON"),
    ):
        value = proof.get(key)
        if value:
            artifacts.append({"label": label, "path": str(value), "sha256": str(proof.get(f"{key}_sha256", ""))})
    saved_files = proof.get("saved_files", [])
    if isinstance(saved_files, list):
        for index, item in enumerate(saved_files, start=1):
            if isinstance(item, dict) and item.get("path"):
                artifacts.append(
                    {
                        "label": f"FX source proof {index}",
                        "path": str(item.get("path")),
                        "sha256": str(item.get("sha256", "")),
                    }
                )
    return artifacts


def configure_fx_from_workpaper(args: argparse.Namespace, analysis: dict, currency: str) -> dict[str, object] | None:
    workpaper_path = getattr(args, "fx_workpaper_json", None)
    if not workpaper_path:
        return None
    workpaper = load_fx_workpaper(Path(workpaper_path), currency, analysis.get("tax_year", ""))
    args.fx_method = "get-yearly-fx-rate"
    args.fx_rate = str(workpaper["foreign_per_usd"])
    args.fx_source = str(workpaper["_fx_source_label"])
    args.rate_direction = "foreign-per-usd"
    return workpaper


def published_fx_requires_workpaper(args: argparse.Namespace) -> bool:
    return (
        getattr(args, "fx_method", None) in {"irs-yearly-average", "posted-yearly-average", "get-yearly-fx-rate"}
        and not getattr(args, "fx_workpaper_json", None)
    )


def dependency_required_message(currency: str, tax_year: object) -> str:
    return (
        f"Published yearly-average FX for {currency}/{tax_year} must come from the get-yearly-fx-rate skill. "
        "Run get-yearly-fx-rate and pass its workpaper.json with --fx-workpaper-json, or use "
        "--fx-method user-rate with a user/preparer supplied custom rate and source."
    )


def build_fx_confirmation_prompt(args: argparse.Namespace, analysis: dict, currency: str, foreign_total: Decimal) -> str:
    institution = analysis.get("institution", "the institution")
    tax_year = analysis.get("tax_year", "the tax year")
    method = FX_METHOD_LABELS.get(args.fx_method or "", args.fx_method or "not supplied")
    lines = [
        f"FX confirmation needed before I generate the PDF for {institution} {tax_year}.",
        "",
    ]
    if args.fx_rate:
        fx_rate = parse_decimal(args.fx_rate, "--fx-rate")
        if args.rate_direction == "foreign-per-usd":
            usd_total = foreign_total / fx_rate
            rate_line = f"{money(fx_rate)} {currency} per 1 USD"
        else:
            usd_total = foreign_total * fx_rate
            rate_line = f"{money(fx_rate)} USD per 1 {currency}"
        lines.extend(
            [
                "I found/propose this FX rate:",
                f"Rate: {rate_line}",
                f"Source: {args.fx_source or 'No source supplied'}",
                f"Method: {method}",
                f"Source-currency total: {money(foreign_total)} {currency}",
                f"USD total using this rate: USD {money(usd_total)}",
            ]
        )
        workpaper_path = getattr(args, "fx_workpaper_json", None)
        if workpaper_path:
            try:
                workpaper = load_fx_workpaper(Path(workpaper_path), currency, tax_year)
                artifact_lines = [f"{item['label']}: {item['path']}" for item in fx_workpaper_artifacts(workpaper)]
            except SystemExit:
                artifact_lines = []
            if artifact_lines:
                lines.extend(["", "FX proof documents from get-yearly-fx-rate:", *artifact_lines])
        lines.extend(
            [
                "",
                'Reply "confirm" to use this rate, or send a custom rate/source instead.',
                'Custom format: rate: <number>, direction: foreign-per-usd or usd-per-foreign, source: <source>, method: user-rate.',
            ]
        )
    else:
        lines.extend(
            [
                f"The extracted interest total is {money(foreign_total)} {currency}, so a USD conversion is required.",
                "No get-yearly-fx-rate workpaper was supplied to the report command.",
                "",
                "Run get-yearly-fx-rate for this currency/year and pass its workpaper.json via --fx-workpaper-json, or send a custom user/preparer rate/source.",
                'Custom format: rate: <number>, direction: foreign-per-usd or usd-per-foreign, source: <source>, method: user-rate.',
            ]
        )
    return "\n".join(lines)


def load_analysis_payload(input_path: Path) -> dict:
    try:
        analysis = json.loads(input_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Input analysis JSON not found: {input_path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Input analysis JSON is invalid: {input_path} ({exc.msg} at line {exc.lineno}, column {exc.colno})") from None
    if not isinstance(analysis, dict):
        raise SystemExit(f"Input analysis JSON must be an object: {input_path}")
    return analysis


def report_rows_and_total(analysis: dict, input_path: Path) -> tuple[list[dict], dict[str, str], str, Decimal]:
    rows = analysis.get("rows", [])
    if not isinstance(rows, list):
        raise SystemExit(f"Input analysis JSON has invalid 'rows'; expected a list: {input_path}")
    if any(not isinstance(row, dict) for row in rows):
        raise SystemExit(f"Input analysis JSON has invalid 'rows'; every row must be an object: {input_path}")
    totals = foreign_totals(rows)
    currencies = [currency for currency in totals if currency != "UNKNOWN"]
    unknown_total = totals.get("UNKNOWN")
    if unknown_total and Decimal(unknown_total) != 0:
        raise SystemExit("Cannot generate USD report because at least one counted row has UNKNOWN currency.")
    if len(currencies) > 1:
        raise SystemExit(f"Report supports one currency per run. Found currencies: {', '.join(currencies)}")
    currency = currencies[0] if currencies else ""
    foreign_total = Decimal(totals[currency]) if currency else Decimal("0")
    return rows, totals, currency, foreign_total


def command_fx_prompt(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    analysis = load_analysis_payload(input_path)
    _rows, _totals, currency, foreign_total = report_rows_and_total(analysis, input_path)
    if foreign_total == 0:
        print("No interest rows were counted, so no FX confirmation is needed.")
        return 0
    if currency == "USD":
        print("Counted interest rows are already denominated in USD, so no FX confirmation is needed.")
        return 0
    configure_fx_from_workpaper(args, analysis, currency)
    if published_fx_requires_workpaper(args):
        print(dependency_required_message(currency, analysis.get("tax_year", "")))
        return 2
    if args.fx_method == "posted-yearly-average":
        reject_self_calculated_posted_average(args)
    print(build_fx_confirmation_prompt(args, analysis, currency, foreign_total))
    return 0


def command_report(args: argparse.Namespace) -> int:
    if colors is None:
        raise SystemExit("reportlab is required. Run with a Python environment that has reportlab installed.")
    if args.fx_workpaper_json and args.fx_rates_json:
        raise SystemExit("Use either --fx-workpaper-json for yearly-average FX or --fx-rates-json for explicit spot rates, not both.")
    input_path = Path(args.input)
    analysis = load_analysis_payload(input_path)
    rows, _totals, currency, foreign_total = report_rows_and_total(analysis, input_path)

    fx_rate = Decimal("0")
    usd_total = Decimal("0")
    row_usd_values: list[Decimal] = []
    row_fx_labels: list[str] = []
    fx_note = "No FX conversion was needed because no interest rows were found."
    fx_confirmation = ""
    fx_workpaper: dict[str, object] | None = None
    if foreign_total != 0:
        if currency == "USD":
            usd_total = foreign_total
            row_usd_values = [Decimal(str(row.get("amount_foreign", "0"))) for row in rows]
            row_fx_labels = ["No FX" for _row in rows]
            fx_note = "No FX conversion was needed because the counted interest rows are already denominated in USD."
        else:
            fx_workpaper = configure_fx_from_workpaper(args, analysis, currency)
            if not args.fx_rate_confirmed:
                if published_fx_requires_workpaper(args):
                    print(dependency_required_message(currency, analysis.get("tax_year", "")), file=sys.stderr)
                    return 2
                if args.fx_method == "posted-yearly-average":
                    try:
                        reject_self_calculated_posted_average(args)
                    except SystemExit as exc:
                        print(str(exc), file=sys.stderr)
                        print(
                            "Ask the user/preparer for a published annual average or custom rate/source before generating the PDF.",
                            file=sys.stderr,
                        )
                        return 2
                print(build_fx_confirmation_prompt(args, analysis, currency, foreign_total), file=sys.stderr)
                return 2
            fx_confirmation = args.fx_confirmation_note or "User confirmed the FX rate before report generation."
            row_fx_rates = load_fx_rates_json(Path(args.fx_rates_json), args.fx_method, args.fx_source, args.rate_direction) if args.fx_rates_json else {}
            if row_fx_rates:
                used_methods: set[str] = set()
                used_sources: set[str] = set()
                used_directions: set[str] = set()
                for row in rows:
                    row_date = row.get("date", "")
                    if not row_date:
                        raise SystemExit("Cannot apply row-level FX rates because at least one counted row has no date.")
                    fx_spec = row_fx_rates.get(str(row_date))
                    if not fx_spec:
                        raise SystemExit(f"Missing FX rate for counted row date: {row_date}")
                    amount = Decimal(str(row.get("amount_foreign", "0")))
                    rate = fx_spec["rate"]
                    direction = str(fx_spec["rate_direction"])
                    if direction == "foreign-per-usd":
                        row_usd = amount / rate
                        label = f"{money(rate)} {currency}/USD"
                    else:
                        row_usd = amount * rate
                        label = f"{money(rate)} USD/{currency}"
                    row_usd_values.append(row_usd)
                    row_fx_labels.append(label)
                    used_methods.add(str(fx_spec["method"]))
                    if fx_spec["source"]:
                        used_sources.add(str(fx_spec["source"]))
                    used_directions.add(direction)
                usd_total = sum(row_usd_values, Decimal("0"))
                methods = ", ".join(FX_METHOD_LABELS.get(method, method) for method in sorted(used_methods))
                sources = "; ".join(sorted(used_sources)) or "No source label supplied"
                directions = ", ".join(sorted(used_directions))
                fx_note = f"{methods}; row-level rates matched by interest date; direction: {directions}; source: {sources}."
            else:
                if published_fx_requires_workpaper(args):
                    raise SystemExit(dependency_required_message(currency, analysis.get("tax_year", "")))
                if not args.fx_method:
                    raise SystemExit("--fx-method is required when non-USD interest rows are present.")
                if not args.fx_rate:
                    raise SystemExit("--fx-rate is required when non-USD interest rows are present.")
                if args.fx_method == "user-rate" and not args.fx_source:
                    raise SystemExit("--fx-source is required with --fx-method user-rate.")
                reject_self_calculated_posted_average(args)
                fx_rate = parse_decimal(args.fx_rate, "--fx-rate")
                if args.rate_direction == "foreign-per-usd":
                    usd_total = foreign_total / fx_rate
                    rate_phrase = f"{money(fx_rate)} {currency} per 1 USD"
                else:
                    usd_total = foreign_total * fx_rate
                    rate_phrase = f"{money(fx_rate)} USD per 1 {currency}"
                for row in rows:
                    amount = Decimal(str(row.get("amount_foreign", "0")))
                    row_usd = amount / fx_rate if args.rate_direction == "foreign-per-usd" else amount * fx_rate
                    row_usd_values.append(row_usd)
                    row_fx_labels.append(rate_phrase)
                method = FX_METHOD_LABELS.get(args.fx_method, args.fx_method)
                fx_source = args.fx_source or "No source label supplied"
                fx_note = f"{method}; {rate_phrase}; source: {fx_source}."
            fx_note = f"{fx_note} Confirmation: {fx_confirmation}"

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    styles = make_styles()
    story: list = []

    profile = analysis.get("institution_profile", {})
    account_currency = profile.get("account_currency", "") or currency
    profile_periods = ", ".join(profile.get("detected_periods", [])) or "Not detected"
    profile_titles = ", ".join(profile.get("statement_titles", [])) or "Not detected"
    label_sources = ", ".join(profile.get("institution_label_sources", [])) or "Not recorded"
    source_total = f"{money(foreign_total)} {currency}".strip() if currency else "0.00"
    usd_total_label = f"USD {money(usd_total)}" if foreign_total else "USD 0.00"
    add_hero(story, styles, str(analysis.get("institution", "")), analysis.get("tax_year", ""))
    add_kpi_cards(
        story,
        [
            ("Interest rows", str(len(rows))),
            ("Source total", source_total),
            ("USD support total", usd_total_label),
        ],
        styles,
    )
    story.append(Paragraph("Packet Summary", styles["SectionTitle"]))
    summary_rows = [
        ["Field", "Value"],
        ["Institution", analysis.get("institution", "")],
        ["Tax year", analysis.get("tax_year", "")],
        ["Account currency", account_currency or "Not detected"],
        ["Statement title(s)", profile_titles],
        ["Statement periods detected", profile_periods],
        ["Institution label source", label_sources],
        ["Statement files reviewed", str(len(analysis.get("statement_files", [])))],
        ["Interest rows counted", str(len(rows))],
        ["Source-currency total", source_total],
        ["USD conversion", fx_note],
        ["FX confirmation", fx_confirmation or "Not applicable"],
        ["USD total for reporting support", usd_total_label],
    ]
    if fx_workpaper:
        proof = fx_workpaper.get("proof", {})
        if isinstance(proof, dict) and proof.get("workpaper_pdf"):
            summary_rows.insert(-1, ["FX proof workpaper", Path(str(proof.get("workpaper_pdf"))).name])
    add_table(story, summary_rows, [2.1 * inch, 5.1 * inch], styles)

    story.append(Paragraph("Statements Reviewed", styles["SectionTitle"]))
    file_rows = [["File", "Pages", "Detected periods", "Account currency", "Currency markers"]]
    for item in analysis.get("statement_files", []):
        file_rows.append(
            [
                display_file_name(item.get("file", "")),
                item.get("page_count", ""),
                ", ".join(item.get("detected_periods", [])) or "Not detected",
                item.get("account_currency", "") or "Not detected",
                ", ".join(item.get("currency_candidates", [])) or "Not detected",
            ]
        )
    add_table(story, file_rows, [2.55 * inch, 0.45 * inch, 1.75 * inch, 1.05 * inch, 1.4 * inch], styles)

    story.append(Paragraph("Interest Rows Counted", styles["SectionTitle"]))
    if rows:
        interest_rows = [["Date", "Period", "Description", "Foreign amount", "USD", "Source"]]
        for index, row in enumerate(rows):
            amount = Decimal(str(row.get("amount_foreign", "0")))
            row_usd = row_usd_values[index] if index < len(row_usd_values) else amount
            fx_label = row_fx_labels[index] if index < len(row_fx_labels) else ""
            usd_cell = f"USD {money(row_usd)}"
            if fx_label and fx_label != "No FX":
                usd_cell = f"{usd_cell}\nFX: {fx_label}"
            interest_rows.append(
                [
                    row.get("date", ""),
                    row.get("statement_period", ""),
                    row.get("description", ""),
                    f"{money(amount)} {row.get('currency', '')}".strip(),
                    usd_cell,
                    f"{Path(row.get('source_file', '')).name}, p. {row.get('page', '')}",
                ]
            )
        add_table(story, interest_rows, [0.75 * inch, 0.65 * inch, 2.55 * inch, 1.1 * inch, 0.9 * inch, 1.25 * inch], styles)
    else:
        story.append(Paragraph("No interest rows were counted from the provided machine-readable statements.", styles["Normal"]))
        story.append(Spacer(1, 0.12 * inch))

    excluded = analysis.get("excluded_candidates", [])
    story.append(Paragraph("Ambiguous or Excluded Items", styles["SectionTitle"]))
    if excluded:
        excluded_rows = [["Source", "Evidence", "Reason"]]
        for item in excluded[:80]:
            excluded_rows.append(
                [
                    f"{Path(item.get('source_file', '')).name}, p. {item.get('page', '')}",
                    item.get("evidence_text", ""),
                    item.get("reason", ""),
                ]
            )
        add_table(story, excluded_rows, [1.3 * inch, 3.7 * inch, 2.2 * inch], styles)
    else:
        story.append(Paragraph("No ambiguous or excluded interest-like lines were detected.", styles["Normal"]))
        story.append(Spacer(1, 0.12 * inch))

    warnings = analysis.get("warnings", [])
    if warnings:
        add_note_box(story, "Warnings for preparer review", warnings, styles, tone="warning")

    if fx_workpaper:
        artifacts = fx_workpaper_artifacts(fx_workpaper)
        if artifacts:
            story.append(Paragraph("FX Proof Documents", styles["SectionTitle"]))
            artifact_rows = [["Artifact", "File", "SHA-256"]]
            for item in artifacts:
                artifact_rows.append(
                    [
                        item.get("label", ""),
                        Path(item.get("path", "")).name,
                        item.get("sha256", ""),
                    ]
                )
            add_table(story, artifact_rows, [1.7 * inch, 2.2 * inch, 3.3 * inch], styles)

    story.append(PageBreak())
    story.append(Paragraph("IRS-Oriented Review Notes", styles["SectionTitle"]))
    notes = [
        "Review Schedule B applicability if taxable interest is present, if total taxable interest and ordinary dividends exceed the Schedule B threshold, or if foreign-account questions apply.",
        "Review Form 1040 or 1040-SR taxable interest reporting with the preparer or tax software.",
        "Review FBAR and Form 8938 applicability separately for foreign financial accounts and specified foreign financial assets.",
        "Keep the original statements, this worksheet, the FX source, and any user/preparer adjustments with tax records.",
    ]
    for note in notes:
        story.append(Paragraph(f"- {escape(note)}", styles["Normal"]))
    story.append(Spacer(1, 0.16 * inch))

    story.append(Paragraph("Source Links", styles["SectionTitle"]))
    sources = [
        "Schedule B: https://www.irs.gov/forms-pubs/about-schedule-b-form-1040",
        "Publication 550: https://www.irs.gov/publications/p550",
        "Foreign currency exchange rates: https://www.irs.gov/individuals/international-taxpayers/foreign-currency-and-currency-exchange-rates",
        "Yearly average exchange rates: https://www.irs.gov/individuals/international-taxpayers/yearly-average-currency-exchange-rates",
        "Treasury reporting rates: https://fiscal.treasury.gov/resources/reporting-rates-exchange",
        "Banco de la Republica TRM: https://www.banrep.gov.co/es/estadisticas/trm",
        "FBAR overview: https://www.irs.gov/businesses/small-businesses-self-employed/report-of-foreign-bank-and-financial-accounts-fbar",
        "Form 8938: https://www.irs.gov/forms-pubs/about-form-8938",
    ]
    for source in sources:
        story.append(Paragraph(escape(source), styles["Small"]))

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    print(f"Wrote PDF: {out_path}")
    print(f"USD total: {money(usd_total)}")
    if fx_workpaper:
        for artifact in fx_workpaper_artifacts(fx_workpaper):
            print(f"{artifact['label']}: {artifact['path']}")
    return 0


def command_self_test(args: argparse.Namespace) -> int:
    cases = [
        (
            "date-time-id-balance",
            "2025-01-03 17:46:01 Intereses abonados 15006800 $1.95 $2,054.34",
            Decimal("1.95"),
        ),
        (
            "small-balance-after-interest",
            "2025-01-03 Interest credited $10.00 $5.00",
            Decimal("10.00"),
        ),
        (
            "balance-before-interest",
            "Balance $100.00 Interest credited $1.23",
            Decimal("1.23"),
        ),
        (
            "currency-code-marker",
            "2025-01-03 Interest credited EUR 2.50 EUR 102.50",
            Decimal("2.50"),
        ),
    ]
    failures: list[str] = []
    for name, line, expected in cases:
        positives = [candidate for candidate in line_amount_candidates(line) if candidate.value > 0]
        if not positives:
            failures.append(f"{name}: no positive candidates found")
            continue
        selected, _note = select_interest_amount(line, positives)
        if selected.value != expected:
            failures.append(f"{name}: expected {expected}, got {selected.value}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    cop_pages = [
        PageText(
            Path("example-bank-cop.pdf"),
            1,
            "\n".join(
                [
                    "Movimientos de cuenta en COP",
                    "Fecha Descripción Movimiento Tarjeta Débito Abono Saldo",
                    "2025-01-03 16:57:44 Intereses abonados 2679063 $642 $9,110",
                ]
            ),
        )
    ]
    rows, _excluded, _warnings, meta = extract_rows_from_pages(cop_pages, "Example Bank", 2025, None)
    if meta.get("account_currency") != "COP":
        failures.append(f"cop-account-currency: expected COP account currency, got {meta.get('account_currency')}")
    if not rows or rows[0].get("currency") != "COP" or rows[0].get("amount_foreign") != "642.00":
        failures.append(f"cop-symbol-currency: expected 642.00 COP row, got {rows[:1]}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"Self-test passed: {len(cases) + 1} parser cases")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze one bank's statements for interest income.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="Extract interest rows from machine-readable statement PDFs.")
    extract.add_argument("--pdf", nargs="+", required=True, help="One or more statement PDF files.")
    extract.add_argument("--tax-year", required=True, type=int, help="Single tax year to analyze.")
    extract.add_argument("--institution", required=True, help="One institution name visible on every statement.")
    extract.add_argument("--account-currency", help="Optional account currency override, e.g. COP when statements use $ for pesos.")
    extract.add_argument("--out", required=True, help="Output JSON path, e.g. work/interest-analysis.json.")
    extract.add_argument("--csv", help="Optional CSV path. Defaults to interest-items.csv beside the JSON.")
    extract.set_defaults(func=command_extract)

    fx_prompt = subparsers.add_parser("fx-prompt", help="Print the user-facing FX confirmation prompt for non-USD rows.")
    fx_prompt.add_argument("--input", required=True, help="Input interest-analysis.json.")
    fx_prompt.add_argument(
        "--fx-method",
        choices=["get-yearly-fx-rate", "irs-yearly-average", "posted-daily-spot", "posted-yearly-average", "user-rate"],
        help="Proposed FX method.",
    )
    fx_prompt.add_argument("--fx-rate", help="Proposed exchange rate. Default direction is foreign currency units per 1 USD.")
    fx_prompt.add_argument("--fx-workpaper-json", help="workpaper.json produced by the get-yearly-fx-rate skill.")
    fx_prompt.add_argument("--fx-source", default="", help="Human-readable FX source label.")
    fx_prompt.add_argument(
        "--rate-direction",
        choices=["foreign-per-usd", "usd-per-foreign"],
        default="foreign-per-usd",
        help="Interpretation of --fx-rate. Defaults to IRS table direction.",
    )
    fx_prompt.set_defaults(func=command_fx_prompt)

    report = subparsers.add_parser("report", help="Generate the IRS-oriented support packet PDF.")
    report.add_argument("--input", required=True, help="Input interest-analysis.json.")
    report.add_argument(
        "--fx-method",
        choices=["get-yearly-fx-rate", "irs-yearly-average", "posted-daily-spot", "posted-yearly-average", "user-rate"],
        help="FX method. Required only when counted interest rows are not already USD.",
    )
    report.add_argument("--fx-rate", help="Exchange rate. Default direction is foreign currency units per 1 USD.")
    report.add_argument("--fx-workpaper-json", help="workpaper.json produced by the get-yearly-fx-rate skill.")
    report.add_argument("--fx-rates-json", help="Date-keyed FX rates JSON for row-level spot conversion.")
    report.add_argument("--fx-source", default="", help="Human-readable FX source label.")
    report.add_argument(
        "--fx-rate-confirmed",
        action="store_true",
        help="Required for non-USD reports after the user confirms the proposed rate or provides a custom rate.",
    )
    report.add_argument(
        "--fx-confirmation-note",
        default="",
        help="Optional note describing the user's FX confirmation or custom-rate instruction.",
    )
    report.add_argument(
        "--rate-direction",
        choices=["foreign-per-usd", "usd-per-foreign"],
        default="foreign-per-usd",
        help="Interpretation of --fx-rate. Defaults to IRS table direction.",
    )
    report.add_argument("--out", required=True, help="Output PDF path.")
    report.set_defaults(func=command_report)

    self_test = subparsers.add_parser("self-test", help="Run parser regression checks.")
    self_test.set_defaults(func=command_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
