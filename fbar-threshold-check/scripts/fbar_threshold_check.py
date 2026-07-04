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

SCHEMA_VERSION = "1.3"
# Ledgers older than 1.1 predate carry-gap coverage fields and would silently
# bypass the confirmation gates; refuse them instead. 1.3 stores native balances
# at their full parsed precision (see fmt_native); 1.1/1.2 ledgers remain
# readable but carry the older cent-rounded native balances.
SUPPORTED_SCHEMA_VERSIONS = {"1.1", "1.2", "1.3"}
THRESHOLD_USD = Decimal("10000")
MIN_TEXT_CHARS = 40
# Longest carry-forward run that is still routine for monthly statement cycles.
# Anything longer means statement evidence is missing for part of the year and
# must be reviewed by the user before confirmation.
MAX_ROUTINE_CARRY_DAYS = 40
ACCEPTED_FX_SKILLS = ("get-year-end-fx-rate", "get-yearly-fx-rate")

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

# One monetary token. Whitespace bridges digit groups only in the explicit
# space-grouped form (French/NBSP thousands like "500 000" or "1 234,56"). A
# decimal point breaks the run of 3-digit groups, so decimal-bearing columns
# ("100.00 200.00 300.00") still tokenize separately; bare space-grouped
# integers are flagged as ambiguous in parse_amount for magnitude review.
MONEY_NUMBER = (
    r"(?:\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d{1,2})?"  # lakh grouping: 1,23,456
    r"|\d{1,3}(?:[.,'’]\d{3})+(?:[.,]\d{1,6})?"  # punct-grouped thousands: 1.234,56
    r"|\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?"  # space-grouped: 1 234,56 or bare 500 000
    r"|\d+(?:[.,]\d{1,6})?)"  # plain: 1234.56
)
MONEY_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    # A sign binds only when glued to the number or its currency symbol:
    # leading "-9,500", trailing accounting minus "9,500.00-". A space-separated
    # dash ("9,500.00 - 31/12" or a column separator "1.234 - 5.678") is
    # statement punctuation, not a sign, so no \s* surrounds the sign atoms.
    r"(\(?\s*[-−]?(?:[$€£¥]\s*)?" + MONEY_NUMBER + r"(?:\s*\))?[-−]?)"
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


def parse_amount(raw: str) -> tuple[Decimal, tuple[str, ...]]:
    """Parse one statement money token into a Decimal plus parsing notes.

    Separator handling: when both `,` and `.` appear, the rightmost one is the
    decimal separator. With a single separator, only a 3-digit trailing group
    can be a thousands group; `1,234` / `1.234` / `2,500` are read as grouped
    thousands (the common statement meaning) and flagged as ambiguous so the
    review gate surfaces them.
    """
    original = clean_text(raw)
    value = original
    notes: list[str] = []
    negative = False
    # Space-separated digit groups ("500 000", "1 234 567") are read as one
    # grouped amount, but bare (no decimal separator) grouping is
    # indistinguishable from two adjacent statement columns, so flag it for
    # magnitude review. A decimal separator disambiguates it as one number.
    space_grouped_bare = re.search(r"\d[ \u00a0]\d", original) is not None and not re.search(r"[.,]", original)
    if value.startswith("(") and value.endswith(")"):
        negative = True
    value = re.sub(r"[A-Za-z$€£¥()\s'’]", "", value)
    # Accounting formats place the minus before or after the digits (SAP/German
    # trailing minus), and PDF text extraction often emits U+2212 for minus.
    if re.match(r"^[-−]", value) or re.search(r"[-−]$", value):
        negative = True
    value = value.replace("+", "").replace("-", "").replace("−", "")

    if not value:
        raise InvalidOperation("empty decimal")

    comma = value.rfind(",")
    dot = value.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal_sep = "," if comma > dot else "."
        thousands_sep = "." if decimal_sep == "," else ","
        value = value.replace(thousands_sep, "")
        value = value.replace(decimal_sep, ".")
    elif comma >= 0 or dot >= 0:
        sep = "," if comma >= 0 else "."
        parts = value.split(sep)
        head, tail = parts[0], parts[-1]
        if len(parts) > 2:
            # Repeated separators are digit grouping (thousands or lakh-style).
            value = "".join(parts)
        elif len(tail) != 3:
            # Only 3-digit trailing groups can be thousands groups.
            value = (head or "0") + "." + tail
        elif not head or head == "0":
            # `0,125` / `0.125` style values are always decimals.
            value = (head or "0") + "." + tail
        elif len(head) <= 3:
            # One separator with a 3-digit tail: grouped thousands is the
            # common statement meaning for both `1,234` and `1.234`.
            value = head + tail
            notes.append(
                f"Ambiguous separator in '{original}': interpreted as thousands grouping ({head}{tail}); verify magnitude against the statement."
            )
        else:
            # A 4+ digit head cannot be a leading thousands group; read as decimal.
            value = head + "." + tail
            notes.append(
                f"Ambiguous separator in '{original}': interpreted as decimal ({head}.{tail}); verify magnitude against the statement."
            )

    parsed = Decimal(value)
    if space_grouped_bare:
        notes.append(
            f"Ambiguous space-separated digit groups in '{original}': read as one amount ({value}); "
            "verify this is a single balance and not adjacent statement columns."
        )
    return (-parsed if negative else parsed), tuple(notes)


def decimal_from_text(raw: str) -> Decimal:
    return parse_amount(raw)[0]


def fmt_decimal(value: Decimal | None, places: str = "0.01") -> str | None:
    if value is None:
        return None
    quantized = value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def fmt_native(value: Decimal | None) -> str | None:
    """Format a native-currency balance without lossy rounding.

    fmt_decimal quantizes to cents, which silently drops the third decimal of
    3-decimal currencies (KWD/BHD/OMR/JOD) and can flip the answer at the
    $10,000 boundary. Native balances must retain the exact parsed precision;
    only trailing-zero display noise is trimmed.
    """
    if value is None:
        return None
    text = format(value, "f")
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


