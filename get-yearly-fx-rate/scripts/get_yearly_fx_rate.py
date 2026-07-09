#!/usr/bin/env python3
"""Create proof-backed workpapers for published yearly average FX rates."""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

getcontext().prec = 28

IRS_YEARLY_URL = (
    "https://www.irs.gov/individuals/international-taxpayers/"
    "yearly-average-currency-exchange-rates"
)

IRS_ROWS_BY_CODE = {
    "AFN": ("Afghanistan", "Afghani"),
    "DZD": ("Algeria", "Dinar"),
    "ARS": ("Argentina", "Peso"),
    "AUD": ("Australia", "Dollar"),
    "BHD": ("Bahrain", "Dinar"),
    "BRL": ("Brazil", "Real"),
    "CAD": ("Canada", "Dollar"),
    "KYD": ("Cayman Islands", "Dollar"),
    "CNY": ("China", "Yuan"),
    "DKK": ("Denmark", "Krone"),
    "EGP": ("Egypt", "Pound"),
    "EUR": ("Euro Zone", "Euro"),
    "HKD": ("Hong Kong", "Dollar"),
    "HUF": ("Hungary", "Forint"),
    "ISK": ("Iceland", "Krona"),
    "INR": ("India", "Rupee"),
    "IQD": ("Iraq", "Dinar"),
    "ILS": ("Israel", "New Shekel"),
    "JPY": ("Japan", "Yen"),
    "LBP": ("Lebanon", "Pound"),
    "MXN": ("Mexico", "Peso"),
    "MAD": ("Morocco", "Dirham"),
    "NZD": ("New Zealand", "Dollar"),
    "NOK": ("Norway", "Kroner"),
    "QAR": ("Qatar", "Rial"),
    "RUB": ("Russia", "Ruble"),
    "SAR": ("Saudi Arabia", "Riyal"),
    "SGD": ("Singapore", "Dollar"),
    "ZAR": ("South Africa", "Rand"),
    "KRW": ("South Korean", "Won"),
    "SEK": ("Sweden", "Krona"),
    "CHF": ("Switzerland", "Franc"),
    "TWD": ("Taiwan", "Dollar"),
    "THB": ("Thailand", "Baht"),
    "TND": ("Tunisia", "Dinar"),
    "TRY": ("Turkey", "New Lira"),
    "AED": ("United Arab Emirates", "Dirham"),
    "GBP": ("United Kingdom", "Pound"),
    "VES": ("Venezuela", "Bolivar (Fuerte)"),
}

ALIASES = {
    "afghani": "AFN",
    "algerian dinar": "DZD",
    "argentine peso": "ARS",
    "argentina peso": "ARS",
    "australian dollar": "AUD",
    "bahraini dinar": "BHD",
    "brazilian real": "BRL",
    "canadian dollar": "CAD",
    "canada dollar": "CAD",
    "cayman islands dollar": "KYD",
    "chinese yuan": "CNY",
    "yuan": "CNY",
    "danish krone": "DKK",
    "egyptian pound": "EGP",
    "euro": "EUR",
    "euro zone euro": "EUR",
    "hong kong dollar": "HKD",
    "hungarian forint": "HUF",
    "iceland krona": "ISK",
    "icelandic krona": "ISK",
    "indian rupee": "INR",
    "iraqi dinar": "IQD",
    "israeli shekel": "ILS",
    "new shekel": "ILS",
    "japanese yen": "JPY",
    "yen": "JPY",
    "lebanese pound": "LBP",
    "mexican peso": "MXN",
    "mexico peso": "MXN",
    "moroccan dirham": "MAD",
    "new zealand dollar": "NZD",
    "norwegian krone": "NOK",
    "qatar riyal": "QAR",
    "qatari riyal": "QAR",
    "russian ruble": "RUB",
    "saudi riyal": "SAR",
    "singapore dollar": "SGD",
    "south african rand": "ZAR",
    "rand": "ZAR",
    "south korean won": "KRW",
    "korean won": "KRW",
    "swedish krona": "SEK",
    "swiss franc": "CHF",
    "taiwan dollar": "TWD",
    "thai baht": "THB",
    "baht": "THB",
    "tunisian dinar": "TND",
    "turkish lira": "TRY",
    "turkey lira": "TRY",
    "uae dirham": "AED",
    "united arab emirates dirham": "AED",
    "british pound": "GBP",
    "pound sterling": "GBP",
    "uk pound": "GBP",
    "venezuelan bolivar": "VES",
    "colombian peso": "COP",
    "colombia peso": "COP",
}

AMBIGUOUS_TERMS = {
    "peso": "Use a country or ISO code, for example ARS, MXN, or COP.",
    "dollar": "Use a country or ISO code, for example CAD, AUD, NZD, SGD, HKD, or TWD.",
    "pound": "Use a country or ISO code, for example GBP, EGP, or LBP.",
    "dinar": "Use a country or ISO code, for example DZD, BHD, IQD, or TND.",
    "krone": "Use a country or ISO code, for example DKK or NOK.",
    "krona": "Use a country or ISO code, for example SEK or ISK.",
}

KNOWN_CURRENCY_CODES = set(IRS_ROWS_BY_CODE) | set(ALIASES.values())


