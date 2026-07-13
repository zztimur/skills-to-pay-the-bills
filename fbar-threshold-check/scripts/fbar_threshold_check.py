#!/usr/bin/env python3
"""Build FBAR daily ledgers and aggregate threshold decisions."""

from __future__ import annotations

import argparse
import csv
import hashlib
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
ACCEPTED_FX_SKILLS = ("get-year-end-fx-rate",)
PREFLIGHT_SKILL = "statement-intake-preflight"
PREFLIGHT_SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1"}
PREFLIGHT_READY_STATUS = "ready-for-domain-extraction"
PREFLIGHT_REVIEW_REQUIRED_STATUS = "review-required"
PREFLIGHT_REVIEWED_HANDOFF_STATUS = "reviewed-for-domain-extraction"
PREFLIGHT_REVIEWED_HANDOFF_TYPE = "reviewed-handoff"

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
# A short date is accepted only at the start of a transaction row and only
# after a verified preflight period supplies its missing year.  Do not add a
# global DD/MM fallback: statements use both orders and a bare tax year is not
# enough source evidence to choose one.
SHORT_TRANSACTION_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})(?=\s|$)")

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
    re.compile(rf"\b({MONTH_RE})\.?(?:[\s-]+)(\d{{1,2}})(?:\s*,\s*|[\s-]+)(20\d{{2}})\b", re.I),
    re.compile(rf"\b(\d{{1,2}})(?:\s+de\s+|[-\s]+)({MONTH_RE})\.?(?:\s+de\s+|[-,\s]+)(20\d{{2}})\b", re.I),
)

# A period-end summary is not a transaction row.  Only accept the tightly
# labelled form below after the source-bound preflight period on the same page
# has been re-verified.  This deliberately excludes generic "final balance"
# language, transaction descriptions, and unanchored totals.
PERIOD_END_SUMMARY_RE = re.compile(
    r"\b(?:al\s+)?(?:final|cierre)\s+(?:del?|de\s+la)\s+per[ií]odo\b"
    r"|\b(?:period|statement)\s+(?:end|ending|closing)\b",
    re.I,
)