def mask_date_spans(line: str) -> str:
    """Blank out date substrings so their fragments cannot parse as money.

    Uses '#' (not alphanumeric) so masking never bridges or blocks adjacent
    monetary tokens; character positions are preserved.
    """
    chars = list(line)
    for pattern in (*DATE_PATTERNS, *MONTH_DATE_PATTERNS):
        for match in pattern.finditer(line):
            for index in range(match.start(), match.end()):
                chars[index] = "#"
    return "".join(chars)


def parse_money_values(line: str) -> list[tuple[Decimal, str, int, tuple[str, ...]]]:
    values: list[tuple[Decimal, str, int, tuple[str, ...]]] = []
    for match in MONEY_RE.finditer(line):
        token = clean_text(match.group(1))
        if re.fullmatch(r"20\d{2}", token):
            continue
        if len(re.sub(r"\D", "", token)) > 16:
            continue
        try:
            amount, notes = parse_amount(token)
        except (InvalidOperation, ValueError):
            continue
        values.append((amount, token, match.start(1), notes))
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
        try:
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
        except FbarError:
            raise
        except Exception as exc:
            raise FbarError(f"Could not read {path} as a PDF: {exc}", 2) from exc

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
    ambiguous_amount_lines = 0
    for ref, line in lines:
        lower = line.lower()
        date_hits = parse_line_dates(line)
        if not date_hits:
            continue
        # Mask date substrings first so fragments like "31/12" or "31, 2023"
        # can never be selected as the day's balance.
        money_values = parse_money_values(mask_date_spans(line))
        if not money_values:
            continue

        has_balance_term = any(term in lower for term in BALANCE_TERMS)
        header_has_balance = any(term in page_text[(ref.file, ref.page)] for term in BALANCE_TERMS)
        if not has_balance_term and not (header_has_balance and len(money_values) >= 3):
            continue
        if any(term in lower for term in LOW_VALUE_TERMS) and not has_balance_term:
            continue

        # Prefer the last monetary value that appears after the balance term,
        # so amount/description columns before the balance column are skipped.
        term_pos = -1
        if has_balance_term:
            term_pos = max(lower.rfind(term) for term in BALANCE_TERMS)
        selected_values = [item for item in money_values if item[2] >= term_pos] if term_pos >= 0 else money_values
        if not selected_values:
            selected_values = money_values
        amount, token, _pos, parse_notes = selected_values[-1]
        bare_small_int = re.fullmatch(r"[-−]?\d{1,2}[-−]?", token) is not None
        if parse_notes:
            ambiguous_amount_lines += 1
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
            if parse_notes:
                confidence = "medium" if confidence == "high" else confidence
                notes.extend(parse_notes)
            if bare_small_int:
                # A 1-2 digit integer with no separators is more likely a row
                # number or stray fragment than a balance; force review.
                confidence = "low"
                low_confidence += 1
                notes.append(f"Selected value '{token}' is a bare 1-2 digit integer; verify it is really the balance.")
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
    if ambiguous_amount_lines:
        warnings.append(
            f"{ambiguous_amount_lines} balance line(s) had ambiguous thousands/decimal separators; verify native balance magnitudes in the review CSV."
        )
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
    first_observed: date | None = None
    last_observed: date | None = None
    rows: list[dict[str, object]] = []
    observed_days = 0
    carried_days = 0
    missing_days = 0
    low_confidence_days = 0
    material_variance_days = 0
    carry_run_start: date | None = None
    carry_run_days = 0
    longest_carry_run = 0
    carry_gaps: list[dict[str, object]] = []

    def close_carry_run(end_day: date) -> None:
        nonlocal carry_run_start, carry_run_days, longest_carry_run
        longest_carry_run = max(longest_carry_run, carry_run_days)
        if carry_run_days > MAX_ROUTINE_CARRY_DAYS and carry_run_start is not None:
            carry_gaps.append({"start": iso_day(carry_run_start), "end": iso_day(end_day), "days": carry_run_days})
        carry_run_start = None
        carry_run_days = 0

    for day in calendar_dates(tax_year):
        day_candidates = by_day.get(day, [])
        if day_candidates:
            if carry_run_days:
                close_carry_run(day - timedelta(days=1))
            selected = max(day_candidates, key=lambda item: item.amount)
            confidence = selected.confidence
            notes = list(selected.notes)
            amounts = sorted({item.amount for item in day_candidates})
            if len(amounts) > 1:
                low_amount, high_amount = amounts[0], amounts[-1]
                # Same-day balances that disagree materially (max more than
                # double the min) may contain a non-balance token; surface for review.
                if high_amount - low_amount > high_amount.copy_abs() * Decimal("0.5"):
                    material_variance_days += 1
                    if confidence == "high":
                        confidence = "medium"
                    notes.append(
                        f"Same-day balance candidates ranged from {fmt_native(low_amount)} to {fmt_native(high_amount)}; "
                        "the maximum was selected for threshold safety - verify against the statement."
                    )
            last_balance = selected.amount
            last_ref = selected.source
            if first_observed is None:
                first_observed = day
            last_observed = day
            observed_days += 1
            if confidence == "low":
                low_confidence_days += 1
            rows.append(
                {
                    "date": iso_day(day),
                    "currency": currency,
                    "native_balance": fmt_native(selected.amount),
                    "usd_balance": None,
                    "threshold_usd_value": None,
                    "balance_source": "observed",
                    "confidence": confidence,
                    "source_refs": [source_ref_to_string(selected.source)],
                    "notes": notes,
                }
            )
        elif last_balance is not None:
            carried_days += 1
            if carry_run_start is None:
                carry_run_start = day
            carry_run_days += 1
            rows.append(
                {
                    "date": iso_day(day),
                    "currency": currency,
                    "native_balance": fmt_native(last_balance),
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

    if carry_run_days:
        close_carry_run(date(tax_year, 12, 31))
    trailing_carry_days = (date(tax_year, 12, 31) - last_observed).days if last_observed else 0

    if missing_days:
        warnings.append(f"{missing_days} day(s) lack opening/prior balance coverage; do not return a confident daily-threshold no.")
    if low_confidence_days:
        warnings.append(f"{low_confidence_days} day(s) have low-confidence balances and need review.")
    if material_variance_days:
        warnings.append(
            f"{material_variance_days} day(s) had materially different same-day balance candidates; the daily maximum was selected - verify those rows in the review CSV."
        )
    if last_observed is not None and trailing_carry_days > MAX_ROUTINE_CARRY_DAYS:
        warnings.append(
            f"Statement evidence stops on {iso_day(last_observed)}; the final {trailing_carry_days} day(s) of the year are carried forward, not observed. "
            "Do not treat the year as fully evidenced without user review."
        )
    interior_gaps = [gap for gap in carry_gaps if gap["end"] != iso_day(date(tax_year, 12, 31))]
    if interior_gaps:
        longest_interior = max(int(gap["days"]) for gap in interior_gaps)
        warnings.append(
            f"{len(interior_gaps)} carry-forward gap(s) exceed {MAX_ROUTINE_CARRY_DAYS} days inside the year (longest {longest_interior} day(s)); "
            "statement coverage may be missing periods."
        )

    coverage = {
        "year": tax_year,
        "calendar_days": len(rows),
        "observed_days": observed_days,
        "carried_forward_days": carried_days,
        "missing_days": missing_days,
        "complete_year": missing_days == 0,
        "low_confidence_days": low_confidence_days,
        "first_observed_date": iso_day(first_observed) if first_observed else None,
        "last_observed_date": iso_day(last_observed) if last_observed else None,
        "trailing_carry_days": trailing_carry_days,
        "longest_carry_run_days": longest_carry_run,
        "carry_gaps": carry_gaps,
        "carry_gap_review_required": bool(carry_gaps),
        "material_same_day_variance_days": material_variance_days,
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
    stem = out_path.stem
    if stem.endswith(f"-{suffix}"):
        return out_path.with_name(stem + ".csv")
    return out_path.with_name(stem + f"-{suffix}.csv")


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
    version = str(data.get("schema_version") or "missing")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise FbarError(
            f"{path} has unsupported schema_version {version}; re-run extract-account with the current skill "
            f"(schema {SCHEMA_VERSION}) so coverage gating applies.",
            2,
        )
    return data


def as_decimal(value: object, label: str) -> Decimal:
    if value is None or value == "":
        raise FbarError(f"Missing decimal value for {label}.", 2)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise FbarError(f"Could not parse decimal value for {label}: {value}", 2) from exc


def as_int(value: object, label: str) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise FbarError(f"Could not parse integer value for {label}: {value!r}", 2) from exc


def validate_fx_workpaper(path: Path, currency: str, year: int) -> dict[str, object]:
    workpaper = load_json(path)
    fx_skill = workpaper.get("skill")
    if fx_skill not in ACCEPTED_FX_SKILLS:
        raise FbarError(
            "FX workpaper must come from get-year-end-fx-rate (preferred for FBAR conversion) or get-yearly-fx-rate.",
            2,
        )
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
    for rate_key, rate_value in (("foreign_per_usd", foreign_per_usd), ("usd_per_foreign", usd_per_foreign)):
        if rate_value:
            rate = as_decimal(rate_value, rate_key)
            if rate <= 0:
                raise FbarError(f"FX workpaper {rate_key} must be a positive rate; got {rate_value}.", 2)

    # get-year-end-fx-rate workpapers are year-end by construction; the
    # FBAR-compatibility heuristic only guards yearly-average workpapers.
    if fx_skill == "get-yearly-fx-rate" and not is_fbar_compatible_workpaper(workpaper):
        raise FbarError(
            "FX workpaper appears to be yearly-average only. Use get-year-end-fx-rate instead, or complete/update "
            "get-yearly-fx-rate so the workpaper is explicitly FBAR/year-end compatible before confirming this account.",
            2,
        )
    return workpaper


def is_fbar_compatible_workpaper(workpaper: dict[str, object]) -> bool:
    # Explicit self-certification by the FX skill is the only unconditional accept.
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
    intent_text = " ".join(values).lower()
    caveat_text = " ".join(str(item) for item in caveats).lower() if isinstance(caveats, list) else ""

    # Only explicit year-end / FBAR intent is positive evidence. Provenance
    # alone (Treasury / Fiscal Data / FMS) is NOT sufficient: those sources
    # publish both year-end AND yearly-average tables, so a Treasury-sourced
    # yearly average must not pass. Source names are deliberately excluded here.
    yearend_terms = (
        "fbar",
        "fincen",
        "year-end",
        "year end",
        "year_end",
        "last day",
        "last business day",
        "12/31",
        "12-31",
        "december 31",
        "dec 31",
        "dec. 31",
        "end of year",
        "end-of-year",
    )
    # Explicit average language anywhere - rate fields, source notes, or
    # caveats - disqualifies the workpaper even if a year-end word also appears.
    # Contradictory metadata is unsafe, so the correct action is to demand
    # get-year-end-fx-rate rather than guess.
    average_terms = (
        "yearly average",
        "yearly-average",
        "yearly avg",
        "annual average",
        "annual-average",
        "annual avg",
        "period average",
        "average exchange rate",
        "average rate",
    )
    has_average = any(term in intent_text + " " + caveat_text for term in average_terms)
    if has_average:
        return False
    return any(term in intent_text for term in yearend_terms)


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
    carry_gaps = coverage.get("carry_gaps") if isinstance(coverage.get("carry_gaps"), list) else []
    accept_carry_forward = bool(getattr(args, "accept_carry_forward", False))
    if carry_gaps and not accept_carry_forward:
        raise FbarError(
            f"Account ledger has carry-forward gap(s) longer than {MAX_ROUTINE_CARRY_DAYS} days (statement evidence is missing "
            "for part of the year; see coverage.carry_gaps). Obtain the missing statements, or re-run with "
            "--accept-carry-forward only after the user has explicitly reviewed and accepted the carried balances.",
            2,
        )

    account = data.get("account", {})
    if not isinstance(account, dict):
        raise FbarError("Account ledger has no account object.", 2)
    currency = str(account.get("currency") or "UNKNOWN").upper()
    tax_year = as_int(data.get("tax_year", 0), "tax_year")
    if currency in {"UNKNOWN", "MIXED"}:
        raise FbarError("Account currency is not confirmed; regenerate with --account-currency or split the account.", 2)

    fx_workpaper: dict[str, object] | None = None
    if currency != "USD":
        if not args.fx_workpaper_json:
            raise FbarError(
                "Non-USD account requires --fx-workpaper-json from get-year-end-fx-rate (preferred) "
                "or an FBAR-compatible get-yearly-fx-rate workpaper.",
                2,
            )
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
        # Keep sub-cent precision in the threshold work value: rounding to
        # cents before the FinCEN whole-dollar ceiling can flip the answer
        # at the $10,000 boundary.
        row["threshold_usd_value"] = fmt_decimal(threshold_value, "0.000001")
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
        "carry_forward_accepted": bool(carry_gaps),
    }
    if carry_gaps:
        # Surface the acceptance so it propagates into the aggregate summary.
        existing_warnings = data.get("warnings")
        if not isinstance(existing_warnings, list):
            existing_warnings = []
        existing_warnings.append(
            f"User accepted {len(carry_gaps)} carry-forward gap(s); daily results rely on carried, not observed, balances for those spans."
        )
        data["warnings"] = sorted({str(warning) for warning in existing_warnings})
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
        if rate <= 0:
            raise FbarError("FX foreign_per_usd rate must be positive.", 2)
        return native / rate
    rate = as_decimal(workpaper.get("usd_per_foreign"), "usd_per_foreign")
    if rate <= 0:
        raise FbarError("FX usd_per_foreign rate must be positive.", 2)
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
    resolved_paths = [Path(path).resolve() for path in args.account_ledger]
    if len(set(resolved_paths)) != len(resolved_paths):
        raise FbarError("Duplicate --account-ledger paths detected; pass each confirmed account exactly once.", 2)
    accounts = [load_confirmed_account(path) for path in resolved_paths]
    if not accounts:
        raise FbarError("At least one --account-ledger is required.", 2)

    seen_identities: set[tuple[object, ...]] = set()
    for account_data, ledger_path in zip(accounts, resolved_paths, strict=True):
        account = account_data.get("account", {})
        if not isinstance(account, dict):
            account = {}
        identity = (
            account.get("account_id"),
            account.get("institution"),
            tuple(account.get("account_number_hints") or []),
        )
        if identity in seen_identities:
            raise FbarError(
                f"{ledger_path} describes the same account as another ledger ({identity[0]!r} at {identity[1]!r}); "
                "if these are truly different accounts, re-extract one with a distinct --account-id.",
                2,
            )
        seen_identities.add(identity)

    tax_years = {as_int(account.get("tax_year", 0), "tax_year") for account in accounts}
    if len(tax_years) != 1:
        raise FbarError("All account ledgers must have the same tax year.", 2)
    tax_year = tax_years.pop()
    days = [iso_day(day) for day in calendar_dates(tax_year)]

    account_labels = unique_account_labels(accounts)
    account_rows: dict[str, dict[str, dict[str, object]]] = {}
    account_summaries: list[dict[str, object]] = []
    warnings: list[str] = []
    fx_workpapers: list[object] = []
    records_complete = True

    for label, account_data in zip(account_labels, accounts, strict=True):
        account_coverage = account_data.get("coverage", {})
        if not isinstance(account_coverage, dict):
            account_coverage = {}
        if int(account_coverage.get("missing_days", 0)) > 0 or account_coverage.get("carry_gaps"):
            records_complete = False
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

    if over_limit_dates:
        daily_answer = "yes"
    elif records_complete:
        daily_answer = "no"
    else:
        daily_answer = "insufficient-records"

    summary: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "skill": "fbar-threshold-check",
        "status": "aggregated",
        "tax_year": tax_year,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "account_count": len(accounts),
        "daily_threshold": {
            "threshold_usd": "10000",
            "answer": daily_answer,
            "exceeded": bool(over_limit_dates),
            "over_limit_dates": over_limit_dates,
            "max_combined_usd_value": fmt_decimal(max_combined),
            "max_combined_date": max_combined_date,
            "records_complete": records_complete,
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

    print(f"Daily threshold answer: {daily_answer}")
    print(f"FinCEN max-value view exceeded: {'yes' if aggregate_max_whole > 10000 else 'no'}")
    if over_limit_dates:
        print(f"Over-limit dates: {', '.join(over_limit_dates[:20])}{' ...' if len(over_limit_dates) > 20 else ''}")
    print(f"Wrote final JSON: {out_path}")
    print(f"Wrote final CSV: {csv_path}")
    print(f"Wrote summary PDF: {pdf_path}")
    return 0


def unique_account_labels(accounts: list[dict[str, object]]) -> list[str]:
    bases: list[str] = []
    for index, account_data in enumerate(accounts, start=1):
        account = account_data.get("account", {})
        if not isinstance(account, dict):
            account = {}
        base = str(account.get("account_id") or f"account-{index}")
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-") or f"account-{index}"
        bases.append(base)
    # Suffix against the full label set: a naive per-base counter can collide
    # with another account's literal id (acct, acct, acct-2) and silently
    # drop/double-count ledgers in the daily combination.
    used: set[str] = set()
    labels: list[str] = []
    for base in bases:
        label = base
        bump = 1
        while label in used:
            bump += 1
            label = f"{base}-{bump}"
        used.add(label)
        labels.append(label)
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
    daily_answer = str(daily.get("answer") or ("yes" if daily["exceeded"] else "no"))
    daily_labels = {
        "yes": "YES",
        "no": "NO",
        "insufficient-records": "INSUFFICIENT RECORDS FOR A CONFIDENT NO",
    }
    lines = [
        f"FBAR Threshold Check - {summary['tax_year']}",
        "",
        f"Daily threshold: {daily_labels.get(daily_answer, daily_answer.upper())}",
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
        test_decimal_parsing()
        test_money_tokenization()
        test_sign_formats()
        test_line_extraction()
        test_build_daily_rows()
        test_native_balance_precision()
        test_leap_year()
        test_same_day_variance()
        test_carry_gap_gate(root)
        test_fx_guardrails(root)
        test_fx_rate_guards(root)
        test_fx_provenance_not_yearend(root)
        test_boundary_rounding(root)
        test_label_collisions()
        test_duplicate_ledger_refused(root)
        test_schema_gate(root)
        test_confirm_and_aggregate(root)
        test_maxima_disagreement(root)
        test_csv_naming()
    print("self-test ok")
    return 0


def month_end_dates(year: int) -> list[date]:
    ends: list[date] = []
    for month in range(1, 13):
        ends.append(date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1))
    return ends


def synthetic_account(
    root: Path,
    name: str,
    year: int,
    currency: str,
    start_balance: Decimal,
    changes: dict[str, Decimal],
    fx_workpaper: Path | None = None,
) -> Path:
    events = sorted((date.fromisoformat(raw_day), value) for raw_day, value in changes.items())

    def balance_on(day: date) -> Decimal:
        balance = start_balance
        for event_day, value in events:
            if event_day <= day:
                balance = value
        return balance

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
    for event_day, value in events:
        candidates.append(
            BalanceCandidate(
                balance_date=event_day,
                amount=value,
                currency=currency,
                confidence="high",
                source=SourceRef(f"{name}.pdf", 1, 2, "synthetic balance"),
                notes=("synthetic self-test row",),
            )
        )
    # Month-end closing balances keep every carry-forward run routine,
    # mirroring a normal monthly statement cycle.
    for month_end in month_end_dates(year):
        candidates.append(
            BalanceCandidate(
                balance_date=month_end,
                amount=balance_on(month_end),
                currency=currency,
                confidence="high",
                source=SourceRef(f"{name}.pdf", 1, 3, "synthetic month-end closing balance"),
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
        accept_carry_forward=False,
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


def make_year_end_fx_workpaper(
    root: Path,
    currency: str = "COP",
    year: int = 2025,
    foreign_per_usd: str | None = "4200",
    usd_per_foreign: str | None = "0.0002380952",
    name: str = "year-end-workpaper",
) -> Path:
    path = root / f"{currency.lower()}-{year}-{name}.json"
    data = {
        "skill": "get-year-end-fx-rate",
        "purpose": "FBAR-style year-end USD exchange-rate support",
        "currency": currency,
        "year": year,
        "year_end_date": f"{year}-12-31",
        "rate": foreign_per_usd or usd_per_foreign,
        "rate_direction": "foreign-per-usd" if foreign_per_usd else "usd-per-foreign",
        "foreign_per_usd": foreign_per_usd,
        "usd_per_foreign": usd_per_foreign,
        "source": {
            # Deliberately neutral wording: the year-end skill must be accepted
            # without the yearly-average FBAR-keyword heuristic.
            "title": "Example central bank closing table",
            "url": "https://example.test/closing-table",
            "retrieved": "2026-01-05",
        },
        "proof": {
            "workpaper_json": str(path),
            "workpaper_pdf": str(root / "year-end-proof.pdf"),
            "saved_files": [{"path": str(root / "year-end-proof.json"), "sha256": "abc"}],
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


def test_money_tokenization() -> None:
    # Adjacent statement columns must tokenize separately, never merge.
    tokens = [token for _amt, token, _pos, _n in parse_money_values("Saldo 100.00 200.00 300.00")]
    assert tokens == ["100.00", "200.00", "300.00"], tokens
    # Space-grouped French amounts stay one token; a decimal disambiguates it
    # as a single number, so no ambiguity note is attached.
    values = parse_money_values("solde 1 234,56")
    assert len(values) == 1 and values[0][0] == Decimal("1234.56")
    assert not values[0][3], values[0][3]
    # Bare space-grouped integers (French "500 000") are read as one amount at
    # the correct magnitude - never split into "500" + "000" - but flagged as
    # ambiguous so the review gate surfaces the possible-columns reading.
    values = parse_money_values("solde 500 000")
    assert len(values) == 1 and values[0][0] == Decimal("500000"), values
    assert values[0][3], "bare space-grouped integer must carry an ambiguity note"
    values = parse_money_values("solde 1 234 567")
    assert len(values) == 1 and values[0][0] == Decimal("1234567"), values
    # Columnar integers whose leading group is 4+ digits cannot start a
    # space-grouped run, so they stay separate.
    # ("2000" is dropped by the bare-year guard, not merged into "1500".)
    tokens = [token for _amt, token, _pos, _n in parse_money_values("balance 1500 2000")]
    assert tokens == ["1500"], tokens
    tokens = [token for _amt, token, _pos, _n in parse_money_values("balance 1500 2600")]
    assert tokens == ["1500", "2600"], tokens
    # Date masking removes date fragments from money candidates.
    masked = mask_date_spans("31/12/2023 closing balance 1,234.56 1,300.00")
    amounts = [amt for amt, _t, _p, _n in parse_money_values(masked)]
    assert amounts == [Decimal("1234.56"), Decimal("1300.00")], amounts
    masked = mask_date_spans("1,234.56 balance as of Dec 31, 2023")
    amounts = [amt for amt, _t, _p, _n in parse_money_values(masked)]
    assert amounts == [Decimal("1234.56")], amounts


def test_sign_formats() -> None:
    cases = {
        "9.500,25-": Decimal("-9500.25"),
        "−4,321.00": Decimal("-4321.00"),
        "US$ -500": Decimal("-500"),
        "(2,50)": Decimal("-2.50"),
        "1.234,56": Decimal("1234.56"),
    }
    for raw, expected in cases.items():
        amount, _notes = parse_amount(raw)
        assert amount == expected, f"{raw!r} parsed to {amount}, expected {expected}"
    # U+2212 minus survives tokenization end-to-end.
    values = parse_money_values("balance −4,321.00")
    assert len(values) == 1 and values[0][0] == Decimal("-4321.00")


def test_line_extraction() -> None:
    def extract_one(line: str, year: int = 2023, currency: str = "EUR") -> BalanceCandidate:
        candidates, _warnings = extract_balance_candidates([(SourceRef("t.pdf", 1, 1, line), line)], year, currency)
        assert candidates, f"no candidate extracted from {line!r}"
        return candidates[-1]

    us = extract_one("12/31/2023 Ending balance 1,234.56")
    assert us.amount == Decimal("1234.56") and us.confidence == "high"
    columnar = extract_one("2023-12-31 Saldo 100.00 200.00 300.00")
    assert columnar.amount == Decimal("300.00"), columnar.amount
    european = extract_one("31.12.2023 Saldo 1.500,00 12.345,67")
    assert european.amount == Decimal("12345.67"), european.amount
    date_after = extract_one("1,234.56 balance as of Dec 31, 2023")
    assert date_after.amount == Decimal("1234.56"), date_after.amount
    trailing = extract_one("31/12/2023 closing balance 9.500,25-")
    assert trailing.amount == Decimal("-9500.25"), trailing.amount
    # A dash separated from the number by whitespace is punctuation, not an
    # accounting sign: the balance must stay positive, not be zeroed. This holds
    # for a trailing dash and for a dash used as a column separator (the leading
    # side), both of which previously bound as a spurious negative sign.
    dash_sep = extract_one("31/12/2023 Closing balance 9,500.00 - see note")
    assert dash_sep.amount == Decimal("9500.00"), dash_sep.amount
    separator = extract_one("31/12/2023 balance 1.234,00 - 5.678,00")
    assert separator.amount == Decimal("5678.00"), separator.amount
    # Bare space-grouped French integer: correct magnitude, but downgraded to a
    # reviewable confidence with an ambiguity note (never a silent high-confidence pick).
    french = extract_one("31/12/2023 Solde 500 000")
    assert french.amount == Decimal("500000"), french.amount
    assert french.confidence != "high"
    assert any("space-separated" in note for note in french.notes), french.notes
    bare = extract_one("31/12/2023 balance 5")
    assert bare.amount == Decimal("5") and bare.confidence == "low"


def test_fx_rate_guards(root: Path) -> None:
    zero_rate = make_year_end_fx_workpaper(root, foreign_per_usd=None, usd_per_foreign="0", name="zero-rate")
    try:
        validate_fx_workpaper(zero_rate, "COP", 2025)
    except FbarError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("zero usd_per_foreign must be rejected")
    negative_rate = make_year_end_fx_workpaper(root, foreign_per_usd=None, usd_per_foreign="-0.00025", name="negative-rate")
    try:
        validate_fx_workpaper(negative_rate, "COP", 2025)
    except FbarError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("negative usd_per_foreign must be rejected")

    # Caveats must never count as positive FBAR/year-end evidence.
    caveat_only = root / "caveat-only-workpaper.json"
    write_json(
        caveat_only,
        {
            "skill": "get-yearly-fx-rate",
            "currency": "CAD",
            "year": 2025,
            "foreign_per_usd": "1.37",
            "usd_per_foreign": "0.729927",
            "source": {
                "title": "IRS Yearly average currency exchange rates",
                "url": "https://www.irs.gov/",
                "retrieved": "2026-07-04",
            },
            "proof": {"workpaper_json": str(caveat_only)},
            "caveats": ["Not intended for FBAR or year-end use."],
        },
    )
    try:
        validate_fx_workpaper(caveat_only, "CAD", 2025)
    except FbarError:
        pass
    else:
        raise AssertionError("caveat wording must not qualify a yearly-average workpaper as FBAR-compatible")


def test_fx_provenance_not_yearend(root: Path) -> None:
    # A Treasury/Fiscal Data provenance is NOT a year-end signal: those sources
    # publish both year-end and yearly-average tables. A Treasury-sourced yearly
    # average that names its rate_kind "yearly average" must be rejected.
    treasury_avg = root / "treasury-average-workpaper.json"
    write_json(
        treasury_avg,
        {
            "skill": "get-yearly-fx-rate",
            "currency": "CAD",
            "year": 2025,
            "foreign_per_usd": "1.37",
            "usd_per_foreign": "0.729927",
            "rate_kind": "yearly average",
            "source": {
                "title": "Treasury Reporting Rates of Exchange - Fiscal Data",
                "url": "https://fiscaldata.treasury.gov/",
                "retrieved": "2026-07-04",
                "note": "Fiscal Data yearly average table.",
            },
            "proof": {"workpaper_json": str(treasury_avg)},
        },
    )
    assert is_fbar_compatible_workpaper(load_json(treasury_avg)) is False
    try:
        validate_fx_workpaper(treasury_avg, "CAD", 2025)
    except FbarError as exc:
        assert "yearly-average" in str(exc)
    else:
        raise AssertionError("Treasury-sourced yearly average must be rejected")

    # Provenance alone (Treasury/Fiscal Data, no year-end wording) is not enough.
    treasury_only = root / "treasury-only-workpaper.json"
    write_json(
        treasury_only,
        {
            "skill": "get-yearly-fx-rate",
            "currency": "CAD",
            "year": 2025,
            "foreign_per_usd": "1.37",
            "usd_per_foreign": "0.729927",
            "source": {
                "title": "Treasury Fiscal Data exchange rates",
                "url": "https://fiscaldata.treasury.gov/",
                "retrieved": "2026-07-04",
            },
            "proof": {"workpaper_json": str(treasury_only)},
        },
    )
    assert is_fbar_compatible_workpaper(load_json(treasury_only)) is False

    # Explicit year-end intent with no average language is accepted.
    yearend_yearly = root / "yearend-yearly-workpaper.json"
    write_json(
        yearend_yearly,
        {
            "skill": "get-yearly-fx-rate",
            "currency": "CAD",
            "year": 2025,
            "foreign_per_usd": "1.37",
            "usd_per_foreign": "0.729927",
            "rate_kind": "FBAR year-end rate (December 31 close)",
            "source": {
                "title": "Central bank year-end closing rate",
                "url": "https://example.test/",
                "retrieved": "2026-07-04",
            },
            "proof": {"workpaper_json": str(yearend_yearly)},
        },
    )
    assert is_fbar_compatible_workpaper(load_json(yearend_yearly)) is True
    assert validate_fx_workpaper(yearend_yearly, "CAD", 2025)["skill"] == "get-yearly-fx-rate"


def test_boundary_rounding(root: Path) -> None:
    fx = make_year_end_fx_workpaper(root, foreign_per_usd=None, usd_per_foreign="0.010000004", name="boundary-rate")
    ledger = synthetic_account(root, "boundary-a", 2025, "COP", Decimal("1000000"), {}, fx)
    out = root / "boundary-summary.json"
    command_aggregate(argparse.Namespace(account_ledger=[str(ledger)], out=str(out), csv=None, pdf=None))
    summary = load_json(out)
    daily = summary["daily_threshold"]
    max_view = summary["fincen_max_value_view"]
    assert isinstance(daily, dict) and isinstance(max_view, dict)
    # True USD value is 10000.004: over the threshold, FinCEN whole-dollar 10001.
    assert daily["answer"] == "yes", daily
    assert max_view["exceeded"] is True, max_view
    assert max_view["aggregate_account_max_whole_dollars"] == 10001, max_view


def test_label_collisions() -> None:
    accounts = [
        {"account": {"account_id": "acct"}},
        {"account": {"account_id": "acct"}},
        {"account": {"account_id": "acct-2"}},
    ]
    labels = unique_account_labels(accounts)  # type: ignore[arg-type]
    assert len(labels) == len(set(labels)) == 3, labels
    assert labels[0] == "acct"


def test_duplicate_ledger_refused(root: Path) -> None:
    ledger = synthetic_account(root, "dup-a", 2025, "USD", Decimal("6000"), {})
    out = root / "dup-summary.json"
    try:
        command_aggregate(argparse.Namespace(account_ledger=[str(ledger), str(ledger)], out=str(out), csv=None, pdf=None))
    except FbarError as exc:
        assert "Duplicate" in str(exc)
    else:
        raise AssertionError("duplicate ledger paths must be refused")
    twin = root / "dup-a-copy.json"
    twin.write_text(Path(ledger).read_text(encoding="utf-8"), encoding="utf-8")
    try:
        command_aggregate(argparse.Namespace(account_ledger=[str(ledger), str(twin)], out=str(out), csv=None, pdf=None))
    except FbarError as exc:
        assert "same account" in str(exc)
    else:
        raise AssertionError("two ledgers describing the same account must be refused")


def test_schema_gate(root: Path) -> None:
    stale = root / "v10-ledger.json"
    write_json(
        stale,
        {
            "schema_version": "1.0",
            "skill": "fbar-threshold-check",
            "status": "confirmed",
            "tax_year": 2025,
            "daily_ledger": [],
        },
    )
    try:
        require_account_data(stale)
    except FbarError as exc:
        assert "schema_version" in str(exc)
    else:
        raise AssertionError("pre-1.1 ledgers must be refused so coverage gating applies")


def test_decimal_parsing() -> None:
    cases = {
        "1.234,56": Decimal("1234.56"),
        "1,234.56": Decimal("1234.56"),
        "2,500": Decimal("2500"),
        "1,234": Decimal("1234"),
        "1.234": Decimal("1234"),
        "0.125": Decimal("0.125"),
        "0,125": Decimal("0.125"),
        "12,00": Decimal("12.00"),
        "1234.56": Decimal("1234.56"),
        "4.000.000": Decimal("4000000"),
        "1,23,456": Decimal("123456"),
        "0.00025": Decimal("0.00025"),
        "(2,50)": Decimal("-2.50"),
        "1 234,56": Decimal("1234.56"),
        "2.500,00": Decimal("2500.00"),
        "$1,250.75": Decimal("1250.75"),
        "1234,567": Decimal("1234.567"),
    }
    for raw, expected in cases.items():
        amount, _notes = parse_amount(raw)
        assert amount == expected, f"{raw!r} parsed to {amount}, expected {expected}"
    grouped, grouped_notes = parse_amount("2,500")
    assert grouped == Decimal("2500")
    assert grouped_notes, "grouped-thousands reading must carry an ambiguity note"
    plain, plain_notes = parse_amount("1,234.56")
    assert plain == Decimal("1234.56")
    assert not plain_notes


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
    assert rows[0]["confidence"] == "high"
    assert coverage["complete_year"] is True
    # Statements stop on Jan 3: the trailing carry to Dec 31 must be flagged.
    assert coverage["last_observed_date"] == "2025-01-03"
    assert coverage["trailing_carry_days"] == 362
    assert coverage["carry_gap_review_required"] is True
    assert coverage["carry_gaps"] and coverage["carry_gaps"][-1]["end"] == "2025-12-31"
    assert any("carried forward" in warning for warning in warnings)


def test_native_balance_precision() -> None:
    # 3-decimal currencies (KWD/BHD/OMR) must keep the third decimal in the
    # stored and carried-forward native balance; cent-rounding flips boundaries.
    candidates = [BalanceCandidate(date(2025, 1, 1), Decimal("1234.567"), "KWD", "high", SourceRef("k.pdf", 1, 1, ""))]
    rows, _coverage, _warnings = build_daily_rows(2025, "KWD", candidates)
    assert rows[0]["native_balance"] == "1234.567", rows[0]["native_balance"]
    assert rows[0]["balance_source"] == "observed"
    # The carried-forward days must also retain full precision.
    assert rows[1]["native_balance"] == "1234.567", rows[1]["native_balance"]
    assert rows[1]["balance_source"] == "carried-forward"


def test_leap_year() -> None:
    candidates = [BalanceCandidate(date(2024, 1, 1), Decimal("1"), "USD", "high", SourceRef("a.pdf", 1, 1, ""))]
    rows, coverage, _warnings = build_daily_rows(2024, "USD", candidates)
    assert len(rows) == 366
    assert coverage["calendar_days"] == 366
    assert coverage["trailing_carry_days"] == 365


def test_same_day_variance() -> None:
    candidates = [
        BalanceCandidate(date(2025, 1, 1), Decimal("100"), "USD", "high", SourceRef("a.pdf", 1, 1, "")),
        BalanceCandidate(date(2025, 1, 1), Decimal("100000"), "USD", "high", SourceRef("a.pdf", 1, 2, "")),
    ]
    rows, coverage, warnings = build_daily_rows(2025, "USD", candidates)
    assert rows[0]["native_balance"] == "100000"
    assert rows[0]["confidence"] == "medium"
    assert any("maximum was selected" in note for note in rows[0]["notes"])
    assert coverage["material_same_day_variance_days"] == 1
    assert any("same-day balance candidates" in warning for warning in warnings)


def test_carry_gap_gate(root: Path) -> None:
    candidates = [
        BalanceCandidate(date(2025, 1, 1), Decimal("5000"), "USD", "high", SourceRef("gap.pdf", 1, 1, "")),
        BalanceCandidate(date(2025, 6, 30), Decimal("6000"), "USD", "high", SourceRef("gap.pdf", 1, 2, "")),
    ]
    rows, coverage, warnings = build_daily_rows(2025, "USD", candidates)
    assert coverage["complete_year"] is True
    assert coverage["carry_gap_review_required"] is True
    assert coverage["trailing_carry_days"] == 184
    assert len(coverage["carry_gaps"]) == 2
    assert any("carried forward" in warning for warning in warnings)
    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "skill": "fbar-threshold-check",
        "status": "extracted-review-required",
        "tax_year": 2025,
        "account": {"account_id": "gap-a", "institution": "Self Test Bank", "currency": "USD", "account_number_hints": ["gap-a"]},
        "statement_files": [],
        "coverage": coverage,
        "fx": {"required": False, "workpaper_json": None, "workpaper": None},
        "warnings": warnings,
        "daily_ledger": rows,
        "artifacts": {},
    }
    extracted = root / "gap-a.json"
    confirmed = root / "gap-a-confirmed.json"
    write_json(extracted, data)
    gate_args = argparse.Namespace(
        input=str(extracted),
        out=str(confirmed),
        csv=None,
        balances_confirmed=True,
        fx_workpaper_json=None,
        accept_carry_forward=False,
    )
    try:
        command_confirm_account(gate_args)
    except FbarError as exc:
        assert "carry-forward" in str(exc)
    else:
        raise AssertionError("carry-forward gaps must block confirmation without --accept-carry-forward")
    gate_args.accept_carry_forward = True
    assert command_confirm_account(gate_args) == 0
    confirmed_data = load_json(confirmed)
    assert confirmed_data["confirmation"]["carry_forward_accepted"] is True  # type: ignore[index]
    assert any("accepted" in str(warning) for warning in confirmed_data["warnings"])  # type: ignore[union-attr]


def test_fx_guardrails(root: Path) -> None:
    year_end = make_year_end_fx_workpaper(root)
    year_end_data = validate_fx_workpaper(year_end, "COP", 2025)
    assert year_end_data["skill"] == "get-year-end-fx-rate"
    assert year_end_data["foreign_per_usd"] == "4200"

    compatible_yearly = make_fbar_fx_workpaper(root)
    data = validate_fx_workpaper(compatible_yearly, "COP", 2025)
    assert data["foreign_per_usd"] == "4000"

    invalid = make_yearly_average_workpaper(root)
    try:
        validate_fx_workpaper(invalid, "CAD", 2025)
    except FbarError as exc:
        assert "yearly-average" in str(exc) or "yearly-average" in exc.args[0]
    else:
        raise AssertionError("yearly-average-only workpaper should be rejected")

    bogus = root / "bogus-workpaper.json"
    write_json(bogus, {"skill": "some-other-skill", "currency": "COP", "year": 2025})
    try:
        validate_fx_workpaper(bogus, "COP", 2025)
    except FbarError as exc:
        assert "get-year-end-fx-rate" in str(exc)
    else:
        raise AssertionError("workpapers from unknown skills should be rejected")


def test_confirm_and_aggregate(root: Path) -> None:
    fx = make_year_end_fx_workpaper(root)
    usd = synthetic_account(root, "usd-a", 2025, "USD", Decimal("5000"), {"2025-06-01": Decimal("8000")})
    cop = synthetic_account(root, "cop-b", 2025, "COP", Decimal("4000000"), {"2025-06-01": Decimal("12000000")}, fx)
    out = root / "summary.json"
    command_aggregate(argparse.Namespace(account_ledger=[str(usd), str(cop)], out=str(out), csv=None, pdf=None))
    summary = load_json(out)
    assert summary["daily_threshold"]["answer"] == "yes"  # type: ignore[index]
    assert summary["daily_threshold"]["exceeded"] is True  # type: ignore[index]
    assert summary["daily_threshold"]["records_complete"] is True  # type: ignore[index]
    assert "2025-06-01" in summary["daily_threshold"]["over_limit_dates"]  # type: ignore[index]
    assert (root / "summary.csv").exists()
    assert (root / "summary.pdf").read_bytes().startswith(b"%PDF")


def test_maxima_disagreement(root: Path) -> None:
    a = synthetic_account(root, "max-a", 2025, "USD", Decimal("8000"), {"2025-01-02": Decimal("0")})
    b = synthetic_account(root, "max-b", 2025, "USD", Decimal("0"), {"2025-01-02": Decimal("8000"), "2025-01-03": Decimal("0")})
    out = root / "max-summary.json"
    command_aggregate(argparse.Namespace(account_ledger=[str(a), str(b)], out=str(out), csv=None, pdf=None))
    summary = load_json(out)
    assert summary["daily_threshold"]["answer"] == "no"  # type: ignore[index]
    assert summary["daily_threshold"]["exceeded"] is False  # type: ignore[index]
    assert summary["fincen_max_value_view"]["exceeded"] is True  # type: ignore[index]


def test_csv_naming() -> None:
    assert account_csv_path(Path("work/account-1.json")).name == "account-1-review.csv"
    assert account_csv_path(Path("work/account-1-confirmed.json"), confirmed=True).name == "account-1-confirmed.csv"
    assert account_csv_path(Path("work/account-1.json"), confirmed=True).name == "account-1-confirmed.csv"


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
    confirm.add_argument(
        "--fx-workpaper-json",
        help="FX workpaper JSON for non-USD accounts, from get-year-end-fx-rate (preferred) or FBAR-compatible get-yearly-fx-rate.",
    )
    confirm.add_argument(
        "--accept-carry-forward",
        action="store_true",
        help=f"Accept carry-forward gaps longer than {MAX_ROUTINE_CARRY_DAYS} days; use only after the user reviewed coverage.carry_gaps.",
    )
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