class RateError(Exception):
    """User-correctable error."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.in_cell:
            text = clean_text(" ".join(self.current_cell))
            self.current_row.append(text)
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if any(cell for cell in self.current_row):
                self.rows.append(self.current_row)
            self.in_row = False


@dataclass(frozen=True)
class IrsRate:
    code: str
    year: int
    country: str
    currency: str
    rate: Decimal
    source_html: str
    source_url: str


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_currency(raw: str) -> str:
    if not raw or not raw.strip():
        raise RateError("Currency is required.", 2)

    text = clean_text(raw).lower()
    text = text.replace(".", "")
    upper = text.upper()

    if text in AMBIGUOUS_TERMS:
        raise RateError(
            f"Ambiguous currency '{raw}'. {AMBIGUOUS_TERMS[text]}",
            2,
        )

    if text in ALIASES:
        return ALIASES[text]

    if len(upper) == 3 and upper.isalpha():
        return upper

    for alias, code in ALIASES.items():
        if text == alias or text.endswith(" " + alias):
            return code

    raise RateError(
        f"Could not normalize currency '{raw}'. Use an ISO 4217 code such as CAD, EUR, GBP, or COP.",
        2,
    )


def parse_decimal(raw: str) -> Decimal:
    value = clean_text(raw).replace(",", ".")
    value = re.sub(r"[^0-9.\-]", "", value)
    if value.count(".") > 1:
        parts = value.split(".")
        value = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise RateError(f"Could not parse rate value '{raw}'.", 3) from exc


def parse_user_rate(raw: str) -> Decimal:
    """Strict parser for user-supplied --rate.

    parse_decimal() stays lenient for trusted IRS table cells (it reads
    European comma decimals like 0,924). User input is untrusted and
    audit-critical, so reject any ambiguous thousands/decimal formatting
    rather than silently rescaling. A leading '-' is allowed through so the
    existing > 0 guard in build_rate_values owns the value check.
    """
    text = clean_text(raw)
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise RateError(
            f"Rate '{raw}' is not an unambiguous number. Use digits with an optional "
            f"period decimal and no thousands separators, e.g. 4200, 4200.00, or 0.924.",
            3,
        )
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise RateError(f"Could not parse rate value '{raw}'.", 3) from exc


def parse_yearly_irs_table(html: str) -> list[dict[str, object]]:
    parser = TableParser()
    parser.feed(html)

    rows = parser.rows
    table: list[dict[str, object]] = []
    years: list[int] | None = None

    for row in rows:
        lower = [cell.lower() for cell in row]
        if "country" in lower and "currency" in lower:
            country_idx = lower.index("country")
            currency_idx = lower.index("currency")
            years = []
            for cell in row[currency_idx + 1 :]:
                if re.fullmatch(r"\d{4}", cell):
                    years.append(int(cell))
            continue

        if years and len(row) >= 2 + len(years):
            country = row[0]
            currency = row[1]
            rates = {}
            for year, cell in zip(years, row[2:]):
                rates[year] = parse_decimal(cell)
            table.append({"country": country, "currency": currency, "rates": rates})

    if not table:
        table = parse_yearly_irs_text_fallback(html)

    return table


def parse_yearly_irs_text_fallback(html: str) -> list[dict[str, object]]:
    text = re.sub(r"<[^>]+>", " ", html)
    text = clean_text(text)
    header = re.search(r"Country Currency ((?:20\d{2} ?)+)", text)
    if not header:
        return []

    years = [int(y) for y in re.findall(r"20\d{2}", header.group(1))]
    if not years:
        return []

    known_rows = sorted(
        IRS_ROWS_BY_CODE.items(),
        key=lambda item: len(item[1][0] + " " + item[1][1]),
        reverse=True,
    )
    table: list[dict[str, object]] = []

    for _code, (country, currency) in known_rows:
        pattern = re.escape(country) + r"\s+" + re.escape(currency)
        match = re.search(pattern + r"\s+((?:[0-9.,]+\s+){0,%d}[0-9.,]+)" % (len(years) - 1), text)
        if not match:
            continue
        values = clean_text(match.group(1)).split()
        if len(values) < len(years):
            continue
        rates = {year: parse_decimal(value) for year, value in zip(years, values)}
        table.append({"country": country, "currency": currency, "rates": rates})

    return table


def load_html(source_url: str, html_file: str | None) -> tuple[str, str]:
    if html_file:
        path = Path(html_file)
        return path.read_text(encoding="utf-8"), str(path.resolve())

    request = Request(
        source_url,
        headers={"User-Agent": "get-yearly-fx-rate/1.0 (+tax support workpaper)"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), source_url
    except URLError as exc:
        raise RateError(f"Could not fetch IRS yearly average page: {exc}", 5) from exc


def _norm_tokens(label: str) -> list[str]:
    label = re.sub(r"\(.*?\)", " ", label.lower())  # drop parentheticals like "(Fuerte)"
    label = re.sub(r"[^a-z0-9 ]", " ", label)
    return [tok for tok in label.split() if tok]


def labels_match(expected: str, candidate: str) -> bool:
    """Return True when two IRS country/currency labels refer to the same thing.

    The IRS table wording drifts over time (South Korea/South Korean,
    Krone/Kroner, New Lira/Lira, Bolivar (Fuerte)/Bolivar). Match on
    normalized tokens with prefix tolerance so a minor relabel does not
    silently break a lookup, while keeping distinct currencies apart
    (Rial vs Riyal, Iceland Krona vs Sweden Krona are still disjoint by
    their country token). Both find_irs_rate and map-check use this, so a
    clean map-check guarantees the lookups it validates.
    """
    expected_tokens = _norm_tokens(expected)
    candidate_tokens = _norm_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return False
    short, long = sorted((expected_tokens, candidate_tokens), key=len)
    used = [False] * len(long)
    for stok in short:
        matched = False
        for i, ltok in enumerate(long):
            if used[i]:
                continue
            if stok == ltok or (len(stok) >= 3 and len(ltok) >= 3 and (stok.startswith(ltok) or ltok.startswith(stok))):
                used[i] = True
                matched = True
                break
        if not matched:
            return False
    return True


def find_irs_rate(currency_raw: str, year: int, html: str, source_url: str) -> IrsRate:
    code = normalize_currency(currency_raw)
    if code not in IRS_ROWS_BY_CODE:
        raise RateError(
            f"No IRS yearly-average table mapping is known for {code}. Do not calculate an annual average from daily/monthly/quarterly data; use a published annual average source and run the manual command.",
            4,
        )

    country, currency = IRS_ROWS_BY_CODE[code]
    table = parse_yearly_irs_table(html)
    if not table:
        raise RateError("Could not parse the IRS yearly-average table from the source page.", 3)

    row = None
    for candidate in table:
        if labels_match(country, str(candidate["country"])) and labels_match(currency, str(candidate["currency"])):
            row = candidate
            break

    if not row:
        raise RateError(
            f"The IRS yearly-average table was parsed, but no row matched {code} ({country} {currency}). Use a published annual average source and run the manual command.",
            4,
        )

    rates = row["rates"]
    assert isinstance(rates, dict)
    if year not in rates:
        available = ", ".join(str(item) for item in sorted(rates.keys(), reverse=True))
        raise RateError(
            f"The IRS table does not list {year} for {code}. Available years in the parsed row: {available}.",
            4,
        )

    return IrsRate(
        code=code,
        year=year,
        country=str(row["country"]),
        currency=str(row["currency"]),
        rate=rates[year],
        source_html=html,
        source_url=source_url,
    )


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"


def fmt_decimal(value: Decimal, max_places: int = 12) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if "." in text and len(text.split(".", 1)[1]) > max_places:
        quant = Decimal("1").scaleb(-max_places)
        text = format(value.quantize(quant), "f").rstrip("0").rstrip(".")
    return text


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def escape_pdf_text(value: str) -> str:
    normalized = clean_text(str(value))
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_text(value: str, width: int = 92) -> list[str]:
    max_width = max(8, width)
    words = clean_text(str(value)).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        chunks = [word[index : index + max_width] for index in range(0, len(word), max_width)] or [word]
        for chunk in chunks:
            if not current:
                current = chunk
            elif len(current) + 1 + len(chunk) <= max_width:
                current += " " + chunk
            else:
                lines.append(current)
                current = chunk
    if current:
        lines.append(current)
    return lines


PDF_PAGE_WIDTH = 612
PDF_PAGE_HEIGHT = 792
PDF_MARGIN_X = 54
PDF_INNER_WIDTH = PDF_PAGE_WIDTH - (PDF_MARGIN_X * 2)
PDF_BOTTOM_MARGIN = 54
PDF_COLOR_INK = (0.105, 0.145, 0.215)
PDF_COLOR_MUTED = (0.365, 0.415, 0.485)
PDF_COLOR_RULE = (0.815, 0.840, 0.880)
PDF_COLOR_PANEL = (0.965, 0.978, 0.992)
PDF_COLOR_ACCENT = (0.075, 0.265, 0.455)
PDF_COLOR_WHITE = (1.000, 1.000, 1.000)


def pdf_num(value: float | int) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def pdf_color(rgb: tuple[float, float, float], operator: str) -> str:
    return f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} {operator}"


def pdf_rect(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    fill: tuple[float, float, float] | None = None,
    stroke: tuple[float, float, float] | None = None,
    line_width: float = 1,
) -> str:
    commands = ["q"]
    if fill:
        commands.append(pdf_color(fill, "rg"))
    if stroke:
        commands.append(pdf_color(stroke, "RG"))
        commands.append(f"{pdf_num(line_width)} w")
    commands.append(f"{pdf_num(x)} {pdf_num(y)} {pdf_num(width)} {pdf_num(height)} re")
    if fill and stroke:
        commands.append("B")
    elif fill:
        commands.append("f")
    else:
        commands.append("S")
    commands.append("Q")
    return "\n".join(commands)


def pdf_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    stroke: tuple[float, float, float] = PDF_COLOR_RULE,
    line_width: float = 0.7,
) -> str:
    return "\n".join(
        [
            "q",
            pdf_color(stroke, "RG"),
            f"{pdf_num(line_width)} w",
            f"{pdf_num(x1)} {pdf_num(y1)} m",
            f"{pdf_num(x2)} {pdf_num(y2)} l",
            "S",
            "Q",
        ]
    )


def pdf_text(
    text: object,
    x: float,
    y: float,
    *,
    font: str = "F1",
    size: float = 10,
    color: tuple[float, float, float] = PDF_COLOR_INK,
) -> str:
    if text is None:
        text = ""
    return "\n".join(
        [
            "BT",
            pdf_color(color, "rg"),
            f"/{font} {pdf_num(size)} Tf",
            f"1 0 0 1 {pdf_num(x)} {pdf_num(y)} Tm",
            f"({escape_pdf_text(str(text))}) Tj",
            "ET",
        ]
    )


def wrap_for_pdf(value: object, max_width: float, font_size: float) -> list[str]:
    char_width = max(font_size * 0.52, 1)
    return wrap_text(str(value), max(12, int(max_width / char_width)))


class WorkpaperPdfRenderer:
    def __init__(self, workpaper: dict[str, object]) -> None:
        self.workpaper = workpaper
        self.pages: list[list[str]] = []
        self.commands: list[str] = []
        self.y = 0.0
        self.new_page(first=True)

    def add(self, command: str) -> None:
        if command:
            self.commands.append(command)

    def new_page(self, *, first: bool = False) -> None:
        self.commands = []
        self.pages.append(self.commands)
        if first:
            self.draw_cover_header()
            self.y = 646
        else:
            self.draw_continuation_header()
            self.y = 724

    def ensure_space(self, needed: float) -> None:
        if self.y - needed < PDF_BOTTOM_MARGIN:
            self.new_page()

    def draw_cover_header(self) -> None:
        currency = self.workpaper["currency"]
        year = self.workpaper["year"]
        self.add(pdf_rect(0, 676, PDF_PAGE_WIDTH, 116, fill=PDF_COLOR_PANEL))
        self.add(pdf_rect(0, 784, PDF_PAGE_WIDTH, 8, fill=PDF_COLOR_ACCENT))
        self.add(pdf_text("Yearly FX Rate Workpaper", PDF_MARGIN_X, 742, font="F2", size=22))
        self.add(
            pdf_text(
                "Published annual average exchange-rate support",
                PDF_MARGIN_X,
                720,
                size=10,
                color=PDF_COLOR_MUTED,
            )
        )
        self.add(pdf_text(f"{currency} | {year}", PDF_MARGIN_X, 696, font="F2", size=11, color=PDF_COLOR_ACCENT))
        self.add(pdf_rect(375, 734, 183, 24, fill=PDF_COLOR_ACCENT))
        self.add(pdf_text("RETAINED SUPPORT WORKPAPER", 389, 742, font="F2", size=8, color=PDF_COLOR_WHITE))

    def draw_continuation_header(self) -> None:
        self.add(pdf_rect(0, 762, PDF_PAGE_WIDTH, 30, fill=PDF_COLOR_PANEL))
        self.add(pdf_rect(0, 788, PDF_PAGE_WIDTH, 4, fill=PDF_COLOR_ACCENT))
        self.add(
            pdf_text(
                f"{self.workpaper['currency']} {self.workpaper['year']} Yearly FX Rate Workpaper",
                PDF_MARGIN_X,
                773,
                font="F2",
                size=10,
                color=PDF_COLOR_ACCENT,
            )
        )

    def draw_rate_card(self) -> None:
        height = 104
        self.ensure_space(height + 20)
        top = self.y
        bottom = top - height
        self.add(pdf_rect(PDF_MARGIN_X, bottom, PDF_INNER_WIDTH, height, fill=PDF_COLOR_WHITE, stroke=PDF_COLOR_RULE))
        self.add(pdf_rect(PDF_MARGIN_X, top - 6, PDF_INNER_WIDTH, 6, fill=PDF_COLOR_ACCENT))
        self.add(pdf_text("RATE", PDF_MARGIN_X + 20, top - 27, font="F2", size=8, color=PDF_COLOR_MUTED))
        self.add(
            pdf_text(
                f"1 USD = {self.workpaper['foreign_per_usd']} {self.workpaper['currency']}",
                PDF_MARGIN_X + 20,
                top - 58,
                font="F2",
                size=23,
            )
        )
        self.add(pdf_text("yearly average", PDF_MARGIN_X + 20, top - 80, size=10, color=PDF_COLOR_MUTED))
        self.add(
            pdf_text(
                f"Reciprocal: 1 {self.workpaper['currency']} = {self.workpaper['usd_per_foreign']} USD",
                PDF_MARGIN_X + 235,
                top - 80,
                font="F2",
                size=10,
                color=PDF_COLOR_ACCENT,
            )
        )
        self.y = bottom - 24

    def draw_section_title(self, title: str) -> None:
        self.ensure_space(34)
        self.add(pdf_text(title.upper(), PDF_MARGIN_X, self.y, font="F2", size=10.5, color=PDF_COLOR_ACCENT))
        self.add(pdf_line(PDF_MARGIN_X, self.y - 9, PDF_MARGIN_X + PDF_INNER_WIDTH, self.y - 9))
        self.y -= 26

    def draw_key_value_rows(self, rows: list[tuple[str, object]], *, continuation_title: str | None = None) -> None:
        label_x = PDF_MARGIN_X
        value_x = PDF_MARGIN_X + 126
        value_width = PDF_INNER_WIDTH - 126
        for label, value in rows:
            value_lines = wrap_for_pdf(value, value_width, 9)
            row_height = max(23, (len(value_lines) * 11) + 10)
            if self.y - (row_height + 4) < PDF_BOTTOM_MARGIN:
                self.new_page()
                if continuation_title:
                    self.draw_section_title(f"{continuation_title} continued")
            top = self.y
            baseline = top - 15
            self.add(pdf_text(label, label_x, baseline, font="F2", size=8, color=PDF_COLOR_MUTED))
            for index, line in enumerate(value_lines):
                self.add(pdf_text(line, value_x, baseline - (index * 11), size=9, color=PDF_COLOR_INK))
            self.add(pdf_line(label_x, top - row_height, label_x + PDF_INNER_WIDTH, top - row_height, line_width=0.45))
            self.y = top - row_height
        self.y -= 12

    def draw_bullets(self, items: Iterable[object]) -> None:
        for item in items:
            lines = wrap_for_pdf(item, PDF_INNER_WIDTH - 18, 9)
            block_height = max(16, len(lines) * 11)
            self.ensure_space(block_height + 4)
            baseline = self.y - 10
            self.add(pdf_text("-", PDF_MARGIN_X, baseline, font="F2", size=9, color=PDF_COLOR_ACCENT))
            for index, line in enumerate(lines):
                self.add(pdf_text(line, PDF_MARGIN_X + 16, baseline - (index * 11), size=9))
            self.y -= block_height + 5
        self.y -= 8

    def draw_footer(self) -> None:
        total = len(self.pages)
        for index, commands in enumerate(self.pages, start=1):
            commands.append(pdf_line(PDF_MARGIN_X, 36, PDF_MARGIN_X + PDF_INNER_WIDTH, 36, line_width=0.45))
            commands.append(
                pdf_text(
                    f"get-yearly-fx-rate support workpaper | Page {index} of {total}",
                    PDF_MARGIN_X,
                    22,
                    size=8,
                    color=PDF_COLOR_MUTED,
                )
            )

    def render(self) -> list[list[str]]:
        source = self.workpaper["source"]
        proof = self.workpaper["proof"]
        assert isinstance(source, dict)
        assert isinstance(proof, dict)
        saved_files = proof.get("saved_files", [])
        limitations = proof.get("limitations", [])
        caveats = self.workpaper.get("caveats", [])

        self.draw_rate_card()
        self.draw_section_title("Rate Details")
        self.draw_key_value_rows(
            [
                ("Currency", self.workpaper["currency"]),
                ("Year", self.workpaper["year"]),
                ("Published rate", f"{self.workpaper['rate']} ({self.workpaper['rate_direction']})"),
                ("Final display", f"1 USD = {self.workpaper['foreign_per_usd']} {self.workpaper['currency']}"),
                ("Reciprocal", f"1 {self.workpaper['currency']} = {self.workpaper['usd_per_foreign']} USD"),
            ],
            continuation_title="Rate Details",
        )

        self.draw_section_title("Source")
        self.draw_key_value_rows(
            [
                ("Title", source.get("title")),
                ("URL", source.get("url")),
                ("Category", source.get("category")),
                ("Retrieved", source.get("retrieved")),
                ("Note", source.get("note")),
            ],
            continuation_title="Source",
        )

        self.draw_section_title("Source Proof")
        proof_rows: list[tuple[str, object]] = []
        if isinstance(saved_files, list) and saved_files:
            for index, item in enumerate(saved_files, start=1):
                if isinstance(item, dict):
                    filename = item.get("filename") or Path(str(item.get("path", ""))).name
                    proof_rows.append((f"Saved source {index}", f"{filename} (retained in this proof packet)"))
                    proof_rows.append((f"SHA-256 {index}", item.get("sha256")))
            proof_rows.append(("Reviewer note", "Provide this PDF together with the saved source proof file(s); local computer paths are not required to verify the source hash."))
        else:
            proof_rows.append(("Source proof", "No local source artifact was saved; see proof limitations."))
        self.draw_key_value_rows(proof_rows, continuation_title="Source Proof")

        if isinstance(limitations, list) and limitations:
            self.draw_section_title("Proof Limitations")
            self.draw_bullets(limitations)

        if isinstance(caveats, list) and caveats:
            self.draw_section_title("Caveats")
            self.draw_bullets(caveats)

        self.draw_footer()
        return self.pages


def pdf_document_bytes(pages: list[list[str]], *, title: str) -> bytes:
    objects: list[bytes] = []
    page_object_ids: list[int] = []

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # pages object placeholder
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>")

    for page_commands in pages:
        content = "\n".join(page_commands).encode("latin-1", errors="replace")
        content_id = len(objects) + 1
        objects.append(
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream"
        )
        page_id = len(objects) + 1
        page_object_ids.append(page_id)
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PDF_PAGE_WIDTH} {PDF_PAGE_HEIGHT}] "
                f"/Resources << /ProcSet [/PDF /Text] /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )

    kids = " ".join(f"{page_id} 0 R" for page_id in page_object_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_ids)} >>".encode("ascii")
    info_id = len(objects) + 1
    objects.append(
        (
            f"<< /Title ({escape_pdf_text(title)}) /Creator (get-yearly-fx-rate) "
            f"/Producer (get-yearly-fx-rate standard-library PDF renderer) >>"
        ).encode("latin-1", errors="replace")
    )

    output = io.BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, payload in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{object_id} 0 obj\n".encode("ascii"))
        output.write(payload)
        output.write(b"\nendobj\n")

    xref_start = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF\n"
        ).encode("ascii")
    )
    return output.getvalue()


def render_workpaper_pdf_bytes(workpaper: dict[str, object]) -> bytes:
    renderer = WorkpaperPdfRenderer(workpaper)
    pages = renderer.render()
    title = f"{workpaper['currency']} {workpaper['year']} Yearly FX Rate Workpaper"
    return pdf_document_bytes(pages, title=title)


def today_iso() -> str:
    return _dt.date.today().isoformat()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def as_abs(path: Path) -> str:
    return str(path.resolve())


def build_rate_values(rate: Decimal, direction: str) -> tuple[Decimal, Decimal]:
    if rate <= 0:
        raise RateError("Rate must be greater than zero.", 2)
    if direction == "foreign-per-usd":
        return rate, Decimal(1) / rate
    if direction == "usd-per-foreign":
        return Decimal(1) / rate, rate
    raise RateError(f"Unsupported rate direction: {direction}", 2)


def create_workpaper(
    *,
    output_root: str,
    currency_code: str,
    year: int,
    rate: Decimal,
    rate_direction: str,
    source_title: str,
    source_url: str,
    retrieval_date: str,
    source_note: str,
    source_category: str,
    saved_proofs: Iterable[Path],
    proof_limitations: list[str],
) -> dict[str, object]:
    foreign_per_usd, usd_per_foreign = build_rate_values(rate, rate_direction)
    source_slug = slugify(source_title)
    folder = Path(output_root) / f"{currency_code.lower()}-{year}-{source_slug}"
    if (folder / "workpaper.json").exists():
        print(f"note: replacing existing workpaper packet at {folder}", file=sys.stderr)
    folder.mkdir(parents=True, exist_ok=True)

    proof_entries = []
    for index, original_proof_path in enumerate(saved_proofs, start=1):
        proof_path = original_proof_path
        if proof_path.exists() and proof_path.parent.resolve() != folder.resolve():
            copied_name = f"source-proof-{index}{proof_path.suffix}"
            copied_path = folder / copied_name
            shutil.copy2(proof_path, copied_path)
            proof_path = copied_path
        if proof_path.exists():
            proof_entries.append(
                {
                    "filename": proof_path.name,
                    "packet_relative_path": proof_path.name,
                    "path": as_abs(proof_path),
                    "sha256": sha256_file(proof_path),
                }
            )

    workpaper = {
        "skill": "get-yearly-fx-rate",
        "currency": currency_code,
        "year": year,
        "rate": fmt_decimal(rate),
        "rate_direction": rate_direction,
        "foreign_per_usd": fmt_decimal(foreign_per_usd),
        "usd_per_foreign": fmt_decimal(usd_per_foreign),
        "source": {
            "title": source_title,
            "url": source_url,
            "category": source_category,
            "retrieved": retrieval_date,
            "note": source_note,
        },
        "proof": {
            "workpaper_md": as_abs(folder / "workpaper.md"),
            "workpaper_json": as_abs(folder / "workpaper.json"),
            "workpaper_pdf": as_abs(folder / "workpaper.pdf"),
            "saved_files": proof_entries,
            "limitations": proof_limitations,
        },
        "caveats": [
            "This is a retained support workpaper, not tax advice and not an official IRS determination.",
            "Do not describe this rate as IRS-approved.",
        ],
    }

    markdown_text = render_workpaper_md(workpaper)
    write_text(folder / "workpaper.md", markdown_text)
    pdf_path = folder / "workpaper.pdf"
    pdf_path.write_bytes(render_workpaper_pdf_bytes(workpaper))
    workpaper["proof"]["workpaper_pdf_sha256"] = sha256_file(pdf_path)  # type: ignore[index]
    write_text(folder / "workpaper.json", json.dumps(workpaper, indent=2, sort_keys=True) + "\n")

    workpaper["proof"]["workpaper_md"] = as_abs(folder / "workpaper.md")  # type: ignore[index]
    workpaper["proof"]["workpaper_json"] = as_abs(folder / "workpaper.json")  # type: ignore[index]
    workpaper["proof"]["workpaper_pdf"] = as_abs(pdf_path)  # type: ignore[index]
    return workpaper


def render_workpaper_md(workpaper: dict[str, object]) -> str:
    source = workpaper["source"]
    proof = workpaper["proof"]
    assert isinstance(source, dict)
    assert isinstance(proof, dict)

    saved_files = proof.get("saved_files", [])
    limitations = proof.get("limitations", [])

    lines = [
        "# Yearly FX Rate Workpaper",
        "",
        f"Currency: {workpaper['currency']}",
        f"Year: {workpaper['year']}",
        f"Rate: 1 USD = {workpaper['foreign_per_usd']} {workpaper['currency']} yearly average",
        f"Reciprocal: 1 {workpaper['currency']} = {workpaper['usd_per_foreign']} USD",
        f"Source: {source.get('title')}",
        f"Source URL: {source.get('url')}",
        f"Source category: {source.get('category')}",
        f"Retrieved: {source.get('retrieved')}",
        f"Source note: {source.get('note')}",
        "",
        "## Proof Files",
        f"- Workpaper PDF: {proof.get('workpaper_pdf')}",
    ]

    if saved_files:
        for item in saved_files:
            assert isinstance(item, dict)
            lines.append(f"- {item.get('path')} (sha256: {item.get('sha256')})")
    else:
        lines.append("- No local source artifact was saved; see proof limitations.")

    if limitations:
        lines.extend(["", "## Proof Limitations"])
        for item in limitations:
            lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Caveats",
            "- This is a retained support workpaper, not tax advice and not an official IRS determination.",
            "- Do not describe this rate as IRS-approved.",
            "",
        ]
    )
    return "\n".join(lines)


def markdown_file_link(label: str, path: object) -> str:
    target = clean_text(str(path))
    safe_label = clean_text(label).replace("[", "\\[").replace("]", "\\]")
    safe_target = target.replace(">", "%3E")
    return f"[{safe_label}](<{safe_target}>)"


def artifact_links(workpaper: dict[str, object]) -> str:
    proof = workpaper["proof"]
    assert isinstance(proof, dict)
    links: list[str] = []
    seen: set[str] = set()

    for key, label in (
        ("workpaper_pdf", "workpaper.pdf"),
        ("workpaper_md", "workpaper.md"),
        ("workpaper_json", "workpaper.json"),
    ):
        path = proof.get(key)
        if path and str(path) not in seen:
            links.append(markdown_file_link(label, path))
            seen.add(str(path))

    saved_files = proof.get("saved_files", [])
    if isinstance(saved_files, list):
        for item in saved_files:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if path and str(path) not in seen:
                links.append(markdown_file_link(Path(str(path)).name, path))
                seen.add(str(path))

    return ", ".join(links)


def final_text(workpaper: dict[str, object]) -> str:
    source = workpaper["source"]
    proof = workpaper["proof"]
    assert isinstance(source, dict)
    assert isinstance(proof, dict)
    pdf_path = proof.get("workpaper_pdf")
    proof_link = markdown_file_link("workpaper.pdf", pdf_path) if pdf_path else ""
    return "\n".join(
        [
            f"Rate: 1 USD = {workpaper['foreign_per_usd']} {workpaper['currency']} yearly average",
            f"Reciprocal: 1 {workpaper['currency']} = {workpaper['usd_per_foreign']} USD",
            f"Source: {source.get('title')}, {source.get('url')}, retrieved {source.get('retrieved')}",
            f"Proof: {proof_link}",
            f"Artifacts: {artifact_links(workpaper)}",
        ]
    )


def command_lookup(args: argparse.Namespace) -> int:
    code = normalize_currency(args.currency)
    if code not in IRS_ROWS_BY_CODE:
        raise RateError(
            f"The IRS yearly-average table does not list {code}. Search for a published annual average from a government, tax authority, central bank, bank, or reputable FX provider; do not calculate an annual average from daily/monthly/quarterly data. Then run the manual command with the published rate/source.",
            4,
        )

    html, _source_ref = load_html(args.source_url, args.html_file)
    rate = find_irs_rate(code, args.year, html, args.source_url)

    source_slug = "irs-yearly-average-currency-exchange-rates"
    folder = Path(args.output_root) / f"{code.lower()}-{args.year}-{source_slug}"
    folder.mkdir(parents=True, exist_ok=True)
    html_path = folder / "irs-yearly-average-source.html"
    html_bytes = html.encode("utf-8")
    html_path.write_bytes(html_bytes)

    fetch_mode = "supplied local HTML file" if args.html_file else "live fetch from source URL"
    note = (
        f"IRS yearly average table row: {rate.country} {rate.currency}; "
        f"source URL {rate.source_url}; snapshot origin: {fetch_mode}; "
        f"retained HTML snapshot sha256 {sha256_bytes(html_bytes)}."
    )
    workpaper = create_workpaper(
        output_root=args.output_root,
        currency_code=code,
        year=args.year,
        rate=rate.rate,
        rate_direction="foreign-per-usd",
        source_title="IRS Yearly average currency exchange rates",
        source_url=rate.source_url,
        retrieval_date=args.retrieved or today_iso(),
        source_note=note,
        source_category="IRS yearly average table",
        saved_proofs=[html_path],
        proof_limitations=["HTML source snapshot retained; screenshot/PDF proof was not generated by this dependency-free script."],
    )
    print(final_text(workpaper))
    return 0


def command_manual(args: argparse.Namespace) -> int:
    code = normalize_currency(args.currency)
    if code not in KNOWN_CURRENCY_CODES and not args.allow_unknown_code:
        raise RateError(
            f"Currency code {code} is not in this skill's known IRS/alias set. If it is a "
            f"real ISO 4217 code, re-run with --allow-unknown-code; otherwise fix the currency.",
            2,
        )
    if not args.annual_average_confirmed:
        raise RateError("Pass --annual-average-confirmed after verifying the source labels the value as yearly/annual average.", 2)
    rate = parse_user_rate(args.rate)
    proof_path = Path(args.proof_file)
    if not proof_path.exists():
        raise RateError(f"Proof file does not exist: {proof_path}", 2)

    workpaper = create_workpaper(
        output_root=args.output_root,
        currency_code=code,
        year=args.year,
        rate=rate,
        rate_direction=args.rate_direction,
        source_title=args.source_title,
        source_url=args.source_url,
        retrieval_date=args.retrieved or today_iso(),
        source_note=args.source_note,
        source_category=args.source_category,
        saved_proofs=[proof_path],
        proof_limitations=[],
    )
    print(final_text(workpaper))
    return 0


def command_map_check(args: argparse.Namespace) -> int:
    html, source_ref = load_html(args.source_url, args.html_file)
    table = parse_yearly_irs_table(html)
    if not table:
        raise RateError("Could not parse the IRS yearly-average table from the source page.", 3)

    unmapped = []
    for row in table:
        row_country = str(row["country"])
        row_currency = str(row["currency"])
        if not any(
            labels_match(map_country, row_country) and labels_match(map_currency, row_currency)
            for map_country, map_currency in IRS_ROWS_BY_CODE.values()
        ):
            unmapped.append(f"{row_country} {row_currency}")

    print(f"Parsed IRS yearly-average rows: {len(table)} from {source_ref}")
    print(f"Mapped rows: {len(table) - len(unmapped)}")
    if unmapped:
        print("Unmapped rows:")
        for item in unmapped:
            print(f"- {item}")
        return 4
    print("All parsed rows are represented in IRS_ROWS_BY_CODE.")
    return 0


def assert_workpaper_pdf(
    path: Path,
    required_terms: Iterable[str],
    forbidden_terms: Iterable[str] = (),
) -> None:
    data = path.read_bytes()
    assert data.startswith(b"%PDF-1."), path
    assert data.rstrip().endswith(b"%%EOF"), path
    assert b"xref" in data, path
    assert b"/Type /Page /Parent" in data, path
    assert b"/Helvetica-Bold" in data, path
    assert len(data) > 3000, path
    for term in required_terms:
        encoded = clean_text(term).encode("latin-1", errors="replace")
        assert encoded in data, term
    for term in forbidden_terms:
        encoded = clean_text(term).encode("latin-1", errors="replace")
        assert encoded not in data, term


def command_self_test(_args: argparse.Namespace) -> int:
    sample_html = """
    <table>
      <tr><th>Country</th><th>Currency</th><th>2025</th><th>2024</th><th>2023</th></tr>
      <tr><td>Canada</td><td>Dollar</td><td>1.398</td><td>1.370</td><td>1.350</td></tr>
      <tr><td>Euro Zone</td><td>Euro</td><td>0.886</td><td>0,924</td><td>0.924</td></tr>
      <tr><td>Mexico</td><td>Peso</td><td>19.212</td><td>18.330</td><td>17.733</td></tr>
    </table>
    """
    table = parse_yearly_irs_table(sample_html)
    assert len(table) == 3, table
    assert find_irs_rate("CAD", 2024, sample_html, IRS_YEARLY_URL).rate == Decimal("1.370")
    assert find_irs_rate("euro", 2024, sample_html, IRS_YEARLY_URL).rate == Decimal("0.924")

    # Patch 1: label matching tolerates IRS wording drift but keeps currencies disjoint
    assert labels_match("South Korean", "South Korea")
    assert labels_match("Kroner", "Krone")
    assert labels_match("New Lira", "Lira")
    assert labels_match("Bolivar (Fuerte)", "Bolivar")
    assert labels_match("New Shekel", "Shekel")
    assert not labels_match("Denmark", "Norway")
    assert not labels_match("Rial", "Riyal")
    assert not labels_match("Won", "Yen")
    drift_html = (
        "<table><tr><th>Country</th><th>Currency</th><th>2024</th></tr>"
        "<tr><td>South Korea</td><td>Won</td><td>1360</td></tr>"
        "<tr><td>Norway</td><td>Krone</td><td>10.5</td></tr>"
        "<tr><td>Turkey</td><td>Lira</td><td>32.9</td></tr></table>"
    )
    assert find_irs_rate("KRW", 2024, drift_html, IRS_YEARLY_URL).rate == Decimal("1360")
    assert find_irs_rate("NOK", 2024, drift_html, IRS_YEARLY_URL).rate == Decimal("10.5")
    assert find_irs_rate("TRY", 2024, drift_html, IRS_YEARLY_URL).rate == Decimal("32.9")
    sweden_html = (
        "<table><tr><th>Country</th><th>Currency</th><th>2024</th></tr>"
        "<tr><td>Sweden</td><td>Krona</td><td>10.6</td></tr></table>"
    )
    try:
        find_irs_rate("ISK", 2024, sweden_html, IRS_YEARLY_URL)
    except RateError as exc:
        assert exc.code == 4
    else:
        raise AssertionError("ISK must not match a Sweden Krona row")

    # Patch 3: table-less HTML resolves through the text fallback parser
    fallback_html = "<div>Country Currency 2024 2023</div><p>Canada Dollar 1.370 1.350</p>"
    assert any(r["country"] == "Canada" for r in parse_yearly_irs_text_fallback(fallback_html))
    assert find_irs_rate("CAD", 2024, fallback_html, IRS_YEARLY_URL).rate == Decimal("1.370")

    assert normalize_currency("yen") == "JPY"
    assert normalize_currency("YEN") == "JPY"
    assert normalize_currency("CAD") == "CAD"
    assert normalize_currency("eur") == "EUR"

    assert parse_user_rate("4200") == Decimal("4200")
    assert parse_user_rate("4200.00") == Decimal("4200.00")
    assert parse_user_rate("0.924") == Decimal("0.924")
    for bad in ("4,200", "0,924", "4.200,00", "4 200", "1e3", "abc", "4.", ""):
        try:
            parse_user_rate(bad)
        except RateError as exc:
            assert exc.code == 3, bad
        else:
            raise AssertionError(f"parse_user_rate should reject {bad!r}")

    try:
        normalize_currency("peso")
    except RateError as exc:
        assert exc.code == 2
    else:
        raise AssertionError("ambiguous peso should fail")

    try:
        find_irs_rate("COP", 2024, sample_html, IRS_YEARLY_URL)
    except RateError as exc:
        assert exc.code == 4
    else:
        raise AssertionError("COP should require a non-IRS published annual source")

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "irs.html"
        html_path.write_text(sample_html, encoding="utf-8")
        lookup_args = argparse.Namespace(
            currency="CAD",
            year=2024,
            output_root=str(Path(tmp) / "proof"),
            source_url=IRS_YEARLY_URL,
            html_file=str(html_path),
            retrieved="2026-07-03",
        )
        lookup_output = io.StringIO()
        with contextlib.redirect_stdout(lookup_output):
            command_lookup(lookup_args)
        lookup_text = lookup_output.getvalue()
        assert "Proof: [workpaper.pdf](<" in lookup_text
        assert "Artifacts: [workpaper.pdf](<" in lookup_text
        assert "[workpaper.md](<" in lookup_text
        assert "[workpaper.json](<" in lookup_text
        assert "[irs-yearly-average-source.html](<" in lookup_text
        workpaper = next((Path(tmp) / "proof").glob("cad-2024-*/workpaper.json"))
        data = json.loads(workpaper.read_text(encoding="utf-8"))
        assert data["foreign_per_usd"] == "1.37"
        lookup_pdf = Path(data["proof"]["workpaper_pdf"])
        assert_workpaper_pdf(
            lookup_pdf,
            [
                "Yearly FX Rate Workpaper",
                "RETAINED SUPPORT WORKPAPER",
                "1 USD = 1.37 CAD",
                "IRS Yearly average currency exchange rates",
                "SOURCE PROOF",
                "Saved source 1",
                "irs-yearly-average-source.html",
                "retained in this proof packet",
                "SHA-256 1",
                "Reviewer note",
                "local computer",
                "paths are not required",
            ],
            [
                "Workpaper PDF",
                "Workpaper JSON",
                "Workpaper MD",
                "PROOF FILES CONTINUED",
                str(Path(tmp)),
            ],
        )
        assert data["proof"]["workpaper_pdf_sha256"] == sha256_file(lookup_pdf)
        assert data["proof"]["saved_files"][0]["filename"] == "irs-yearly-average-source.html"
        assert data["proof"]["saved_files"][0]["packet_relative_path"] == "irs-yearly-average-source.html"
        assert "supplied local HTML file" in workpaper.read_text(encoding="utf-8")

        rerun_err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(rerun_err):
            command_lookup(lookup_args)
        assert "replacing existing workpaper packet" in rerun_err.getvalue()

        proof_file = Path(tmp) / "proof-source.html"
        proof_file.write_text("<p>published annual average</p>", encoding="utf-8")
        manual_args = argparse.Namespace(
            currency="COP",
            year=2024,
            rate="4200",
            rate_direction="foreign-per-usd",
            source_title="Example Central Bank Annual Average",
            source_url="https://example.test/fx",
            source_category="central bank published annual average",
            source_note="Source labels this as annual average.",
            annual_average_confirmed=True,
            allow_unknown_code=False,
            retrieved="2026-07-03",
            proof_file=str(proof_file),
            output_root=str(Path(tmp) / "manual-proof"),
        )
        manual_output = io.StringIO()
        with contextlib.redirect_stdout(manual_output):
            command_manual(manual_args)
        manual_text = manual_output.getvalue()
        assert "Proof: [workpaper.pdf](<" in manual_text
        assert "Artifacts: [workpaper.pdf](<" in manual_text
        assert "[workpaper.md](<" in manual_text
        assert "[workpaper.json](<" in manual_text
        assert "[source-proof-1.html](<" in manual_text
        manual_workpaper = next((Path(tmp) / "manual-proof").glob("cop-2024-*/workpaper.json"))
        manual_data = json.loads(manual_workpaper.read_text(encoding="utf-8"))
        assert manual_data["foreign_per_usd"] == "4200"
        assert manual_data["proof"]["saved_files"]
        saved_file = manual_data["proof"]["saved_files"][0]
        saved_path = Path(saved_file["path"])
        assert saved_path.exists()
        assert saved_path.parent.resolve() == manual_workpaper.parent.resolve()
        assert saved_file["sha256"] == sha256_file(saved_path)
        assert saved_file["filename"] == "source-proof-1.html"
        assert saved_file["packet_relative_path"] == "source-proof-1.html"
        manual_pdf = Path(manual_data["proof"]["workpaper_pdf"])
        assert_workpaper_pdf(
            manual_pdf,
            [
                "Yearly FX Rate Workpaper",
                "1 USD = 4200 COP",
                "Example Central Bank Annual Average",
                "SOURCE PROOF",
                "Saved source 1",
                "source-proof-1.html",
                "retained in this proof packet",
                "SHA-256 1",
                "Reviewer note",
                "local computer",
                "paths are not required",
            ],
            [
                "Workpaper PDF",
                "Workpaper JSON",
                "Workpaper MD",
                "PROOF FILES CONTINUED",
                str(Path(tmp)),
            ],
        )
        assert manual_data["proof"]["workpaper_pdf_sha256"] == sha256_file(manual_pdf)

        assert _year_arg("2024") == 2024
        for bad in ("24", "1969", "2101", "abc"):
            try:
                _year_arg(bad)
            except argparse.ArgumentTypeError:
                pass
            else:
                raise AssertionError(f"_year_arg should reject {bad!r}")
        assert _iso_date_arg("2026-07-09") == "2026-07-09"
        for bad in ("banana", "2026-13-01", "07/09/2026", "20260709", "2026-7-9"):
            try:
                _iso_date_arg(bad)
            except argparse.ArgumentTypeError:
                pass
            else:
                raise AssertionError(f"_iso_date_arg should reject {bad!r}")

        unknown_args = argparse.Namespace(
            currency="XQZ",
            year=2024,
            rate="1",
            rate_direction="foreign-per-usd",
            source_title="T",
            source_url="https://x",
            source_category="c",
            source_note="n",
            annual_average_confirmed=True,
            allow_unknown_code=False,
            retrieved="2026-07-03",
            proof_file=str(proof_file),
            output_root=str(Path(tmp) / "unknown-proof"),
        )
        try:
            command_manual(unknown_args)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("unknown currency code should require --allow-unknown-code")

        map_check_args = argparse.Namespace(
            source_url=IRS_YEARLY_URL,
            html_file=str(html_path),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_map_check(map_check_args) == 0

    print("self-test passed")
    return 0


def _year_arg(raw: str) -> int:
    try:
        year = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"year must be an integer, got {raw!r}")
    if not (1970 <= year <= 2100):
        raise argparse.ArgumentTypeError(f"year {year} is outside the supported range 1970-2100")
    return year


def _iso_date_arg(raw: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise argparse.ArgumentTypeError(f"retrieved date must be YYYY-MM-DD, got {raw!r}")
    try:
        _dt.date.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"retrieved date must be a real YYYY-MM-DD date, got {raw!r}")
    return raw


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--currency", required=True, help="Currency code or unambiguous currency name.")
    parser.add_argument("--year", required=True, type=_year_arg, help="Calendar/tax year (1970-2100).")
    parser.add_argument("--output-root", default="work/fx-rate-proof", help="Proof output root.")
    parser.add_argument("--retrieved", type=_iso_date_arg, help="Retrieval date YYYY-MM-DD; defaults to today.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lookup = subparsers.add_parser("lookup", help="Fetch/parse IRS yearly-average table and create proof workpaper.")
    add_common_args(lookup)
    lookup.add_argument("--source-url", default=IRS_YEARLY_URL, help="IRS yearly-average source URL.")
    lookup.add_argument("--html-file", help="Use a local HTML file instead of fetching the IRS page.")
    lookup.set_defaults(func=command_lookup)

    manual = subparsers.add_parser("manual", help="Create a proof workpaper for a published non-IRS annual source.")
    add_common_args(manual)
    manual.add_argument("--rate", required=True, help="Published rate value.")
    manual.add_argument(
        "--rate-direction",
        choices=["foreign-per-usd", "usd-per-foreign"],
        default="foreign-per-usd",
        help="Direction of the supplied rate.",
    )
    manual.add_argument("--source-title", required=True, help="Published source title.")
    manual.add_argument("--source-url", required=True, help="Published source URL.")
    manual.add_argument(
        "--source-category",
        default="published annual average",
        help="Source class, e.g. IRS table, central bank annual average, bank annual average.",
    )
    manual.add_argument("--source-note", required=True, help="Note confirming the source labels the value as yearly/annual average.")
    manual.add_argument("--annual-average-confirmed", action="store_true", help="Required confirmation that the source labels the value as a yearly/annual average.")
    manual.add_argument("--allow-unknown-code", action="store_true", help="Confirm a real ISO 4217 code that is not in the skill's known IRS/alias set.")
    manual.add_argument("--proof-file", required=True, help="Local screenshot/PDF/HTML/source proof file to copy, hash, and reference.")
    manual.set_defaults(func=command_manual)

    map_check = subparsers.add_parser("map-check", help="Compare parsed IRS table rows with the hard-coded IRS currency map.")
    map_check.add_argument("--source-url", default=IRS_YEARLY_URL, help="IRS yearly-average source URL.")
    map_check.add_argument("--html-file", help="Use a local HTML file instead of fetching the IRS page.")
    map_check.set_defaults(func=command_map_check)

    self_test = subparsers.add_parser("self-test", help="Run dependency-free parser/workpaper tests.")
    self_test.set_defaults(func=command_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