# Confirmed COP transaction tables can have this exact header.
# It is a narrow, source-bound parsing context - never infer it from a filename,
# institution name, a partial header, or an unconfirmed currency.
COP_TRANSACTION_TABLE_HEADER_RE = re.compile(
    r"^fecha\s+descripci[oó]n\s+movimiento\s+tarjeta\s+d[eé]bito\s+abono\s+saldo$", re.I
)
# The compact three-column form is deliberately recognised from PDF geometry,
# not flattened text. In this layout the transaction description can contain
# arbitrary numbers, so a "last numeric token" fallback is not safe enough to
# identify the final Saldo cell.
COMPACT_COP_TABLE_MIN_DATE_TO_DESCRIPTION_GAP = 50.0
COMPACT_COP_TABLE_MIN_DESCRIPTION_TO_SALDO_GAP = 100.0
COMPACT_COP_SALDO_COLUMN_TOLERANCE = 24.0
PDF_WORD_ROW_TOLERANCE = 1.5
ISO_TIMESTAMP_RE = re.compile(r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?\b")

# One monetary token. Whitespace bridges digit groups only in the explicit
# space-grouped form (French/NBSP thousands like "500 000" or "1 234,56"). A
# decimal point breaks the run of 3-digit groups, so decimal-bearing columns
# ("100.00 200.00 300.00") still tokenize separately; bare space-grouped
# integers are flagged as ambiguous in parse_amount for magnitude review.
MONEY_NUMBER = (
    r"(?:\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d{1,2})?"  # lakh grouping: 1,23,456
    r"|\d{1,3}(?:[.,'’]\d{3})+(?:[.,]\d{1,6})?"  # punct-grouped thousands: 1.234,56
    r"|[.,]\d{1,6}"  # unpadded decimal: .42 or ,42
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
    candidate_type: str = "transaction-row"
    period_end: date | None = None
    period_end_source: SourceRef | None = None


@dataclass(frozen=True)
class PagePeriodContext:
    """A preflight period whose source line was re-verified in the PDF."""

    start: date
    end: date
    source: SourceRef
    end_source: SourceRef


@dataclass(frozen=True)
class InferredShortDate:
    value: date
    note: str
    span: tuple[int, int]


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_institution_label(value: object) -> str:
    """Compare user-facing institution labels without accepting a different name."""
    return re.sub(r"[^\w]+", "", clean_text(value).casefold())


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


def parse_line_dates(line: str, inferred_short_date: InferredShortDate | None = None) -> list[tuple[date, str, str]]:
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

    if not dates and inferred_short_date is not None:
        dates.append((inferred_short_date.value, "medium", inferred_short_date.note))

    unique: dict[str, tuple[date, str, str]] = {}
    for item in dates:
        unique[item[0].isoformat()] = item
    return list(unique.values())


# Mask-only spans: two-digit-year numeric dates ("31/12/23") and clock times
# ("23:59", "23:59:00"). These are deliberately NOT parsed as balance dates -
# a two-digit year is ambiguous - but they must be blanked so their fragments
# ("31", "12", "23", "59") cannot be picked as a balance on a line that also
# carries a valid four-digit date. The trailing \b keeps the year group from
# matching inside a four-digit year or a 3+ digit money group.
MASK_ONLY_PATTERNS = (
    re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
)


def mask_date_spans(line: str, extra_spans: Iterable[tuple[int, int]] = ()) -> str:
    """Blank out date substrings so their fragments cannot parse as money.

    Uses '#' (not alphanumeric) so masking never bridges or blocks adjacent
    monetary tokens; character positions are preserved.
    """
    chars = list(line)
    for pattern in (*DATE_PATTERNS, *MONTH_DATE_PATTERNS, *MASK_ONLY_PATTERNS):
        for match in pattern.finditer(line):
            for index in range(match.start(), match.end()):
                chars[index] = "#"
    for start, end in extra_spans:
        for index in range(max(0, start), min(len(chars), end)):
            chars[index] = "#"
    return "".join(chars)


def infer_short_transaction_date(
    line: str, period_contexts: tuple[PagePeriodContext, ...], day_first: bool
) -> InferredShortDate | None:
    """Resolve an anchored short transaction date from one verified page period.

    A table value such as ``4/01`` has no year and may be DD/MM or MM/DD.  It
    is therefore useful only when one high-confidence preflight period was
    re-verified in the same PDF page.  Spanish ``DESDE ... HASTA`` pages use
    DD/MM; all other pages must yield exactly one in-period interpretation.
    """
    if len(period_contexts) != 1:
        return None
    match = SHORT_TRANSACTION_DATE_RE.search(line)
    if not match:
        return None

    first, second = int(match.group(1)), int(match.group(2))
    context = period_contexts[0]
    day_month_pairs = ((first, second),) if day_first else ((first, second), (second, first))
    candidates: set[date] = set()
    for day, month in day_month_pairs:
        for year in range(context.start.year, context.end.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if context.start <= candidate <= context.end:
                candidates.add(candidate)
    if len(candidates) != 1:
        return None

    value = next(iter(candidates))
    style = "DD/MM" if day_first else "short numeric"
    return InferredShortDate(
        value=value,
        note=(
            f"{style} date inferred from source-bound page statement period "
            f"{context.start.isoformat()} through {context.end.isoformat()}."
        ),
        span=(match.start(), match.end()),
    )


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


def is_confirmed_cop_table_header(line: str) -> bool:
    """Recognize only the exact text-layer header used by confirmed COP rows."""
    return bool(COP_TRANSACTION_TABLE_HEADER_RE.fullmatch(clean_text(line)))


def is_whole_cop_comma_grouped_token(token: str) -> bool:
    """Return true for an integer with one or more comma thousands groups."""
    normalized = re.sub(r"[A-Za-z$€£¥()\s'’+−-]", "", clean_text(token))
    return bool(re.fullmatch(r"\d{1,3}(?:,\d{3})+", normalized))


def visual_word_rows(words: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Group pdfplumber words into visual rows without retaining page text."""
    rows: list[list[dict[str, object]]] = []
    for word in sorted(words, key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0)))):
        if not rows or abs(float(word.get("top", 0)) - float(rows[-1][0].get("top", 0))) > PDF_WORD_ROW_TOLERANCE:
            rows.append([word])
        else:
            rows[-1].append(word)
    return rows


def compact_cop_table_header_columns(
    visual_rows: list[list[dict[str, object]]],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]] | None:
    """Return a trusted compact `Fecha | Descripción | Saldo` header only."""
    for row in visual_rows:
        by_label: dict[str, list[dict[str, object]]] = defaultdict(list)
        for word in row:
            by_label[clean_text(word.get("text")).casefold()].append(word)
        fechas = by_label.get("fecha", [])
        descriptions = [*by_label.get("descripción", []), *by_label.get("descripcion", [])]
        saldos = by_label.get("saldo", [])
        if len(fechas) != 1 or len(descriptions) != 1 or len(saldos) != 1:
            continue
        fecha, description, saldo = fechas[0], descriptions[0], saldos[0]
        fecha_x = float(fecha.get("x0", 0))
        description_x = float(description.get("x0", 0))
        saldo_x = float(saldo.get("x0", 0))
        if (
            fecha_x < description_x < saldo_x
            and description_x - fecha_x >= COMPACT_COP_TABLE_MIN_DATE_TO_DESCRIPTION_GAP
            and saldo_x - description_x >= COMPACT_COP_TABLE_MIN_DESCRIPTION_TO_SALDO_GAP
        ):
            return fecha, description, saldo
    return None


def compact_cop_amount_word(word: dict[str, object]) -> tuple[Decimal, str, tuple[str, ...]] | None:
    """Accept exactly one isolated monetary word in the trusted Saldo column."""
    raw = clean_text(word.get("text"))
    values = parse_money_values(raw)
    if len(values) != 1:
        return None
    amount, token, position, notes = values[0]
    if position != 0 or token != raw:
        return None
    return amount, token, notes


def compact_cop_source_ref(
    source_rows: list[tuple[SourceRef, str]],
    date_token: str,
    balance_token: str,
    used_line_numbers: set[int],
) -> SourceRef | None:
    """Bind a visual row back to one unused, source-text line on the page."""
    date_pattern = re.compile(rf"(?<!\d){re.escape(date_token)}(?!\d)")
    for ref, line in source_rows:
        if ref.line in used_line_numbers:
            continue
        if date_pattern.search(line) and balance_token in line:
            used_line_numbers.add(ref.line)
            return ref
    return None


def extract_compact_cop_table_candidates(
    pdf_paths: list[str],
    lines: list[tuple[SourceRef, str]],
    tax_year: int,
    currency: str,
    page_period_contexts: dict[tuple[str, int], tuple[PagePeriodContext, ...]],
    currency_confirmed: bool,
) -> tuple[list[BalanceCandidate], set[tuple[str, int]], list[str]]:
    """Extract only final Saldo cells from a reviewed, source-bound compact COP table.

    A compact page is handled only when the user confirmed COP, exactly one
    preflight period was re-verified on that page, the visual header has all
    three expected columns, each row has one left-column DD/MM date, and one
    monetary word aligns with the final Saldo header.  Any malformed row is
    omitted rather than falling back to an unanchored numeric token.
    """
    if currency != "COP" or not currency_confirmed:
        return [], set(), []
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-specific.
        raise FbarError("pdfplumber is required for extract-account. Install/use a runtime with pdfplumber.", 2) from exc

    source_rows_by_page: dict[tuple[str, int], list[tuple[SourceRef, str]]] = defaultdict(list)
    for ref, line in lines:
        source_rows_by_page[(normalize_preflight_path(ref.file), ref.page)].append((ref, line))

    candidates: list[BalanceCandidate] = []
    handled_pages: set[tuple[str, int]] = set()
    warnings: list[str] = []
    for raw_path in pdf_paths:
        path = Path(raw_path)
        try:
            with pdfplumber.open(str(path)) as pdf:
                for page_number, page in enumerate(pdf.pages, start=1):
                    page_key = (normalize_preflight_path(path), page_number)
                    contexts = page_period_contexts.get(page_key, ())
                    if len(contexts) != 1:
                        continue
                    visual_rows = visual_word_rows(page.extract_words(use_text_flow=True, keep_blank_chars=False))
                    header = compact_cop_table_header_columns(visual_rows)
                    if header is None:
                        continue
                    fecha_header, description_header, saldo_header = header
                    handled_pages.add(page_key)
                    header_top = float(fecha_header.get("top", 0))
                    date_column_right = (float(fecha_header.get("x0", 0)) + float(description_header.get("x0", 0))) / 2
                    saldo_column_left = float(saldo_header.get("x0", 0)) - COMPACT_COP_SALDO_COLUMN_TOLERANCE
                    source_rows = source_rows_by_page.get(page_key, [])
                    used_line_numbers: set[int] = set()
                    matched_rows = 0
                    omitted_rows = 0
                    for row in visual_rows:
                        if not row or float(row[0].get("top", 0)) <= header_top + PDF_WORD_ROW_TOLERANCE:
                            continue
                        date_words = [
                            word
                            for word in row
                            if float(word.get("x0", 0)) <= date_column_right
                            and SHORT_TRANSACTION_DATE_RE.fullmatch(clean_text(word.get("text")))
                        ]
                        if len(date_words) != 1:
                            continue
                        matched_rows += 1
                        date_token = clean_text(date_words[0].get("text"))
                        saldo_words = [
                            word
                            for word in row
                            if float(word.get("x0", 0)) >= saldo_column_left and compact_cop_amount_word(word) is not None
                        ]
                        if len(saldo_words) != 1:
                            omitted_rows += 1
                            continue
                        parsed_amount = compact_cop_amount_word(saldo_words[0])
                        if parsed_amount is None:  # defensive: filtered above
                            omitted_rows += 1
                            continue
                        amount, token, parse_notes = parsed_amount
                        inferred_date = infer_short_transaction_date(date_token, contexts, day_first=True)
                        if inferred_date is None or inferred_date.value.year != tax_year:
                            omitted_rows += 1
                            continue
                        source_ref = compact_cop_source_ref(source_rows, date_token, token, used_line_numbers)
                        if source_ref is None:
                            omitted_rows += 1
                            continue
                        contextual_cop_grouping = is_whole_cop_comma_grouped_token(token)
                        notes = [
                            "Recognized source-bound compact COP Fecha Descripción Saldo table by PDF columns; selected the final Saldo cell.",
                            inferred_date.note,
                        ]
                        if contextual_cop_grouping:
                            notes.append(
                                "Whole-COP comma grouping accepted only in the confirmed, source-bound compact COP Saldo table context."
                            )
                        else:
                            notes.extend(parse_notes)
                        candidates.append(
                            BalanceCandidate(
                                balance_date=inferred_date.value,
                                amount=amount,
                                currency=currency,
                                confidence="high" if not parse_notes or contextual_cop_grouping else "medium",
                                source=source_ref,
                                notes=tuple(notes),
                            )
                        )
                    if matched_rows == 0:
                        warnings.append(
                            f"Compact COP table on {path.name} page {page_number} had no source-bound DD/MM transaction rows."
                        )
                    elif omitted_rows:
                        warnings.append(
                            f"Compact COP table on {path.name} page {page_number} omitted {omitted_rows} of {matched_rows} DD/MM row(s) that could not be bound to one final Saldo cell and source line."
                        )
        except FbarError:
            raise
        except Exception as exc:
            raise FbarError(f"Could not read {path} as a PDF: {exc}", 2) from exc
    return candidates, handled_pages, warnings


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


def account_hint_token(raw: object) -> str | None:
    """Keep a numeric, masked, or IBAN-shaped account identifier only."""
    cleaned = clean_text(raw).strip(" .:-")
    if not cleaned:
        return None
    iban = re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9 ]{6,34}", cleaned, re.I)
    if iban:
        return re.sub(r"\s+", "", cleaned).upper()
    match = re.match(r"[A-Za-z]{0,4}[*Xx0-9](?:[*Xx0-9]|[ .\-](?=[*Xx0-9]))*", cleaned)
    if not match:
        return None
    token = match.group(0).strip(" .-")
    compact = re.sub(r"[ .\-]", "", token)
    digits = sum(char.isdigit() for char in compact)
    masks = sum(char in "*Xx" for char in compact)
    return token if digits + masks >= 4 else None


def stable_account_hints(values: Iterable[object]) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for raw in values:
        hint = account_hint_token(raw)
        if not hint:
            continue
        key = re.sub(r"[ .\-]", "", hint).upper()
        if key in seen:
            continue
        seen.add(key)
        hints.append(hint)
    return hints


def extract_account_hints(text: str) -> list[str]:
    hints: list[str] = []
    for pattern in ACCOUNT_PATTERNS:
        for match in pattern.finditer(text):
            hint = account_hint_token(match.group(1))
            if hint:
                hints.append(hint)
    return stable_account_hints(hints)


def selected_account_hints(preflight: dict[str, object], full_text: str) -> list[str]:
    """Prefer the source-bound preflight identity over local text heuristics."""
    preflight_hints = preflight.get("account_hints")
    if isinstance(preflight_hints, list):
        validated = stable_account_hints(preflight_hints)
        if validated:
            return validated
    return extract_account_hints(full_text)


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
    page_period_contexts: dict[tuple[str, int], tuple[PagePeriodContext, ...]] | None = None,
    currency_confirmed: bool = False,
    excluded_page_keys: set[tuple[str, int]] | None = None,
    external_candidate_count: int = 0,
) -> tuple[list[BalanceCandidate], list[str]]:
    warnings: list[str] = []
    candidates: list[BalanceCandidate] = []
    page_text: dict[tuple[str, int], str] = defaultdict(str)
    cop_table_pages: set[tuple[str, int]] = set()
    for ref, line in lines:
        page_text[(ref.file, ref.page)] += " " + line.lower()
        if is_confirmed_cop_table_header(line):
            cop_table_pages.add((normalize_preflight_path(ref.file), ref.page))

    outside_year = 0
    ambiguous_numeric_date_candidates = 0
    bare_small_amount_candidates = 0
    ambiguous_amount_lines = 0
    period_contexts = page_period_contexts or {}
    excluded_pages = excluded_page_keys or set()
    for ref, line in lines:
        lower = line.lower()
        page_key = (normalize_preflight_path(ref.file), ref.page)
        if page_key in excluded_pages:
            continue
        page_lower = page_text[(ref.file, ref.page)]
        inferred_short_date = infer_short_transaction_date(
            line,
            period_contexts.get(page_key, ()),
            day_first="desde" in page_lower and "hasta" in page_lower,
        )
        date_hits = parse_line_dates(line, inferred_short_date)
        if not date_hits:
            continue
        # Mask date substrings first so fragments like "31/12" or "31, 2023"
        # can never be selected as the day's balance.
        extra_spans = (inferred_short_date.span,) if inferred_short_date is not None else ()
        money_values = parse_money_values(mask_date_spans(line, extra_spans))
        if not money_values:
            continue

        confirmed_cop_table_row = (
            currency == "COP"
            and currency_confirmed
            and page_key in cop_table_pages
            and len(period_contexts.get(page_key, ())) == 1
            and ISO_TIMESTAMP_RE.search(line) is not None
        )
        has_balance_term = any(term in lower for term in BALANCE_TERMS)
        header_has_balance = any(term in page_lower for term in BALANCE_TERMS)
        table_minimum_amounts = 2 if inferred_short_date is not None else 3
        if not has_balance_term and not (header_has_balance and len(money_values) >= table_minimum_amounts):
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
        if confirmed_cop_table_row:
            # This exact table has currency-marked Abono and Saldo columns;
            # selecting the last marked token protects against numeric movement
            # identifiers before those columns.
            currency_marked_values = [item for item in selected_values if "$" in item[1]]
            if len(currency_marked_values) >= 2:
                selected_values = currency_marked_values
        amount, token, _pos, raw_parse_notes = selected_values[-1]
        contextual_cop_grouping = confirmed_cop_table_row and is_whole_cop_comma_grouped_token(token)
        parse_notes = () if contextual_cop_grouping else raw_parse_notes
        bare_small_int = re.fullmatch(r"[-−]?\d{1,2}[-−]?", token) is not None and not confirmed_cop_table_row
        if parse_notes:
            ambiguous_amount_lines += 1
        for parsed_date, date_confidence, date_note in date_hits:
            if parsed_date.year != tax_year:
                outside_year += 1
                continue
            confidence = "high" if (has_balance_term or confirmed_cop_table_row) and date_confidence == "high" else "medium"
            notes = [f"Selected last monetary value on line: {token}", date_note]
            if date_confidence == "low":
                confidence = "low"
                ambiguous_numeric_date_candidates += 1
            if not has_balance_term and not confirmed_cop_table_row:
                confidence = "medium" if confidence == "high" else confidence
                notes.append("Line inferred from a page/table containing balance language.")
            if confirmed_cop_table_row:
                notes.append(
                    "Recognized source-bound COP Fecha Descripción Movimiento Tarjeta Débito Abono Saldo table; "
                    "selected the final currency-marked Saldo value."
                )
            if contextual_cop_grouping:
                notes.append(
                    "Whole-COP comma grouping accepted only in the confirmed, source-bound COP Saldo table context."
                )
            if parse_notes:
                confidence = "medium" if confidence == "high" else confidence
                notes.extend(parse_notes)
            if bare_small_int:
                # A 1-2 digit integer with no separators is more likely a row
                # number or stray fragment than a balance; force review.
                confidence = "low"
                bare_small_amount_candidates += 1
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

    # A statement's labelled closing summary can provide an exact period-end
    # observation even when transaction rows stop earlier.  It must remain
    # distinct from a transaction-row candidate: require one re-verified page
    # period, one amount-bearing summary line, no line date, and an exact
    # statement-period end inside the requested year.  Do not use it to infer
    # intervening daily balances.
    for ref, line in lines:
        page_key = (normalize_preflight_path(ref.file), ref.page)
        contexts_for_page = period_contexts.get(page_key, ())
        if len(contexts_for_page) != 1:
            continue
        context = contexts_for_page[0]
        if context.end.year != tax_year or not PERIOD_END_SUMMARY_RE.search(line):
            continue
        if parse_line_dates(line):
            continue
        money_values = parse_money_values(mask_date_spans(line))
        if len(money_values) != 1:
            continue
        amount, token, _pos, parse_notes = money_values[0]
        notes = [
            f"Period-end summary amount: {token}",
            (
                "Source-bound period-end summary matched the verified statement period end "
                f"{context.end.isoformat()}; it does not evidence intervening days."
            ),
        ]
        if parse_notes:
            ambiguous_amount_lines += 1
            notes.extend(parse_notes)
        candidates.append(
            BalanceCandidate(
                balance_date=context.end,
                amount=amount,
                currency=currency,
                # Keep summaries reviewable even when their period/date source
                # binding is exact: they are not transaction-row balances.
                confidence="medium",
                source=ref,
                notes=tuple(notes),
                candidate_type="period-end-summary",
                period_end=context.end,
                period_end_source=context.end_source,
            )
        )

    if outside_year:
        warnings.append(f"Ignored {outside_year} balance candidate date(s) outside the requested tax year.")
    if ambiguous_numeric_date_candidates:
        warnings.append(
            f"{ambiguous_numeric_date_candidates} balance candidate date(s) used ambiguous numeric date interpretation."
        )
    if bare_small_amount_candidates:
        warnings.append(
            f"{bare_small_amount_candidates} balance candidate(s) selected a bare 1-2 digit amount and need review."
        )
    if ambiguous_amount_lines:
        warnings.append(
            f"{ambiguous_amount_lines} balance line(s) had ambiguous thousands/decimal separators; verify native balance magnitudes in the review CSV."
        )
    if not candidates and not external_candidate_count:
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
    transaction_observed_days = 0
    period_end_summary_days = 0
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
            review_flags: list[dict[str, object]] = []
            for summary_candidate in day_candidates:
                if summary_candidate.candidate_type != "period-end-summary":
                    continue
                review_flags.append(
                    {
                        "code": "period-end-summary",
                        "matched_period_end": iso_day(summary_candidate.period_end or day),
                        "summary_source_ref": source_ref_to_string(summary_candidate.source),
                        "period_end_source_ref": source_ref_to_string(summary_candidate.period_end_source),
                    }
                )
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
                    review_flags.append(
                        {
                            "code": "material-same-day-balance-candidates",
                            "candidate_count": len(day_candidates),
                            "minimum_native_balance": fmt_native(low_amount),
                            "maximum_native_balance": fmt_native(high_amount),
                            "selected_native_balance": fmt_native(selected.amount),
                            "selected_source_ref": source_ref_to_string(selected.source),
                            "candidates": [
                                {
                                    "native_balance": fmt_native(item.amount),
                                    "source_ref": source_ref_to_string(item.source),
                                    "candidate_type": item.candidate_type,
                                }
                                for item in sorted(
                                    day_candidates,
                                    key=lambda item: (item.amount, source_ref_to_string(item.source)),
                                )
                            ],
                        }
                    )
            last_balance = selected.amount
            last_ref = selected.source
            if first_observed is None:
                first_observed = day
            last_observed = day
            observed_days += 1
            if selected.candidate_type == "period-end-summary":
                period_end_summary_days += 1
            else:
                transaction_observed_days += 1
            if confidence == "low":
                low_confidence_days += 1
            source_refs = [source_ref_to_string(selected.source)]
            if selected.candidate_type == "period-end-summary":
                end_source_ref = source_ref_to_string(selected.period_end_source)
                if end_source_ref and end_source_ref not in source_refs:
                    source_refs.append(end_source_ref)
            rows.append(
                {
                    "date": iso_day(day),
                    "currency": currency,
                    "native_balance": fmt_native(selected.amount),
                    "usd_balance": None,
                    "threshold_usd_value": None,
                    "balance_source": "period-end-summary" if selected.candidate_type == "period-end-summary" else "observed",
                    "evidence_class": "period-end-summary" if selected.candidate_type == "period-end-summary" else "transaction",
                    "confidence": confidence,
                    "source_refs": source_refs,
                    "notes": notes,
                    "review_flags": review_flags,
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
                    "evidence_class": "carried-forward",
                    "confidence": "medium",
                    "source_refs": [source_ref_to_string(last_ref)] if last_ref else [],
                    "notes": ["No same-day balance found; carried forward most recent observed account balance."],
                    "review_flags": [],
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
                    "evidence_class": "missing-opening",
                    "confidence": "missing",
                    "source_refs": [],
                    "notes": ["No opening or prior balance was available for this day."],
                    "review_flags": [],
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
        # Keep observed_days for compatibility. These two selected-source
        # counts partition it so sparse period-end summaries cannot look like
        # equivalent transaction-row evidence in a review artifact.
        "observed_days": observed_days,
        "transaction_observed_days": transaction_observed_days,
        "period_end_summary_days": period_end_summary_days,
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


def build_data_sufficiency(coverage: dict[str, object]) -> dict[str, object]:
    """Describe whether extracted evidence can support a later threshold review.

    This is deliberately not a threshold decision and cannot replace the
    confirmation gate. It makes sparse period-end-only statements explicit so
    a reviewer cannot mistake a displayed zero balance for an annual maximum.
    """
    observed_days = as_int(coverage.get("observed_days", 0), "coverage.observed_days")
    transaction_observed_days = as_int(
        coverage.get("transaction_observed_days", 0), "coverage.transaction_observed_days"
    )
    period_end_summary_days = as_int(
        coverage.get("period_end_summary_days", 0), "coverage.period_end_summary_days"
    )
    missing_days = as_int(coverage.get("missing_days", 0), "coverage.missing_days")
    carry_gaps = coverage.get("carry_gaps") if isinstance(coverage.get("carry_gaps"), list) else []

    if transaction_observed_days and period_end_summary_days:
        evidence_profile = "mixed-transaction-and-period-end"
    elif transaction_observed_days:
        evidence_profile = "transaction-rows"
    elif period_end_summary_days:
        evidence_profile = "period-end-only"
    else:
        evidence_profile = "no-balance-observations"

    reason_codes: list[str] = []
    if evidence_profile == "period-end-only":
        reason_codes.append("period-end-only")
    if observed_days == 0:
        reason_codes.append("no-balance-observations")
    if missing_days:
        reason_codes.append("missing-opening-coverage")
    if carry_gaps:
        reason_codes.append("long-carry-forward-gap")

    records_insufficient = bool(missing_days or carry_gaps or observed_days == 0)
    return {
        "evidence_profile": evidence_profile,
        "daily_threshold": {
            "answer": "insufficient-records" if records_insufficient else "review-required",
            "reason_codes": reason_codes,
        },
        "maximum_account_value": {
            "answer": "not-determinable" if records_insufficient else "review-required",
            "reason_codes": reason_codes,
        },
    }


def source_ref_to_string(ref: SourceRef | None) -> str:
    if ref is None:
        return ""
    return f"{Path(ref.file).name}:p{ref.page}:l{ref.line}"


def build_review_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    """Return a compact, deterministic card for rows that need human review."""
    same_day_items: list[dict[str, object]] = []
    period_end_items: list[dict[str, object]] = []
    for row in rows:
        flags = row.get("review_flags")
        if not isinstance(flags, list) or not flags:
            continue
        same_day_flags = [
            flag
            for flag in flags
            if isinstance(flag, dict) and flag.get("code") == "material-same-day-balance-candidates"
        ]
        if same_day_flags:
            same_day_items.append({"date": row.get("date"), "flags": same_day_flags})
        for flag in flags:
            if isinstance(flag, dict) and flag.get("code") == "period-end-summary":
                period_end_items.append({"date": row.get("date"), "flag": flag})
    return {
        "same_day_balance_candidates": {
            "count": len(same_day_items),
            "requires_user_review": bool(same_day_items),
            "items": same_day_items,
        },
        "period_end_summaries": {
            "count": len(period_end_items),
            "requires_user_review": bool(period_end_items),
            "items": period_end_items,
        },
    }


def format_review_flags(flags: object) -> str:
    if not isinstance(flags, list):
        return ""
    summaries: list[str] = []
    for flag in flags:
        if not isinstance(flag, dict):
            continue
        if flag.get("code") == "material-same-day-balance-candidates":
            summaries.append(
                "same-day candidates "
                f"(count={flag.get('candidate_count')}; "
                f"selected={flag.get('selected_native_balance')}; "
                f"range={flag.get('minimum_native_balance')}..{flag.get('maximum_native_balance')}; "
                f"source={flag.get('selected_source_ref')})"
            )
        elif flag.get("code") == "period-end-summary":
            summaries.append(
                "period-end summary "
                f"(period_end={flag.get('matched_period_end')}; "
                f"source={flag.get('summary_source_ref')}; "
                f"period_end_source={flag.get('period_end_source_ref')})"
            )
        else:
            summaries.append(str(flag.get("code") or "review flag"))
    return " | ".join(summaries)


def print_same_day_candidate_review_card(account_data: dict[str, object]) -> None:
    summary = account_data.get("review_summary")
    if not isinstance(summary, dict):
        return
    same_day = summary.get("same_day_balance_candidates")
    if not isinstance(same_day, dict):
        return
    items = same_day.get("items")
    if not isinstance(items, list) or not items:
        return
    print(
        f"Same-day candidate review: {len(items)} date(s) need user review. "
        "Detailed values and source references are in review_summary and the review CSV."
    )


def print_period_end_summary_review_card(account_data: dict[str, object]) -> None:
    summary = account_data.get("review_summary")
    if not isinstance(summary, dict):
        return
    period_end = summary.get("period_end_summaries")
    if not isinstance(period_end, dict):
        return
    items = period_end.get("items")
    if not isinstance(items, list) or not items:
        return
    print(
        f"Period-end summary review: {len(items)} source-bound summary observation(s) need user review. "
        "They are exact period-end observations only, not daily coverage or an annual maximum; "
        "exact period ends and source references are in review_summary and the review CSV."
    )


def print_data_sufficiency_review_card(account_data: dict[str, object]) -> None:
    sufficiency = account_data.get("data_sufficiency")
    if not isinstance(sufficiency, dict):
        return
    daily = sufficiency.get("daily_threshold")
    maximum = sufficiency.get("maximum_account_value")
    if not isinstance(daily, dict) or not isinstance(maximum, dict):
        return
    if daily.get("answer") != "insufficient-records" and maximum.get("answer") != "not-determinable":
        return
    print(
        "Evidence sufficiency: "
        f"profile={sufficiency.get('evidence_profile')}; "
        f"daily threshold={daily.get('answer')}; "
        f"maximum account value={maximum.get('answer')}. "
        "Do not treat period-end observations or carried balances as an annual maximum."
    )


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
        "evidence_class",
        "confidence",
        "source_refs",
        "notes",
        "review_flags",
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
                    "evidence_class": row.get("evidence_class"),
                    "confidence": row.get("confidence"),
                    "source_refs": "; ".join(row.get("source_refs", []) if isinstance(row.get("source_refs"), list) else []),
                    "notes": "; ".join(row.get("notes", []) if isinstance(row.get("notes"), list) else []),
                    "review_flags": format_review_flags(row.get("review_flags")),
                }
            )


def normalize_preflight_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def preflight_page_period_contexts(
    preflight: dict[str, object], lines: list[tuple[SourceRef, str]]
) -> dict[tuple[str, int], tuple[PagePeriodContext, ...]]:
    """Return only preflight periods whose source line still matches the PDF.

    Period hints are useful for restoring a missing transaction-row year, but
    they must not become an independent source of truth. Require the exact
    preflight source line to still contain both recorded dates, or for a
    narrow split-range handoff, require each endpoint's retained source line
    to contain its recorded date after the extraction-time fingerprint check.
    """
    source_lines = {
        (normalize_preflight_path(ref.file), ref.page, ref.line): (ref, text) for ref, text in lines
    }
    raw_files = preflight.get("statement_files")
    if not isinstance(raw_files, list):
        return {}

    contexts: dict[tuple[str, int], list[PagePeriodContext]] = defaultdict(list)
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            continue
        source_path = raw_file.get("resolved_file") or raw_file.get("file")
        if not source_path:
            continue
        normalized_path = normalize_preflight_path(str(source_path))
        raw_periods = raw_file.get("period_intervals")
        if not isinstance(raw_periods, list):
            continue
        for raw_period in raw_periods:
            if not isinstance(raw_period, dict) or str(raw_period.get("confidence")) != "high":
                continue
            source_ref = raw_period.get("source_ref")
            if not isinstance(source_ref, dict):
                continue
            try:
                start = date.fromisoformat(str(raw_period.get("start")))
                end = date.fromisoformat(str(raw_period.get("end")))
                page = int(source_ref.get("page"))
                line_number = int(source_ref.get("line"))
            except (TypeError, ValueError):
                continue
            if end < start or page <= 0 or line_number <= 0:
                continue
            source_item = source_lines.get((normalized_path, page, line_number))
            if not source_item:
                continue
            source_ref, source_line = source_item
            end_source_ref = raw_period.get("end_source_ref")
            if end_source_ref is None:
                verified_dates = {parsed_date for parsed_date, _confidence, _note in parse_line_dates(source_line)}
                if start not in verified_dates or end not in verified_dates:
                    continue
                end_ref = source_ref
            elif isinstance(end_source_ref, dict):
                try:
                    end_page = int(end_source_ref.get("page"))
                    end_line_number = int(end_source_ref.get("line"))
                except (TypeError, ValueError):
                    continue
                if end_page != page or end_line_number <= 0:
                    continue
                end_source_item = source_lines.get((normalized_path, end_page, end_line_number))
                if not end_source_item:
                    continue
                end_ref, end_source_line = end_source_item
                start_dates = {parsed_date for parsed_date, _confidence, _note in parse_line_dates(source_line)}
                end_dates = {parsed_date for parsed_date, _confidence, _note in parse_line_dates(end_source_line)}
                if start not in start_dates or end not in end_dates:
                    continue
            else:
                continue
            key = (normalized_path, page)
            context = PagePeriodContext(start=start, end=end, source=source_ref, end_source=end_ref)
            if context not in contexts[key]:
                contexts[key].append(context)
    return {key: tuple(value) for key, value in contexts.items()}


def preflight_file_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise FbarError(f"Could not hash file {path}: {exc}", 2) from exc


def preflight_file_bytes(path: Path, label: str) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        raise FbarError(f"Could not stat {label} {path}: {exc}", 2) from exc


def read_preflight_json(path: Path, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FbarError(f"Could not read {label} {path}: {exc}", 2) from exc
    except json.JSONDecodeError as exc:
        raise FbarError(f"{label.capitalize()} {path} is not valid JSON: {exc}", 2) from exc
    if not isinstance(data, dict):
        raise FbarError(f"{label.capitalize()} {path} must contain a JSON object.", 2)
    return data


def preflight_statement_fingerprints(data: dict[str, object]) -> list[dict[str, object]]:
    statement_files = data.get("statement_files")
    if not isinstance(statement_files, list) or not statement_files:
        raise FbarError("Preflight JSON has no statement_files list.", 2)
    fingerprints: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    seen_digests: set[str] = set()
    for index, item in enumerate(statement_files, start=1):
        if not isinstance(item, dict):
            raise FbarError(f"Preflight statement_files entry {index} is not an object.", 2)
        source_path = item.get("resolved_file") or item.get("file")
        if not source_path:
            raise FbarError(f"Preflight statement_files entry {index} has no file path.", 2)
        normalized_path = normalize_preflight_path(str(source_path))
        if normalized_path in seen_paths:
            raise FbarError("Preflight contains duplicate statement paths; remove duplicates and rerun preflight.", 2)
        seen_paths.add(normalized_path)
        digest = str(item.get("content_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise FbarError(
                f"Preflight statement {Path(normalized_path).name} lacks a valid SHA-256 fingerprint; rerun preflight.",
                2,
            )
        if digest in seen_digests:
            raise FbarError("Preflight contains byte-identical statement PDFs; remove duplicates and rerun preflight.", 2)
        seen_digests.add(digest)
        try:
            content_bytes = int(str(item.get("content_bytes")))
        except (TypeError, ValueError) as exc:
            raise FbarError(
                f"Preflight statement {Path(normalized_path).name} lacks a valid byte-size fingerprint; rerun preflight.",
                2,
            ) from exc
        if content_bytes <= 0:
            raise FbarError(
                f"Preflight statement {Path(normalized_path).name} has a non-positive byte-size fingerprint; rerun preflight.",
                2,
            )
        fingerprints.append(
            {
                "resolved_file": normalized_path,
                "content_sha256": digest,
                "content_bytes": content_bytes,
            }
        )
    return fingerprints


def verify_preflight_statement_fingerprints(data: dict[str, object], pdf_paths: list[str]) -> list[dict[str, object]]:
    expected_paths = [normalize_preflight_path(path_item) for path_item in pdf_paths]
    if len(set(expected_paths)) != len(expected_paths):
        raise FbarError("Duplicate statement PDFs were supplied to extraction; remove duplicates and rerun preflight.", 2)
    fingerprints = preflight_statement_fingerprints(data)
    actual_paths = [str(item["resolved_file"]) for item in fingerprints]
    if expected_paths != actual_paths:
        raise FbarError(
            "Preflight PDF sequence does not match extraction PDFs. "
            f"Expected {expected_paths}; preflight has {actual_paths}.",
            2,
        )
    for expected, fingerprint in zip(expected_paths, fingerprints, strict=True):
        current_path = Path(expected)
        current_bytes = preflight_file_bytes(current_path, "statement PDF")
        current_digest = preflight_file_sha256(current_path)
        if current_bytes != fingerprint["content_bytes"] or current_digest != fingerprint["content_sha256"]:
            raise FbarError(
                f"Statement PDF {current_path.name} changed after preflight; rerun preflight and complete review again.",
                2,
            )
    return fingerprints


def validate_preflight_identity(
    data: dict[str, object], expected_scope: str, tax_year: int, pdf_paths: list[str]
) -> list[dict[str, object]]:
    if data.get("skill") != PREFLIGHT_SKILL:
        raise FbarError(f"Preflight JSON must come from {PREFLIGHT_SKILL}.", 2)
    if str(data.get("schema_version", "")) not in PREFLIGHT_SUPPORTED_SCHEMA_VERSIONS:
        raise FbarError(f"Unsupported preflight schema_version {data.get('schema_version')!r}.", 2)
    if as_int(data.get("tax_year", 0), "preflight.tax_year") != tax_year:
        raise FbarError(f"Preflight tax_year {data.get('tax_year')} does not match extraction tax year {tax_year}.", 2)
    if data.get("scope") != expected_scope:
        raise FbarError(f"Preflight scope {data.get('scope')!r} does not match required scope {expected_scope!r}.", 2)

    return verify_preflight_statement_fingerprints(data, pdf_paths)


def validated_review_gates(data: dict[str, object]) -> list[dict[str, str]]:
    raw_gates = data.get("review_gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise FbarError("review-required preflight has no review_gates; rerun preflight.", 2)
    gates: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for raw_gate in raw_gates:
        if not isinstance(raw_gate, dict):
            raise FbarError("Preflight review_gates must contain objects; rerun preflight.", 2)
        code = str(raw_gate.get("code") or "").strip()
        severity = str(raw_gate.get("severity") or "").strip()
        message = str(raw_gate.get("message") or "").strip()
        if not code or not severity or not message:
            raise FbarError("Every preflight review gate needs code, severity, and message; rerun preflight.", 2)
        if code in seen_codes:
            raise FbarError(f"Preflight has duplicate review gate code {code!r}; rerun preflight.", 2)
        seen_codes.add(code)
        gates.append({"code": code, "severity": severity, "message": message})
    return gates


def reviewed_resolution_object(resolutions: dict[str, object], key: str) -> dict[str, object]:
    value = resolutions.get(key)
    if not isinstance(value, dict):
        raise FbarError(f"Reviewed handoff user_resolutions.{key} must be an object.", 2)
    return value


def reviewed_resolution_gate_codes(resolution: dict[str, object], expected: set[str], key: str) -> None:
    raw_codes = resolution.get("resolved_gate_codes")
    if not isinstance(raw_codes, list):
        raise FbarError(f"Reviewed handoff user_resolutions.{key}.resolved_gate_codes must be a list.", 2)
    codes = [str(code).strip() for code in raw_codes if str(code).strip()]
    if len(codes) != len(raw_codes) or len(set(codes)) != len(codes) or set(codes) != expected:
        raise FbarError(
            f"Reviewed handoff user_resolutions.{key}.resolved_gate_codes must exactly match the source review gates.",
            2,
        )


def preflight_requires_institution(source: dict[str, object]) -> bool:
    requirements = source.get("requirements")
    return isinstance(requirements, dict) and requirements.get("institution_required") is True


def validate_reviewed_user_resolutions(
    handoff: dict[str, object], source: dict[str, object], source_sha256: str, gates: list[dict[str, str]]
) -> dict[str, object]:
    """Accept only structured, source-bound reviewer resolutions.

    The reviewed handoff must never turn a bare currency symbol or an absent
    account number into an implicit extraction decision.  These fields are
    optional only for legacy reviewed handoffs with unrelated review gates.
    """
    gate_codes = {gate["code"] for gate in gates}
    currency_gate_codes = {"ambiguous-dollar", "unknown-currency"} & gate_codes
    account_gate_codes = {"unknown-account", "possible-mixed-accounts"} & gate_codes
    year_gate_codes = {"mixed-years", "unresolved-year-evidence", "unknown-year-coverage"} & gate_codes
    institution_gate_codes = {"unknown-institution", "possible-mixed-institutions"} & gate_codes
    raw_resolutions = handoff.get("user_resolutions")
    if raw_resolutions is None:
        if currency_gate_codes or account_gate_codes or year_gate_codes or institution_gate_codes:
            raise FbarError(
                "Reviewed handoff is missing structured user resolutions for its currency, account, year, or institution review gates.",
                2,
            )
        return {}
    if not isinstance(raw_resolutions, dict):
        raise FbarError("Reviewed handoff user_resolutions must be an object.", 2)
    resolutions = raw_resolutions
    if str(resolutions.get("source_preflight_sha256") or "") != source_sha256:
        raise FbarError("Reviewed handoff user resolutions are not bound to the reviewed source preflight SHA-256.", 2)

    try:
        tax_year = as_int(source.get("tax_year", 0), "source preflight tax_year")
    except FbarError:
        raise FbarError("Reviewed handoff source preflight has an invalid tax year.", 2) from None

    statement_years = reviewed_resolution_object(resolutions, "statement_years")
    if "mixed-years" in year_gate_codes:
        raise FbarError(
            "Reviewed handoff cannot resolve mixed-years; rerun preflight with the corrected tax year or statement set.",
            2,
        )
    if year_gate_codes:
        if statement_years.get("status") != "user-confirmed":
            raise FbarError("Reviewed handoff does not contain a user-confirmed statement-year resolution.", 2)
        raw_years = statement_years.get("confirmed_years")
        if not isinstance(raw_years, list):
            raise FbarError("Reviewed handoff statement-year resolution must list confirmed_years.", 2)
        try:
            confirmed_years = sorted({int(year) for year in raw_years})
        except (TypeError, ValueError) as exc:
            raise FbarError("Reviewed handoff statement-year resolution contains an invalid year.", 2) from exc
        if confirmed_years != [tax_year]:
            raise FbarError(
                f"Reviewed handoff statement-year resolution must confirm only the extraction year {tax_year}.",
                2,
            )
    elif statement_years.get("status") != "not-required":
        raise FbarError("Reviewed handoff has an unexpected statement-year resolution.", 2)

    currency = reviewed_resolution_object(resolutions, "currency")
    source_currency = source.get("currency") if isinstance(source.get("currency"), dict) else {}
    if currency_gate_codes:
        if str(source_currency.get("code") or "").upper() != "UNKNOWN":
            raise FbarError("Reviewed handoff currency resolution is only valid when source preflight currency is UNKNOWN.", 2)
        if currency.get("status") != "user-confirmed":
            raise FbarError("Reviewed handoff does not contain a user-confirmed currency resolution.", 2)
        reviewed_resolution_gate_codes(currency, currency_gate_codes, "currency")
        confirmed_currency = str(currency.get("code") or "").strip().upper()
        if confirmed_currency not in CURRENCY_CODES:
            raise FbarError("Reviewed handoff currency resolution must use a supported ISO currency code.", 2)
        if str(currency.get("source_currency_code") or "").upper() != "UNKNOWN":
            raise FbarError("Reviewed handoff currency resolution does not preserve the source UNKNOWN currency state.", 2)
    elif currency.get("status") != "not-required":
        raise FbarError("Reviewed handoff has an unexpected currency resolution.", 2)

    one_account = reviewed_resolution_object(resolutions, "one_account")
    if account_gate_codes:
        if one_account.get("status") != "user-confirmed" or one_account.get("confirmed") is not True:
            raise FbarError("Reviewed handoff does not contain a user-confirmed one-account resolution.", 2)
        if one_account.get("account_identifier_provided") is not False:
            raise FbarError("Reviewed handoff one-account resolution must not claim an unverified account identifier.", 2)
        reviewed_resolution_gate_codes(one_account, account_gate_codes, "one_account")
    elif one_account.get("status") != "not-required":
        raise FbarError("Reviewed handoff has an unexpected one-account resolution.", 2)

    institution = reviewed_resolution_object(resolutions, "institution")
    if institution_gate_codes:
        if source.get("scope") != "one-account" or not preflight_requires_institution(source):
            raise FbarError(
                "Reviewed institution resolution is valid only for a one-account preflight run with --require-institution.",
                2,
            )
        if institution.get("status") != "user-confirmed":
            raise FbarError("Reviewed handoff does not contain a user-confirmed institution resolution.", 2)
        reviewed_resolution_gate_codes(institution, institution_gate_codes, "institution")
        confirmed_institution = clean_text(institution.get("name"))
        if not confirmed_institution or len(confirmed_institution) > 160:
            raise FbarError("Reviewed handoff institution resolution must contain a non-empty name of at most 160 characters.", 2)
    elif institution.get("status") != "not-required":
        raise FbarError("Reviewed handoff has an unexpected institution resolution.", 2)

    return resolutions


def resolve_reviewed_handoff(handoff: dict[str, object], handoff_path: Path) -> dict[str, object]:
    if handoff.get("skill") != PREFLIGHT_SKILL:
        raise FbarError(f"Reviewed handoff must come from {PREFLIGHT_SKILL}.", 2)
    if str(handoff.get("schema_version", "")) not in PREFLIGHT_SUPPORTED_SCHEMA_VERSIONS:
        raise FbarError(f"Unsupported reviewed-handoff schema_version {handoff.get('schema_version')!r}.", 2)
    if handoff.get("artifact_type") != PREFLIGHT_REVIEWED_HANDOFF_TYPE:
        raise FbarError("Preflight JSON is neither a ready preflight nor a reviewed-handoff artifact.", 2)
    if handoff.get("status") != PREFLIGHT_REVIEWED_HANDOFF_STATUS:
        raise FbarError("Reviewed handoff is not marked reviewed-for-domain-extraction.", 2)

    source_meta = handoff.get("source_preflight")
    review = handoff.get("review")
    if not isinstance(source_meta, dict) or not isinstance(review, dict):
        raise FbarError("Reviewed handoff is missing source_preflight or review metadata.", 2)
    if review.get("user_review_confirmed") is not True:
        raise FbarError("Reviewed handoff does not record explicit user review confirmation.", 2)
    source_path_value = source_meta.get("resolved_path") or source_meta.get("path")
    if not source_path_value:
        raise FbarError("Reviewed handoff has no source preflight path.", 2)
    source_path = Path(str(source_path_value))
    if normalize_preflight_path(source_path) == normalize_preflight_path(handoff_path):
        raise FbarError("Reviewed handoff must preserve a separate source preflight JSON.", 2)
    expected_source_hash = str(source_meta.get("sha256") or "")
    if not expected_source_hash:
        raise FbarError("Reviewed handoff has no source preflight SHA-256.", 2)
    if preflight_file_sha256(source_path) != expected_source_hash:
        raise FbarError("Source preflight changed after review; rerun preflight and create a new reviewed handoff.", 2)
    source = read_preflight_json(source_path, "source preflight JSON")
    if source.get("status") != PREFLIGHT_REVIEW_REQUIRED_STATUS:
        raise FbarError("Reviewed handoff source must retain status review-required.", 2)

    for key in ("schema_version", "tax_year", "scope", "requirements", "statement_files", "review_gates"):
        if handoff.get(key) != source.get(key):
            raise FbarError(f"Reviewed handoff {key} does not match its source preflight.", 2)
    for key in ("schema_version", "tax_year", "scope", "requirements", "status"):
        if source_meta.get(key) != source.get(key):
            raise FbarError(f"Reviewed handoff source_preflight.{key} does not match its source preflight.", 2)

    gates = validated_review_gates(source)
    stop_codes = sorted(gate["code"] for gate in gates if gate["severity"] == "stop")
    if stop_codes:
        raise FbarError(
            "Reviewed handoff cannot accept structural stop gate(s): " + ", ".join(stop_codes) + ".",
            2,
        )
    if any(gate["severity"] != "review" for gate in gates):
        raise FbarError("Reviewed handoff source has unsupported review-gate severity; rerun preflight.", 2)
    accepted_codes_raw = review.get("accepted_gate_codes")
    if not isinstance(accepted_codes_raw, list):
        raise FbarError("Reviewed handoff has no accepted_gate_codes list.", 2)
    accepted_codes = [str(code).strip() for code in accepted_codes_raw if str(code).strip()]
    if len(set(accepted_codes)) != len(accepted_codes):
        raise FbarError("Reviewed handoff has duplicate accepted gate codes.", 2)
    expected_codes = {gate["code"] for gate in gates}
    if set(accepted_codes) != expected_codes:
        raise FbarError("Reviewed handoff must accept every and only the source preflight review gates.", 2)
    user_resolutions = validate_reviewed_user_resolutions(handoff, source, expected_source_hash, gates)

    resolved = dict(source)
    resolved["status"] = PREFLIGHT_REVIEWED_HANDOFF_STATUS
    resolved["review_handoff"] = {
        "artifact_json": str(handoff_path),
        "source_preflight_json": str(source_path),
        "source_preflight_sha256": expected_source_hash,
        "accepted_gate_codes": sorted(accepted_codes),
        "confirmed_at": review.get("confirmed_at"),
    }
    if user_resolutions:
        resolved["user_resolutions"] = user_resolutions
    return resolved


def load_preflight_json(path: str | None, expected_scope: str, tax_year: int, pdf_paths: list[str]) -> dict[str, object]:
    if not path:
        raise FbarError("--preflight-json is required. Run statement-intake-preflight before extracting an FBAR ledger.", 2)
    preflight_path = Path(path)
    data = read_preflight_json(preflight_path, "preflight JSON")
    if data.get("status") == PREFLIGHT_REVIEWED_HANDOFF_STATUS:
        data = resolve_reviewed_handoff(data, preflight_path)
    elif data.get("status") == PREFLIGHT_REVIEW_REQUIRED_STATUS:
        raise FbarError(
            "Preflight is review-required. Resolve its structural issues or create a reviewed handoff after explicit user review.",
            2,
        )
    elif data.get("status") != PREFLIGHT_READY_STATUS:
        raise FbarError(f"Unsupported preflight status {data.get('status')!r}.", 2)
    elif data.get("review_gates"):
        raise FbarError("Ready preflight unexpectedly contains review gates; rerun preflight.", 2)

    verified_files = validate_preflight_identity(data, expected_scope, tax_year, pdf_paths)
    data = dict(data)
    data["verified_statement_files"] = verified_files
    return data


def preflight_warning_lines(preflight: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for warning in preflight.get("warnings", []):
        if isinstance(warning, str) and warning.strip():
            lines.append(f"Preflight: {warning.strip()}")
    for gate in preflight.get("review_gates", []):
        if isinstance(gate, dict):
            code = str(gate.get("code") or "review-gate")
            message = str(gate.get("message") or "").strip()
            if message:
                lines.append(f"Preflight gate {code}: {message}")
    handoff = preflight.get("review_handoff")
    if isinstance(handoff, dict):
        accepted = handoff.get("accepted_gate_codes")
        if isinstance(accepted, list) and accepted:
            lines.append("Preflight reviewed handoff accepted gate(s): " + ", ".join(str(code) for code in accepted))
    return sorted(set(lines))


def preflight_profile(preflight: dict[str, object], source_path: str) -> dict[str, object]:
    return {
        "source_json": source_path,
        "status": preflight.get("status"),
        "scope": preflight.get("scope"),
        "requirements": preflight.get("requirements"),
        "currency": preflight.get("currency"),
        "profile": preflight.get("profile"),
        "coverage_hints": preflight.get("coverage_hints"),
        "review_gates": preflight.get("review_gates", []),
        "review_csv": (preflight.get("artifacts") or {}).get("review_csv") if isinstance(preflight.get("artifacts"), dict) else None,
        "review_handoff": preflight.get("review_handoff"),
        "user_resolutions": preflight.get("user_resolutions"),
        "verified_statement_files": preflight.get("verified_statement_files", []),
    }


def resolve_institution(
    preflight: dict[str, object], user_resolutions: dict[str, object], requested_institution: str | None
) -> str | None:
    """Use a reviewed typed issuer when present, otherwise source preflight evidence."""
    reviewed = user_resolutions.get("institution")
    if isinstance(reviewed, dict) and reviewed.get("status") == "user-confirmed":
        confirmed = clean_text(reviewed.get("name"))
        if requested_institution and normalized_institution_label(requested_institution) != normalized_institution_label(confirmed):
            raise FbarError(
                "--institution conflicts with the user-confirmed institution in the reviewed preflight handoff.",
                2,
            )
        return confirmed
    if requested_institution:
        return clean_text(requested_institution)
    profile = preflight.get("profile")
    primary = profile.get("primary_institution") if isinstance(profile, dict) else None
    return clean_text(primary) or None


def command_extract_account(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    warnings: list[str] = []
    preflight = load_preflight_json(args.preflight_json, "one-account", args.tax_year, args.pdf)
    warnings.extend(preflight_warning_lines(preflight))
    lines, file_profiles, full_text, pdf_warnings = load_pdf_lines(args.pdf)
    verify_preflight_statement_fingerprints(preflight, args.pdf)
    warnings.extend(pdf_warnings)
    page_period_contexts = preflight_page_period_contexts(preflight, lines)

    user_resolutions = preflight.get("user_resolutions") if isinstance(preflight.get("user_resolutions"), dict) else {}
    reviewed_currency = user_resolutions.get("currency") if isinstance(user_resolutions.get("currency"), dict) else {}
    confirmed_currency = (
        normalize_currency(str(reviewed_currency.get("code") or ""))
        if reviewed_currency.get("status") == "user-confirmed"
        else None
    )
    if confirmed_currency:
        if args.account_currency:
            requested_currency = normalize_currency(args.account_currency)
            if requested_currency != confirmed_currency:
                raise FbarError(
                    "--account-currency conflicts with the user-confirmed currency in the reviewed preflight handoff.",
                    2,
                )
        # A reviewed ISO resolution wins over a bare '$' in statement text.
        currency = confirmed_currency
    else:
        currency = infer_currency(full_text, args.account_currency, warnings)
    if currency in {"UNKNOWN", "MIXED"} and preflight:
        preflight_currency = preflight.get("currency")
        if isinstance(preflight_currency, dict):
            preflight_code = str(preflight_currency.get("code") or "").upper()
            if preflight_code not in {"", "UNKNOWN", "MIXED"}:
                currency = preflight_code
    account_hints = selected_account_hints(preflight, full_text)
    if len(account_hints) > 1:
        warnings.append(f"Multiple account hints found; verify this is one account: {', '.join(account_hints[:8])}.")
    if not account_hints:
        warnings.append("No account number/designation hint was found; verify this is one account.")
    if currency in {"UNKNOWN", "MIXED"}:
        warnings.append("Account currency is not confirmed; do not confirm this ledger until resolved.")
    institution = resolve_institution(preflight, user_resolutions, args.institution)

    compact_candidates, compact_page_keys, compact_warnings = extract_compact_cop_table_candidates(
        args.pdf,
        lines,
        args.tax_year,
        currency,
        page_period_contexts,
        currency_confirmed=bool(confirmed_currency and confirmed_currency == currency),
    )
    fallback_candidates, candidate_warnings = extract_balance_candidates(
        lines,
        args.tax_year,
        currency,
        page_period_contexts=page_period_contexts,
        currency_confirmed=bool(confirmed_currency and confirmed_currency == currency),
        excluded_page_keys=compact_page_keys,
        external_candidate_count=len(compact_candidates),
    )
    candidates = [*compact_candidates, *fallback_candidates]
    warnings.extend(compact_warnings)
    warnings.extend(candidate_warnings)
    # The compact-coordinate path opens the PDFs after the first post-text
    # fingerprint check. Re-check now so every extraction pass is bound to the
    # exact preflighted bytes, not merely the initial text-layer pass.
    verify_preflight_statement_fingerprints(preflight, args.pdf)
    daily_rows, coverage, coverage_warnings = build_daily_rows(args.tax_year, currency, candidates)
    warnings.extend(coverage_warnings)

    reviewed_one_account = user_resolutions.get("one_account") if isinstance(user_resolutions.get("one_account"), dict) else {}
    if args.account_id:
        account_id = args.account_id
        account_id_source = "user-supplied"
    elif not account_hints and reviewed_one_account.get("status") == "user-confirmed" and reviewed_one_account.get("confirmed") is True:
        # The preflight reviewer confirmed scope but deliberately did not supply
        # an account number. Keep a stable local label without inventing one.
        account_id = f"reviewed-one-account-{args.tax_year}"
        account_id_source = "reviewed-one-account-local-label"
    else:
        account_id = make_account_id(institution, account_hints, args.tax_year)
        account_id_source = "derived-from-statement-hints" if account_hints else "generated-local-label"
    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "skill": "fbar-threshold-check",
        "status": "extracted-review-required",
        "tax_year": args.tax_year,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "account": {
            "account_id": account_id,
            "institution": institution,
            "currency": currency,
            "account_number_hints": account_hints,
            "account_id_source": account_id_source,
        },
        "preflight": preflight_profile(preflight, args.preflight_json),
        "statement_files": file_profiles,
        "coverage": coverage,
        "data_sufficiency": build_data_sufficiency(coverage),
        "fx": {
            "required": currency not in {"USD", "UNKNOWN", "MIXED"},
            "workpaper_json": None,
            "workpaper": None,
        },
        "warnings": sorted(set(warnings)),
        "daily_ledger": daily_rows,
        "review_summary": build_review_summary(daily_rows),
        "artifacts": {},
    }
    csv_path = Path(args.csv) if args.csv else account_csv_path(out_path)
    data["artifacts"] = {"review_csv": str(csv_path)}
    write_json(out_path, data)
    write_account_csv(csv_path, data)
    print(f"Wrote account JSON: {out_path}")
    print(f"Wrote review CSV: {csv_path}")
    print_same_day_candidate_review_card(data)
    print_period_end_summary_review_card(data)
    print_data_sufficiency_review_card(data)
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


def schema_tuple(version: object) -> tuple[int, ...]:
    """Parse a "major.minor" schema string into a comparable tuple.

    Unparseable versions sort below every real schema so a malformed value can
    never satisfy a minimum-version floor.
    """
    try:
        return tuple(int(part) for part in str(version).split("."))
    except (TypeError, ValueError):
        return (0,)


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
            "FX workpaper must come from get-year-end-fx-rate for FBAR year-end conversion.",
            2,
        )
    if str(workpaper.get("currency", "")).upper() != currency:
        raise FbarError(f"FX workpaper currency {workpaper.get('currency')} does not match account currency {currency}.", 2)
    if as_int(workpaper.get("year", 0), "FX workpaper year") != year:
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

    return workpaper


def command_confirm_account(args: argparse.Namespace) -> int:
    if not args.balances_confirmed:
        raise FbarError("Pass --balances-confirmed only after reviewing the account JSON/CSV with the user.", 2)

    input_path = Path(args.input)
    out_path = Path(args.out)
    data = require_account_data(input_path)
    coverage = data.get("coverage", {})
    if not isinstance(coverage, dict):
        raise FbarError("Account ledger has no coverage object.", 2)
    if as_int(coverage.get("missing_days", 0), "coverage.missing_days") > 0:
        raise FbarError("Account ledger has missing days; do not confirm until opening/prior balance coverage is resolved.", 2)
    if as_int(coverage.get("low_confidence_days", 0), "coverage.low_confidence_days") > 0:
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
                "Non-USD account requires --fx-workpaper-json from get-year-end-fx-rate.",
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
    # Aggregation requires the native-precision era. Ledgers confirmed under
    # schema < 1.3 carry cent-rounded native balances/thresholds, so importing
    # them would silently reintroduce the boundary-rounding defect for 3-decimal
    # currencies. Re-extract those accounts with the current skill first.
    version = str(data.get("schema_version") or "missing")
    if schema_tuple(version) < (1, 3):
        raise FbarError(
            f"{path} was confirmed under schema {version}, whose native balances are cent-rounded. "
            f"Re-run extract-account and confirm-account with the current skill (schema {SCHEMA_VERSION}) "
            "before aggregating so 3-decimal currencies and boundary values stay exact.",
            2,
        )
    coverage = data.get("coverage", {})
    if isinstance(coverage, dict) and as_int(coverage.get("missing_days", 0), "coverage.missing_days") > 0:
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
        if as_int(account_coverage.get("missing_days", 0), "coverage.missing_days") > 0 or account_coverage.get("carry_gaps"):
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
                "max_usd_value": fmt_native(max_value),
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
            row[label] = fmt_native(value)
            combined += value
        row["combined_usd_value"] = fmt_native(combined)
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
            "max_combined_usd_value": fmt_native(max_combined),
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
        print(
            "pdfplumber missing — extract-account needs a Python runtime with pdfplumber. "
            "In Codex Desktop, call load_workspace_dependencies and rerun with the bundled Python; "
            "otherwise use a Python runtime that includes pdfplumber."
        )
        return 1
    print("pdfplumber ok")
    return 0


def command_self_test(_args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        test_decimal_parsing()
        test_separator_guardrails()
        test_money_tokenization()
        test_unpadded_fractional_balance_tokenization(root)
        test_sign_formats()
        test_line_extraction()
        test_source_bound_short_dates(root)
        test_source_bound_period_end_summaries(root)
        test_confirmed_cop_table_context(root)
        test_account_hint_selection()
        test_build_daily_rows()
        test_period_end_only_data_sufficiency()
        test_native_balance_precision()
        test_leap_year()
        test_same_day_variance()
        test_carry_gap_gate(root)
        test_mask_time_and_two_digit_year()
        test_fx_guardrails(root)
        test_fx_rate_guards(root)
        test_hostile_inputs_clean_errors(root)
        test_boundary_rounding(root)
        test_aggregate_rejects_pre_1_3(root)
        test_aggregate_native_precision_display(root)
        test_label_collisions()
        test_duplicate_ledger_refused(root)
        test_schema_gate(root)
        test_confirm_and_aggregate(root)
        test_maxima_disagreement(root)
        test_csv_naming()
        test_preflight_handoff(root)
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
            # Deliberately neutral wording: the year-end skill itself is the
            # dependency proof, so source title keywords are not required.
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


def test_unpadded_fractional_balance_tokenization(root: Path) -> None:
    """Keep an unpadded fractional column separate from a grouped balance."""
    values = parse_money_values(".42 210,345.67")
    assert [(amount, token) for amount, token, _pos, _notes in values] == [
        (Decimal("0.42"), ".42"),
        (Decimal("210345.67"), "210,345.67"),
    ], values

    # Preserve the existing guarded separator behavior while fixing the
    # adjacent-column boundary: ambiguous bare grouping remains reviewable,
    # explicit decimals are unambiguous, and a legitimate space grouping stays
    # one amount.
    for raw, expected in (("12,000", Decimal("12000")), ("12.000", Decimal("12000"))):
        amount, notes = parse_amount(raw)
        assert amount == expected and notes, (raw, amount, notes)
    for raw, expected in (("10,000.01", Decimal("10000.01")), ("10.000,01", Decimal("10000.01"))):
        amount, notes = parse_amount(raw)
        assert amount == expected and not notes, (raw, amount, notes)
    amount, notes = parse_amount("1 234,56")
    assert amount == Decimal("1234.56") and not notes, (amount, notes)

    # Exercise the generic, source-bound table path as well. The first value
    # is an adjacent transaction amount; the last is the selected balance.
    pdf = root / "example-statement.pdf"
    pdf.write_bytes(b"synthetic example statement")
    preflight_path = write_preflight_fixture(root, "example-preflight", 2025, "one-account", [pdf])
    preflight = load_json(preflight_path)
    statement_files = preflight.get("statement_files")
    assert isinstance(statement_files, list) and isinstance(statement_files[0], dict)
    statement_files[0]["period_intervals"] = [
        {
            "start": "2025-01-01",
            "end": "2025-03-31",
            "confidence": "high",
            "source_ref": {"page": 1, "line": 1},
        }
    ]
    period_line = "Statement period 2025/01/01 through 2025/03/31"
    row_line = "1/04 Example entry .42 210,345.67"
    lines = [
        (SourceRef(str(pdf), 1, 1, period_line), period_line),
        (SourceRef(str(pdf), 1, 2, "DATE DETAILS BALANCE"), "DATE DETAILS BALANCE"),
        (SourceRef(str(pdf), 1, 3, row_line), row_line),
    ]
    contexts = preflight_page_period_contexts(preflight, lines)
    candidates, warnings = extract_balance_candidates(lines, 2025, "USD", page_period_contexts=contexts)
    assert not warnings, warnings
    assert len(candidates) == 1, candidates
    candidate = candidates[0]
    assert candidate.balance_date == date(2025, 1, 4), candidate.balance_date
    assert candidate.amount == Decimal("210345.67"), candidate.amount
    assert candidate.source == SourceRef(str(pdf), 1, 3, row_line), candidate.source

    rows, coverage, _warnings = build_daily_rows(2025, "USD", candidates)
    january_fourth = next(row for row in rows if row["date"] == "2025-01-04")
    assert january_fourth["native_balance"] == "210345.67", january_fourth
    assert january_fourth["review_flags"] == [], january_fourth
    assert coverage["observed_days"] == 1, coverage
    assert coverage["material_same_day_variance_days"] == 0, coverage


def test_mask_time_and_two_digit_year() -> None:
    # A clock time or 2-digit-year date sitting after the balance term must be
    # masked so its fragments cannot be selected instead of the real balance.
    masked = mask_date_spans("2023-12-31 balance 1,234.56 at 23:59")
    assert [amt for amt, _t, _p, _n in parse_money_values(masked)] == [Decimal("1234.56")]
    masked = mask_date_spans("2023-12-31 balance 9,999.99 prior 31/12/22")
    assert [amt for amt, _t, _p, _n in parse_money_values(masked)] == [Decimal("9999.99")]
    # End-to-end: the real balance wins even when the time trails the term.
    line = "2023-12-31 Closing balance 1,234.56 at 23:59"
    cands, _warnings = extract_balance_candidates([(SourceRef("t.pdf", 1, 1, line), line)], 2023, "USD")
    assert cands and cands[-1].amount == Decimal("1234.56"), cands[-1].amount if cands else None
    # Masking must not eat real amounts: European grouped money (3-digit groups)
    # and comma decimals are left intact.
    assert "#" not in mask_date_spans("balance 4.000.000,00")
    assert "#" not in mask_date_spans("balance 1.234,56")


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


def test_source_bound_short_dates(root: Path) -> None:
    """Exercise Spanish table dates without making a global DD/MM assumption."""
    pdf = root / "short-date-statement.pdf"
    pdf.write_bytes(b"synthetic short date statement")
    preflight_path = write_preflight_fixture(root, "short-date-preflight", 2025, "one-account", [pdf])
    preflight = load_json(preflight_path)
    statement_files = preflight.get("statement_files")
    assert isinstance(statement_files, list) and isinstance(statement_files[0], dict)

    def with_period(
        start: str, end: str, line_number: int = 1, end_line_number: int | None = None
    ) -> dict[str, object]:
        period: dict[str, object] = {
            "start": start,
            "end": end,
            "confidence": "high",
            "source_ref": {"page": 1, "line": line_number},
        }
        if end_line_number is not None:
            period["end_source_ref"] = {"page": 1, "line": end_line_number}
        statement_files[0]["period_intervals"] = [period]
        return preflight

    header = "DESDE: 2024/12/31 HASTA: 2025/03/31"
    lines = [
        (SourceRef(str(pdf), 1, 1, header), header),
        (SourceRef(str(pdf), 1, 2, "FECHA VALOR SALDO"), "FECHA VALOR SALDO"),
        (
            SourceRef(str(pdf), 1, 3, "4/01 COMPRA 100,000.00 9,876,543.21"),
            "4/01 COMPRA 100,000.00 9,876,543.21",
        ),
    ]
    contexts = preflight_page_period_contexts(with_period("2024-12-31", "2025-03-31"), lines)
    candidates, warnings = extract_balance_candidates(lines, 2025, "COP", page_period_contexts=contexts)
    assert not warnings, warnings
    assert len(candidates) == 1, candidates
    candidate = candidates[0]
    assert candidate.balance_date == date(2025, 1, 4), candidate.balance_date
    assert candidate.amount == Decimal("9876543.21"), candidate.amount
    assert candidate.confidence == "medium", candidate.confidence
    assert candidate.source.line == 3, candidate.source
    assert any("source-bound page statement period" in note for note in candidate.notes), candidate.notes

    split_header_lines = [
        (SourceRef(str(pdf), 1, 1, "DESDE: 01-Ene-2025"), "DESDE: 01-Ene-2025"),
        (SourceRef(str(pdf), 1, 2, "HASTA: 31-Mar-2025"), "HASTA: 31-Mar-2025"),
        (SourceRef(str(pdf), 1, 3, "FECHA VALOR SALDO"), "FECHA VALOR SALDO"),
        (SourceRef(str(pdf), 1, 4, "4/01 COMPRA 100,000.00 9,876,543.21"), "4/01 COMPRA 100,000.00 9,876,543.21"),
    ]
    contexts = preflight_page_period_contexts(
        with_period("2025-01-01", "2025-03-31", line_number=1, end_line_number=2), split_header_lines
    )
    candidates, warnings = extract_balance_candidates(split_header_lines, 2025, "COP", page_period_contexts=contexts)
    assert not warnings, warnings
    assert len(candidates) == 1 and candidates[0].balance_date == date(2025, 1, 4), candidates

    cross_year_lines = [
        (SourceRef(str(pdf), 1, 1, header), header),
        (SourceRef(str(pdf), 1, 2, "FECHA VALOR SALDO"), "FECHA VALOR SALDO"),
        (SourceRef(str(pdf), 1, 3, "31/12 CIERRE 10.00 900.00"), "31/12 CIERRE 10.00 900.00"),
        (SourceRef(str(pdf), 1, 4, "1/01 APERTURA 10.00 950.00"), "1/01 APERTURA 10.00 950.00"),
    ]
    contexts = preflight_page_period_contexts(with_period("2024-12-31", "2025-03-31"), cross_year_lines)
    candidates, warnings = extract_balance_candidates(cross_year_lines, 2025, "COP", page_period_contexts=contexts)
    assert len(candidates) == 1 and candidates[0].balance_date == date(2025, 1, 1), candidates
    assert any("outside" in warning for warning in warnings), warnings

    neutral_header = "Statement period 2025/01/01 through 2025/03/31"
    ambiguous_lines = [
        (SourceRef(str(pdf), 1, 1, neutral_header), neutral_header),
        (SourceRef(str(pdf), 1, 2, "DATE VALUE BALANCE"), "DATE VALUE BALANCE"),
        (SourceRef(str(pdf), 1, 3, "1/02 ITEM 10.00 950.00"), "1/02 ITEM 10.00 950.00"),
    ]
    contexts = preflight_page_period_contexts(with_period("2025-01-01", "2025-03-31"), ambiguous_lines)
    candidates, warnings = extract_balance_candidates(ambiguous_lines, 2025, "USD", page_period_contexts=contexts)
    assert not candidates, candidates
    assert any("No balance candidates" in warning for warning in warnings), warnings

    candidates, warnings = extract_balance_candidates(lines, 2025, "COP")
    assert not candidates, candidates
    assert any("No balance candidates" in warning for warning in warnings), warnings

    mismatched_header = "DESDE: 2025/04/01 HASTA: 2025/06/30"
    mismatched_lines = [
        (SourceRef(str(pdf), 1, 1, mismatched_header), mismatched_header),
        (SourceRef(str(pdf), 1, 2, "FECHA VALOR SALDO"), "FECHA VALOR SALDO"),
        (SourceRef(str(pdf), 1, 3, "4/01 COMPRA 100,000.00 9,876,543.21"), "4/01 COMPRA 100,000.00 9,876,543.21"),
    ]
    contexts = preflight_page_period_contexts(with_period("2024-12-31", "2025-03-31"), mismatched_lines)
    assert not contexts, contexts
    candidates, warnings = extract_balance_candidates(mismatched_lines, 2025, "COP", page_period_contexts=contexts)
    assert not candidates, candidates
    assert any("No balance candidates" in warning for warning in warnings), warnings


def test_source_bound_period_end_summaries(root: Path) -> None:
    """Keep period-end summaries source-bound and distinct from daily rows."""
    pdf = root / "period-end-statement.pdf"
    pdf.write_bytes(b"synthetic period-end statement")
    preflight_path = write_preflight_fixture(root, "period-end-preflight", 2025, "one-account", [pdf])
    preflight = load_json(preflight_path)
    statement_files = preflight.get("statement_files")
    assert isinstance(statement_files, list) and isinstance(statement_files[0], dict)
    statement_files[0]["period_intervals"] = [
        {
            "start": "2025-10-01",
            "end": "2025-12-31",
            "confidence": "high",
            "source_ref": {"page": 1, "line": 1},
        }
    ]
    header = "DESDE: 2025/10/01 HASTA: 2025/12/31"
    summary_line = "Saldo al final del período 8,905,317"
    lines = [
        (SourceRef(str(pdf), 1, 1, header), header),
        (SourceRef(str(pdf), 1, 2, summary_line), summary_line),
    ]
    contexts = preflight_page_period_contexts(preflight, lines)
    candidates, warnings = extract_balance_candidates(lines, 2025, "COP", page_period_contexts=contexts)
    assert not warnings, warnings
    assert len(candidates) == 1, candidates
    candidate = candidates[0]
    assert candidate.balance_date == date(2025, 12, 31), candidate
    assert candidate.amount == Decimal("8905317"), candidate
    assert candidate.candidate_type == "period-end-summary", candidate
    assert candidate.period_end == date(2025, 12, 31), candidate
    assert candidate.period_end_source is not None and candidate.period_end_source.line == 1, candidate

    rows, coverage, _warnings = build_daily_rows(2025, "COP", candidates)
    assert rows[-1]["balance_source"] == "period-end-summary", rows[-1]
    assert rows[-1]["evidence_class"] == "period-end-summary", rows[-1]
    assert rows[-1]["source_refs"] == ["period-end-statement.pdf:p1:l2", "period-end-statement.pdf:p1:l1"], rows[-1]
    assert rows[-2]["balance_source"] == "missing-opening-coverage", rows[-2]
    assert rows[-2]["evidence_class"] == "missing-opening", rows[-2]
    assert coverage["observed_days"] == 1 and coverage["carried_forward_days"] == 0, coverage
    assert coverage["trailing_carry_days"] == 0, coverage
    flags = rows[-1]["review_flags"]
    assert isinstance(flags, list) and flags == [
        {
            "code": "period-end-summary",
            "matched_period_end": "2025-12-31",
            "summary_source_ref": "period-end-statement.pdf:p1:l2",
            "period_end_source_ref": "period-end-statement.pdf:p1:l1",
        }
    ], flags
    review_summary = build_review_summary(rows)
    assert review_summary["same_day_balance_candidates"]["count"] == 0, review_summary
    assert review_summary["period_end_summaries"]["count"] == 1, review_summary
    assert "period-end summary (period_end=2025-12-31;" in format_review_flags(flags)
    csv_path = root / "period-end-review.csv"
    write_account_csv(csv_path, {"account": {}, "daily_ledger": rows})
    with csv_path.open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert csv_rows[-1]["evidence_class"] == "period-end-summary", csv_rows[-1]
    assert csv_rows[-2]["evidence_class"] == "missing-opening", csv_rows[-2]

    unbound_candidates, _warnings = extract_balance_candidates(lines, 2025, "COP")
    assert not unbound_candidates, unbound_candidates
    other_page_lines = [
        lines[0],
        (SourceRef(str(pdf), 2, 1, summary_line), summary_line),
    ]
    other_page_candidates, _warnings = extract_balance_candidates(
        other_page_lines, 2025, "COP", page_period_contexts=contexts
    )
    assert not other_page_candidates, other_page_candidates


def test_confirmed_cop_table_context(root: Path) -> None:
    """Permit a COP grouping rule only in the exact verified COP table."""
    pdf = root / "cop-table.pdf"
    pdf.write_bytes(b"synthetic COP table")
    preflight_path = write_preflight_fixture(root, "cop-table-preflight", 2025, "one-account", [pdf])
    preflight = load_json(preflight_path)
    statement_files = preflight.get("statement_files")
    assert isinstance(statement_files, list) and isinstance(statement_files[0], dict)
    statement_files[0]["period_intervals"] = [
        {
            "start": "2025-01-01",
            "end": "2025-03-31",
            "confidence": "high",
            "source_ref": {"page": 1, "line": 1},
        }
    ]
    period_line = "DESDE: 2025/01/01 HASTA: 2025/03/31"
    table_header = "Fecha Descripción Movimiento Tarjeta Débito Abono Saldo"
    grouped_row = "2025-01-02 11:13:11 COMPRA 1234567 $1 $12,000"
    zero_row = "2025-01-03 11:13:11 COMPRA 1234567 $12,000 $-0"
    lines = [
        (SourceRef(str(pdf), 1, 1, period_line), period_line),
        (SourceRef(str(pdf), 1, 2, table_header), table_header),
        (SourceRef(str(pdf), 1, 3, grouped_row), grouped_row),
        (SourceRef(str(pdf), 1, 4, zero_row), zero_row),
    ]
    contexts = preflight_page_period_contexts(preflight, lines)
    trusted, warnings = extract_balance_candidates(
        lines, 2025, "COP", page_period_contexts=contexts, currency_confirmed=True
    )
    assert not warnings, warnings
    assert [candidate.amount for candidate in trusted] == [Decimal("12000"), Decimal("0")], trusted
    assert all(candidate.confidence == "high" for candidate in trusted), trusted
    assert any("Whole-COP comma grouping accepted only" in note for note in trusted[0].notes), trusted[0]
    assert any("COP Fecha Descripción Movimiento Tarjeta Débito Abono Saldo" in note for note in trusted[1].notes), trusted[1]

    unconfirmed, warnings = extract_balance_candidates(lines, 2025, "COP", page_period_contexts=contexts)
    assert len(unconfirmed) == 2 and all(candidate.confidence != "high" for candidate in unconfirmed), unconfirmed
    assert any("ambiguous thousands/decimal separators" in warning for warning in warnings), warnings
    assert any("bare 1-2 digit amount" in warning for warning in warnings), warnings

    unbound, warnings = extract_balance_candidates(lines, 2025, "COP", currency_confirmed=True)
    assert len(unbound) == 2 and all(candidate.confidence != "high" for candidate in unbound), unbound
    assert any("ambiguous thousands/decimal separators" in warning for warning in warnings), warnings

    amount, parse_notes = parse_amount("12,000")
    assert amount == Decimal("12000") and parse_notes, (amount, parse_notes)


def test_account_hint_selection() -> None:
    assert extract_account_hints("Cuenta 76543210") == ["76543210"]
    assert extract_account_hints("Cuenta DE AHORROS") == []
    assert selected_account_hints({"account_hints": ["76543210"]}, "Cuenta 12345678") == ["76543210"]
    assert selected_account_hints({"account_hints": ["DE AHORROS"]}, "Cuenta 12345678") == ["12345678"]


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

    yearly_workpaper = root / "yearly-workpaper.json"
    write_json(
        yearly_workpaper,
        {
            "skill": "get-yearly-fx-rate",
            "currency": "CAD",
            "year": 2025,
            "foreign_per_usd": "1.37",
            "usd_per_foreign": "0.729927",
            "source": {
                "title": "Example yearly average table",
                "url": "https://example.test/yearly-average",
                "retrieved": "2026-07-04",
            },
            "proof": {"workpaper_json": str(yearly_workpaper)},
        },
    )
    try:
        validate_fx_workpaper(yearly_workpaper, "CAD", 2025)
    except FbarError as exc:
        assert "get-year-end-fx-rate" in str(exc)
    else:
        raise AssertionError("get-yearly-fx-rate workpaper must be rejected for FBAR")


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


def test_aggregate_rejects_pre_1_3(root: Path) -> None:
    # A ledger confirmed under a pre-1.3 schema carries cent-rounded native
    # balances; aggregate must refuse it rather than silently re-import them.
    ledger = synthetic_account(root, "stale-a", 2025, "USD", Decimal("6000"), {})
    data = load_json(ledger)
    data["schema_version"] = "1.2"
    write_json(ledger, data)
    out = root / "stale-summary.json"
    try:
        command_aggregate(argparse.Namespace(account_ledger=[str(ledger)], out=str(out), csv=None, pdf=None))
    except FbarError as exc:
        assert "1.2" in str(exc) and "schema" in str(exc).lower()
    else:
        raise AssertionError("aggregate must refuse pre-1.3 confirmed ledgers")


def test_aggregate_native_precision_display(root: Path) -> None:
    # A 3-decimal currency keeps full precision in the displayed max/combined USD.
    fx = make_year_end_fx_workpaper(root, currency="KWD", foreign_per_usd=None, usd_per_foreign="3.26", name="kwd-disp")
    ledger = synthetic_account(root, "kwd-disp-a", 2025, "KWD", Decimal("3067.485"), {}, fx)
    out = root / "kwd-disp-summary.json"
    command_aggregate(argparse.Namespace(account_ledger=[str(ledger)], out=str(out), csv=None, pdf=None))
    summary = load_json(out)
    account = summary["accounts"][0]
    # 3067.485 KWD * 3.26 = 10000.0011 USD: displayed at full precision, not "10000".
    assert account["max_usd_value"] == "10000.0011", account["max_usd_value"]
    assert account["max_usd_value_whole_dollars"] == 10001, account["max_usd_value_whole_dollars"]


def test_hostile_inputs_clean_errors(root: Path) -> None:
    # A non-integer FX year must raise a clean FbarError, not a raw ValueError.
    workpaper = root / "hostile-year-workpaper.json"
    write_json(
        workpaper,
        {
            "skill": "get-year-end-fx-rate",
            "currency": "COP",
            "year": "banana",
            "usd_per_foreign": "0.00025",
            "source": {"title": "t", "url": "u", "retrieved": "2026-01-01"},
            "proof": {"workpaper_json": str(workpaper)},
        },
    )
    try:
        validate_fx_workpaper(workpaper, "COP", 2025)
    except FbarError as exc:
        assert "year" in str(exc).lower()
    else:
        raise AssertionError("hostile FX workpaper year must raise FbarError")

    # A non-integer coverage counter must raise a clean FbarError.
    ledger = root / "hostile-coverage-ledger.json"
    write_json(
        ledger,
        {
            "schema_version": SCHEMA_VERSION,
            "skill": "fbar-threshold-check",
            "tax_year": 2025,
            "account": {"currency": "USD"},
            "coverage": {"missing_days": "lots"},
            "daily_ledger": [],
        },
    )
    try:
        command_confirm_account(
            argparse.Namespace(
                input=str(ledger),
                out=str(root / "hostile-out.json"),
                csv=None,
                balances_confirmed=True,
                fx_workpaper_json=None,
                accept_carry_forward=False,
            )
        )
    except FbarError as exc:
        assert "missing_days" in str(exc)
    else:
        raise AssertionError("hostile coverage counter must raise FbarError")


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


def test_separator_guardrails() -> None:
    """Keep generic separator handling reviewable outside a verified table."""
    for raw, expected in (("12,000", Decimal("12000")), ("12.000", Decimal("12000"))):
        amount, notes = parse_amount(raw)
        assert amount == expected and notes, (raw, amount, notes)
    for raw, expected in (("10,000.01", Decimal("10000.01")), ("10.000,01", Decimal("10000.01"))):
        amount, notes = parse_amount(raw)
        assert amount == expected and not notes, (raw, amount, notes)


def test_build_daily_rows() -> None:
    candidates = [
        BalanceCandidate(date(2025, 1, 1), Decimal("100"), "USD", "high", SourceRef("a.pdf", 1, 1, "")),
        BalanceCandidate(date(2025, 1, 1), Decimal("150"), "USD", "high", SourceRef("a.pdf", 1, 2, "")),
        BalanceCandidate(date(2025, 1, 3), Decimal("125"), "USD", "high", SourceRef("a.pdf", 1, 3, "")),
    ]
    rows, coverage, warnings = build_daily_rows(2025, "USD", candidates)
    assert len(rows) == 365
    assert rows[0]["native_balance"] == "150"
    assert rows[0]["evidence_class"] == "transaction"
    assert rows[1]["native_balance"] == "150"
    assert rows[1]["evidence_class"] == "carried-forward"
    assert rows[2]["native_balance"] == "125"
    assert rows[0]["confidence"] == "high"
    assert coverage["complete_year"] is True
    # Statements stop on Jan 3: the trailing carry to Dec 31 must be flagged.
    assert coverage["last_observed_date"] == "2025-01-03"
    assert coverage["trailing_carry_days"] == 362
    assert coverage["carry_gap_review_required"] is True
    assert coverage["carry_gaps"] and coverage["carry_gaps"][-1]["end"] == "2025-12-31"
    assert any("carried forward" in warning for warning in warnings)


def test_period_end_only_data_sufficiency() -> None:
    candidates = [
        BalanceCandidate(
            end,
            Decimal("0"),
            "COP",
            "medium",
            SourceRef("summary.pdf", index, 2, "Cierre del periodo 0"),
            candidate_type="period-end-summary",
            period_end=end,
            period_end_source=SourceRef("summary.pdf", index, 1, "Statement period"),
        )
        for index, end in enumerate(
            (date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)), start=1
        )
    ]
    _rows, coverage, _warnings = build_daily_rows(2025, "COP", candidates)
    sufficiency = build_data_sufficiency(coverage)
    assert coverage["observed_days"] == 4, coverage
    assert coverage["transaction_observed_days"] == 0, coverage
    assert coverage["period_end_summary_days"] == 4, coverage
    assert sufficiency == {
        "evidence_profile": "period-end-only",
        "daily_threshold": {
            "answer": "insufficient-records",
            "reason_codes": ["period-end-only", "missing-opening-coverage", "long-carry-forward-gap"],
        },
        "maximum_account_value": {
            "answer": "not-determinable",
            "reason_codes": ["period-end-only", "missing-opening-coverage", "long-carry-forward-gap"],
        },
    }, sufficiency


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
    flags = rows[0]["review_flags"]
    assert isinstance(flags, list) and len(flags) == 1
    flag = flags[0]
    assert isinstance(flag, dict)
    assert flag["code"] == "material-same-day-balance-candidates"
    assert flag["candidate_count"] == 2
    assert flag["minimum_native_balance"] == "100"
    assert flag["maximum_native_balance"] == "100000"
    assert flag["selected_source_ref"] == "a.pdf:p1:l2"
    summary = build_review_summary(rows)
    same_day = summary["same_day_balance_candidates"]
    assert isinstance(same_day, dict)
    assert same_day["count"] == 1 and same_day["requires_user_review"] is True
    assert format_review_flags(flags).startswith("same-day candidates (count=2;")
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

    invalid = make_yearly_average_workpaper(root)
    try:
        validate_fx_workpaper(invalid, "CAD", 2025)
    except FbarError as exc:
        assert "get-year-end-fx-rate" in str(exc)
    else:
        raise AssertionError("get-yearly-fx-rate workpaper should be rejected")

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


def write_preflight_fixture(
    root: Path,
    name: str,
    tax_year: int,
    scope: str,
    pdf_paths: list[Path],
    *,
    status: str = PREFLIGHT_READY_STATUS,
    gates: list[dict[str, str]] | None = None,
) -> Path:
    path = root / f"{name}.json"
    review_gates = gates or []
    data = {
        "schema_version": "1.0",
        "skill": PREFLIGHT_SKILL,
        "status": status,
        "tax_year": tax_year,
        "scope": scope,
        "statement_files": [
            {
                "file": str(pdf),
                "resolved_file": normalize_preflight_path(pdf),
                "content_sha256": preflight_file_sha256(pdf),
                "content_bytes": preflight_file_bytes(pdf, "test statement PDF"),
            }
            for pdf in pdf_paths
        ],
        "currency": {"code": "USD", "candidates": ["USD"]},
        "profile": {"primary_institution": "Preflight Bank"},
        "warnings": ["sample review warning"],
        "review_gates": review_gates,
        "artifacts": {"review_csv": str(root / f"{name}.csv")},
    }
    write_json(path, data)
    return path


def write_reviewed_handoff_fixture(root: Path, name: str, source_path: Path, accepted_codes: list[str] | None = None) -> Path:
    source = load_json(source_path)
    review_gates = source["review_gates"]
    assert isinstance(review_gates, list)
    expected_codes = [str(gate["code"]) for gate in review_gates if isinstance(gate, dict)]
    handoff = {
        "schema_version": source["schema_version"],
        "skill": PREFLIGHT_SKILL,
        "artifact_type": PREFLIGHT_REVIEWED_HANDOFF_TYPE,
        "status": PREFLIGHT_REVIEWED_HANDOFF_STATUS,
        "tax_year": source["tax_year"],
        "scope": source["scope"],
        "requirements": source.get("requirements"),
        "statement_files": source["statement_files"],
        "review_gates": review_gates,
        "source_preflight": {
            "path": str(source_path),
            "resolved_path": normalize_preflight_path(source_path),
            "sha256": preflight_file_sha256(source_path),
            "schema_version": source["schema_version"],
            "status": source["status"],
            "tax_year": source["tax_year"],
            "scope": source["scope"],
            "requirements": source.get("requirements"),
        },
        "review": {
            "user_review_confirmed": True,
            "accepted_gate_codes": accepted_codes if accepted_codes is not None else expected_codes,
            "confirmed_at": "2026-07-12T00:00:00Z",
        },
    }
    path = root / f"{name}.json"
    write_json(path, handoff)
    return path


def test_preflight_handoff(root: Path) -> None:
    pdf = root / "statement.pdf"
    pdf.write_bytes(b"synthetic statement PDF one")
    other_pdf = root / "other.pdf"
    other_pdf.write_bytes(b"synthetic statement PDF two")
    preflight = write_preflight_fixture(root, "preflight-ok", 2025, "one-account", [pdf])
    data = load_preflight_json(str(preflight), "one-account", 2025, [str(pdf)])
    warnings = preflight_warning_lines(data)
    assert any("sample review warning" in warning for warning in warnings), warnings

    review_gate = {"code": "sample-gate", "severity": "review", "message": "sample gate message"}
    review_required = write_preflight_fixture(
        root,
        "preflight-review-required",
        2025,
        "one-account",
        [pdf],
        status=PREFLIGHT_REVIEW_REQUIRED_STATUS,
        gates=[review_gate],
    )
    try:
        load_preflight_json(str(review_required), "one-account", 2025, [str(pdf)])
    except FbarError as exc:
        assert "review-required" in str(exc), str(exc)
    else:
        raise AssertionError("raw review-required preflight must be rejected")

    reviewed_handoff = write_reviewed_handoff_fixture(root, "preflight-reviewed", review_required)
    reviewed = load_preflight_json(str(reviewed_handoff), "one-account", 2025, [str(pdf)])
    assert reviewed["status"] == PREFLIGHT_REVIEWED_HANDOFF_STATUS
    handoff = reviewed.get("review_handoff")
    assert isinstance(handoff, dict) and handoff.get("accepted_gate_codes") == ["sample-gate"]

    institution_gate = {"code": "unknown-institution", "severity": "review", "message": "issuer needs review"}
    institution_source = write_preflight_fixture(
        root,
        "preflight-institution-review",
        2025,
        "one-account",
        [pdf],
        status=PREFLIGHT_REVIEW_REQUIRED_STATUS,
        gates=[institution_gate],
    )
    institution_source_data = load_json(institution_source)
    institution_source_data["requirements"] = {"institution_required": True}
    write_json(institution_source, institution_source_data)
    institution_handoff = write_reviewed_handoff_fixture(root, "preflight-institution-reviewed", institution_source)
    institution_handoff_data = load_json(institution_handoff)
    institution_handoff_data["user_resolutions"] = {
        "source_preflight_sha256": preflight_file_sha256(institution_source),
        "statement_years": {"status": "not-required"},
        "currency": {"status": "not-required"},
        "one_account": {"status": "not-required"},
        "institution": {
            "status": "user-confirmed",
            "name": "Reviewed Test Bank",
            "resolved_gate_codes": ["unknown-institution"],
        },
    }
    write_json(institution_handoff, institution_handoff_data)
    institution_reviewed = load_preflight_json(str(institution_handoff), "one-account", 2025, [str(pdf)])
    institution_resolutions = institution_reviewed.get("user_resolutions")
    assert isinstance(institution_resolutions, dict)
    assert resolve_institution(institution_reviewed, institution_resolutions, None) == "Reviewed Test Bank"
    try:
        resolve_institution(institution_reviewed, institution_resolutions, "Different Test Bank")
    except FbarError as exc:
        assert "conflicts with the user-confirmed institution" in str(exc), str(exc)
    else:
        raise AssertionError("a CLI institution must not override a reviewed institution resolution")

    incomplete_handoff = write_reviewed_handoff_fixture(root, "preflight-incomplete", review_required, accepted_codes=[])
    try:
        load_preflight_json(str(incomplete_handoff), "one-account", 2025, [str(pdf)])
    except FbarError as exc:
        assert "every and only" in str(exc), str(exc)
    else:
        raise AssertionError("reviewed handoff must acknowledge every source gate")

    structural_source = write_preflight_fixture(
        root,
        "preflight-structural",
        2025,
        "one-account",
        [pdf],
        status=PREFLIGHT_REVIEW_REQUIRED_STATUS,
        gates=[{"code": "low-text-pdf", "severity": "stop", "message": "statement has little text"}],
    )
    structural_handoff = write_reviewed_handoff_fixture(root, "preflight-structural-handoff", structural_source)
    try:
        load_preflight_json(str(structural_handoff), "one-account", 2025, [str(pdf)])
    except FbarError as exc:
        assert "structural stop" in str(exc), str(exc)
    else:
        raise AssertionError("reviewed handoff must reject structural stop gates")

    changed_source = write_preflight_fixture(
        root,
        "preflight-changed-source",
        2025,
        "one-account",
        [pdf],
        status=PREFLIGHT_REVIEW_REQUIRED_STATUS,
        gates=[review_gate],
    )
    changed_handoff = write_reviewed_handoff_fixture(root, "preflight-changed-handoff", changed_source)
    changed_data = load_json(changed_source)
    changed_data["warnings"] = ["changed after review"]
    write_json(changed_source, changed_data)
    try:
        load_preflight_json(str(changed_handoff), "one-account", 2025, [str(pdf)])
    except FbarError as exc:
        assert "changed after review" in str(exc), str(exc)
    else:
        raise AssertionError("reviewed handoff must reject a changed source preflight")

    try:
        load_preflight_json(None, "one-account", 2025, [str(pdf)])
    except FbarError as exc:
        assert "required" in str(exc), str(exc)
    else:
        raise AssertionError("preflight must be required")

    legacy = write_preflight_fixture(root, "preflight-legacy", 2025, "one-account", [pdf])
    legacy_data = load_json(legacy)
    legacy_files = legacy_data["statement_files"]
    assert isinstance(legacy_files, list) and isinstance(legacy_files[0], dict)
    legacy_files[0].pop("content_sha256", None)
    legacy_files[0].pop("content_bytes", None)
    write_json(legacy, legacy_data)
    try:
        load_preflight_json(str(legacy), "one-account", 2025, [str(pdf)])
    except FbarError as exc:
        assert "fingerprint" in str(exc), str(exc)
    else:
        raise AssertionError("legacy preflight without file fingerprints must be rejected")

    reordered = write_preflight_fixture(root, "preflight-reordered", 2025, "one-account", [pdf, other_pdf])
    try:
        load_preflight_json(str(reordered), "one-account", 2025, [str(other_pdf), str(pdf)])
    except FbarError as exc:
        assert "sequence" in str(exc), str(exc)
    else:
        raise AssertionError("reordered statement PDFs must be rejected")

    try:
        load_preflight_json(str(preflight), "one-account", 2025, [str(pdf), str(pdf)])
    except FbarError as exc:
        assert "Duplicate statement" in str(exc), str(exc)
    else:
        raise AssertionError("duplicate extraction PDFs must be rejected")

    duplicate_path_preflight = write_preflight_fixture(
        root, "preflight-duplicate-path", 2025, "one-account", [pdf, other_pdf]
    )
    duplicate_path_data = load_json(duplicate_path_preflight)
    duplicate_path_files = duplicate_path_data["statement_files"]
    assert isinstance(duplicate_path_files, list) and isinstance(duplicate_path_files[0], dict) and isinstance(duplicate_path_files[1], dict)
    duplicate_path_files[1]["resolved_file"] = duplicate_path_files[0]["resolved_file"]
    write_json(duplicate_path_preflight, duplicate_path_data)
    try:
        load_preflight_json(str(duplicate_path_preflight), "one-account", 2025, [str(pdf), str(other_pdf)])
    except FbarError as exc:
        assert "duplicate statement paths" in str(exc), str(exc)
    else:
        raise AssertionError("preflight with duplicate statement paths must be rejected")

    identical_copy = root / "identical-copy.pdf"
    identical_copy.write_bytes(pdf.read_bytes())
    duplicate_content_preflight = write_preflight_fixture(
        root, "preflight-duplicate-content", 2025, "one-account", [pdf, identical_copy]
    )
    try:
        load_preflight_json(str(duplicate_content_preflight), "one-account", 2025, [str(pdf), str(identical_copy)])
    except FbarError as exc:
        assert "byte-identical" in str(exc), str(exc)
    else:
        raise AssertionError("preflight with byte-identical statement PDFs must be rejected")

    changed_pdf = root / "changed-statement.pdf"
    changed_pdf.write_bytes(b"statement bytes before preflight")
    changed_pdf_preflight = write_preflight_fixture(root, "preflight-changed-pdf", 2025, "one-account", [changed_pdf])
    changed_pdf.write_bytes(b"statement bytes changed after preflight")
    try:
        load_preflight_json(str(changed_pdf_preflight), "one-account", 2025, [str(changed_pdf)])
    except FbarError as exc:
        assert "changed after preflight" in str(exc), str(exc)
    else:
        raise AssertionError("changed statement PDFs must be rejected")

    for name, year, scope, pdfs, expected in (
        ("bad-year", 2024, "one-account", [pdf], "tax_year"),
        ("bad-scope", 2025, "one-institution", [pdf], "scope"),
        ("bad-pdfs", 2025, "one-account", [other_pdf], "sequence"),
    ):
        bad = write_preflight_fixture(root, name, year, scope, pdfs)
        try:
            load_preflight_json(str(bad), "one-account", 2025, [str(pdf)])
        except FbarError as exc:
            assert expected in str(exc), str(exc)
        else:
            raise AssertionError(f"{name} preflight fixture should be rejected")


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
    extract.add_argument(
        "--preflight-json",
        required=True,
        help="Ready preflight JSON or reviewed-handoff JSON from statement-intake-preflight.",
    )
    extract.set_defaults(func=command_extract_account)

    confirm = subparsers.add_parser("confirm-account", help="Confirm reviewed account ledger and convert to USD when needed.")
    confirm.add_argument("--input", required=True, help="Extracted account JSON.")
    confirm.add_argument("--out", required=True, help="Confirmed account JSON.")
    confirm.add_argument("--csv", help="Optional confirmed CSV path.")
    confirm.add_argument("--balances-confirmed", action="store_true", help="Required after review of the account ledger.")
    confirm.add_argument(
        "--fx-workpaper-json",
        help="FX workpaper JSON for non-USD accounts from get-year-end-fx-rate.",
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
