#!/usr/bin/env python3
"""Create proof-backed workpapers for year-end USD exchange rates."""

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
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

getcontext().prec = 28

TREASURY_DATASET_URL = (
    "https://fiscaldata.treasury.gov/datasets/treasury-reporting-rates-exchange/"
    "treasury-reporting-rates-of-exchange"
)
TREASURY_API_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v1/accounting/od/rates_of_exchange_source"
)

TREASURY_ROWS_BY_CODE = {
    "AED": {"country": "United Arab Emirates", "currency": "Dirham"},
    "ARS": {"country": "Argentina", "currency": "Peso"},
    "AUD": {"country": "Australia", "currency": "Dollar"},
    "BRL": {"country": "Brazil", "currency": "Real"},
    "CAD": {"country": "Canada", "currency": "Dollar"},
    "CHF": {"country": "Switzerland", "currency": "Franc"},
    "CLP": {"country": "Chile", "currency": "Peso"},
    "CNY": {"country": "China", "currency": "Renminbi"},
    "COP": {"country": "Colombia", "currency": "Peso"},
    "DKK": {"country": "Denmark", "currency": "Krone"},
    "EUR": {"country": None, "currency": "Euro"},
    "GBP": {"country": "United Kingdom", "currency": "Pound"},
    "HKD": {"country": "Hong Kong", "currency": "Dollar"},
    "ILS": {"country": "Israel", "currency": "Shekel"},
    "INR": {"country": "India", "currency": "Rupee"},
    "JPY": {"country": "Japan", "currency": "Yen"},
    "KRW": {"country": "Korea", "currency": "Won"},
    "MXN": {"country": "Mexico", "currency": "Peso"},
    "NOK": {"country": "Norway", "currency": "Krone"},
    "NZD": {"country": "New Zealand", "currency": "Dollar"},
    "PEN": {"country": "Peru", "currency": "Sol"},
    "RUB": {"country": "Russia", "currency": "Ruble"},
    "SEK": {"country": "Sweden", "currency": "Krona"},
    "SGD": {"country": "Singapore", "currency": "Dollar"},
    "TRY": {"country": "Turkey", "currency": "Lira"},
    "TWD": {"country": "Taiwan", "currency": "Dollar"},
    "UYU": {"country": "Uruguay", "currency": "Peso"},
    "ZAR": {"country": "South Africa", "currency": "Rand"},
}

ALIASES = {
    "argentine peso": "ARS",
    "australian dollar": "AUD",
    "brazilian real": "BRL",
    "canadian dollar": "CAD",
    "chilean peso": "CLP",
    "chinese yuan": "CNY",
    "colombian peso": "COP",
    "colombia peso": "COP",
    "danish krone": "DKK",
    "euro": "EUR",
    "british pound": "GBP",
    "pound sterling": "GBP",
    "hong kong dollar": "HKD",
    "israeli shekel": "ILS",
    "indian rupee": "INR",
    "japanese yen": "JPY",
    "yen": "JPY",
    "korean won": "KRW",
    "mexican peso": "MXN",
    "mexico peso": "MXN",
    "norwegian krone": "NOK",
    "new zealand dollar": "NZD",
    "peruvian sol": "PEN",
    "russian ruble": "RUB",
    "swedish krona": "SEK",
    "singapore dollar": "SGD",
    "turkish lira": "TRY",
    "taiwan dollar": "TWD",
    "uae dirham": "AED",
    "emirati dirham": "AED",
    "united arab emirates dirham": "AED",
    "uruguayan peso": "UYU",
    "south african rand": "ZAR",
    "rand": "ZAR",
}

AMBIGUOUS_TERMS = {
    "peso": "Use a country or ISO code, for example COP, MXN, ARS, CLP, or UYU.",
    "dollar": "Use a country or ISO code, for example CAD, AUD, NZD, SGD, HKD, or TWD.",
    "pound": "Use a country or ISO code, for example GBP.",
    "franc": "Use a country or ISO code, for example CHF.",
    "dirham": "Use a country or ISO code, for example AED or MAD.",
    "ruble": "Use a country or ISO code, for example RUB.",
    "krone": "Use a country or ISO code, for example DKK or NOK.",
    "krona": "Use a country or ISO code, for example SEK.",
}

AVERAGE_LANGUAGE = (
    "yearly average",
    "yearly-average",
    "annual average",
    "annual-average",
    "average exchange rate",
)


class RateError(Exception):
    """User-correctable error."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TreasuryRate:
    code: str
    year: int
    year_end_date: str
    country: str
    currency: str
    country_currency_desc: str
    rate: Decimal
    query_url: str
    source_json: str
    record: dict[str, object]
    duplicate_rows: int = 0


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_currency(raw: str) -> str:
    if not raw or not raw.strip():
        raise RateError("Currency is required.", 2)

    text = clean_text(raw).lower().replace(".", "")
    upper = text.upper()
    if len(upper) == 3 and upper.isalpha():
        return upper

    if text in AMBIGUOUS_TERMS:
        raise RateError(f"Ambiguous currency '{raw}'. {AMBIGUOUS_TERMS[text]}", 2)

    if text in ALIASES:
        return ALIASES[text]

    raise RateError(
        f"Could not normalize currency '{raw}'. Use an ISO 4217 code such as COP, CAD, EUR, or GBP.",
        2,
    )


def reject_average_language(*values: object) -> None:
    combined = " ".join(clean_text(value).lower() for value in values if value is not None)
    for phrase in AVERAGE_LANGUAGE:
        if phrase in combined:
            raise RateError(
                "This skill is for year-end rates only. Use get-yearly-fx-rate for yearly/annual averages.",
                2,
            )


def parse_decimal(raw: object) -> Decimal:
    value = clean_text(raw).replace(",", "")
    value = re.sub(r"[^0-9.\-]", "", value)
    if value.count(".") > 1:
        parts = value.split(".")
        value = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise RateError(f"Could not parse rate value '{raw}'.", 3) from exc


def fmt_decimal(value: Decimal, max_places: int = 12) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if "." in text and len(text.split(".", 1)[1]) > max_places:
        quant = Decimal("1").scaleb(-max_places)
        text = format(value.quantize(quant), "f").rstrip("0").rstrip(".")
    return text


def build_rate_values(rate: Decimal, direction: str) -> tuple[Decimal, Decimal]:
    if rate <= 0:
        raise RateError("Rate must be greater than zero.", 2)
    if direction == "foreign-per-usd":
        return rate, Decimal(1) / rate
    if direction == "usd-per-foreign":
        return Decimal(1) / rate, rate
    raise RateError(f"Unsupported rate direction: {direction}", 2)


def today_iso() -> str:
    return _dt.date.today().isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_abs(path: Path) -> str:
    return str(path.resolve())


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"


def treasury_query_url(code: str, year: int, api_url: str) -> str:
    if code not in TREASURY_ROWS_BY_CODE:
        raise RateError(
            f"No Treasury/Fiscal Data row mapping is known for {code}. Use a verified manual year-end source.",
            4,
        )

    mapping = TREASURY_ROWS_BY_CODE[code]
    filters = [
        f"record_date:eq:{year}-12-31",
        f"currency:eq:{mapping['currency']}",
    ]
    if mapping.get("country"):
        filters.append(f"country:eq:{mapping['country']}")
    params = {
        "fields": ",".join(
            [
                "record_date",
                "country",
                "currency",
                "country_currency_desc",
                "exchange_rate",
                "effective_date",
                "src_line_nbr",
                "record_calendar_year",
            ]
        ),
        "filter": ",".join(filters),
        "sort": "country,currency,src_line_nbr",
        "page[size]": "500",
    }
    return api_url + "?" + urlencode(params)


def treasury_rows_url(year: int, api_url: str) -> str:
    params = {
        "fields": ",".join(
            [
                "record_date",
                "country",
                "currency",
                "country_currency_desc",
                "exchange_rate",
                "effective_date",
                "src_line_nbr",
                "record_calendar_year",
            ]
        ),
        "filter": f"record_date:eq:{year}-12-31",
        "sort": "country,currency,src_line_nbr",
        "page[size]": "5000",
    }
    return api_url + "?" + urlencode(params)


def load_json_text(query_url: str, api_file: str | None) -> tuple[str, str]:
    if api_file:
        path = Path(api_file)
        return path.read_text(encoding="utf-8"), as_abs(path)

    request = Request(
        query_url,
        headers={"User-Agent": "get-year-end-fx-rate/1.0 (+FBAR support workpaper)"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), query_url
    except URLError as exc:
        raise RateError(f"Could not fetch Treasury/Fiscal Data API: {exc}", 5) from exc


def parse_json_payload(json_text: str) -> dict[str, object]:
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RateError(f"Could not parse Treasury/Fiscal Data JSON: {exc}", 3) from exc
    if not isinstance(payload, dict):
        raise RateError("Treasury/Fiscal Data response was not a JSON object.", 3)
    return payload


def treasury_records_from_payload(json_text: str) -> list[dict[str, object]]:
    payload = parse_json_payload(json_text)
    records = payload.get("data")
    if not isinstance(records, list):
        raise RateError("Treasury/Fiscal Data response did not contain a data list.", 3)
    return [record for record in records if isinstance(record, dict)]


def treasury_row_pair(record: dict[str, object]) -> tuple[str, str]:
    return (clean_text(record.get("country")).lower(), clean_text(record.get("currency")).lower())


def mapped_treasury_pairs() -> set[tuple[str, str]]:
    pairs = set()
    for mapping in TREASURY_ROWS_BY_CODE.values():
        country = mapping.get("country")
        currency = mapping.get("currency")
        if country and currency:
            pairs.add((clean_text(country).lower(), clean_text(currency).lower()))
    return pairs


def find_treasury_rate(currency_raw: str, year: int, json_text: str, query_url: str) -> TreasuryRate:
    code = normalize_currency(currency_raw)
    if code not in TREASURY_ROWS_BY_CODE:
        raise RateError(
            f"No Treasury/Fiscal Data row mapping is known for {code}. Use a verified manual year-end source.",
            4,
        )

    mapping = TREASURY_ROWS_BY_CODE[code]
    records = treasury_records_from_payload(json_text)

    year_end_date = f"{year}-12-31"
    matches: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if clean_text(record.get("record_date")) != year_end_date:
            continue
        if clean_text(record.get("currency")).lower() != clean_text(mapping["currency"]).lower():
            continue
        if mapping.get("country") and clean_text(record.get("country")).lower() != clean_text(mapping["country"]).lower():
            continue
        matches.append(record)

    if not matches:
        label = mapping["currency"]
        country = mapping.get("country")
        mapped_name = f"{country} {label}" if country else str(label)
        raise RateError(
            f"Treasury/Fiscal Data did not list a {year_end_date} year-end rate for {code} ({mapped_name}). Use a verified manual year-end source.",
            4,
        )

    rates = {fmt_decimal(parse_decimal(record.get("exchange_rate"))) for record in matches}
    if len(rates) != 1:
        raise RateError(
            f"Treasury/Fiscal Data returned multiple different {year_end_date} rates for {code}; resolve the correct year-end row manually.",
            4,
        )

    record = matches[0]
    return TreasuryRate(
        code=code,
        year=year,
        year_end_date=year_end_date,
        country=clean_text(record.get("country")),
        currency=clean_text(record.get("currency")),
        country_currency_desc=clean_text(record.get("country_currency_desc")),
        rate=parse_decimal(record.get("exchange_rate")),
        query_url=query_url,
        source_json=json_text,
        record=record,
        duplicate_rows=max(0, len(matches) - 1),
    )


def escape_pdf_text(value: object) -> str:
    normalized = clean_text(value)
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_text(value: object, width: int = 88) -> list[str]:
    words = clean_text(value).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        chunks = [word[index : index + width] for index in range(0, len(word), width)] or [word]
        for chunk in chunks:
            if not current:
                current = chunk
            elif len(current) + 1 + len(chunk) <= width:
                current += " " + chunk
            else:
                lines.append(current)
                current = chunk
    if current:
        lines.append(current)
    return lines


PDF_PAGE_WIDTH = 612
PDF_PAGE_HEIGHT = 792
PDF_MARGIN_X = 42
PDF_BOTTOM_MARGIN = 62
PDF_CONTENT_WIDTH = PDF_PAGE_WIDTH - (PDF_MARGIN_X * 2)

PDF_INK = (0.10, 0.13, 0.18)
PDF_MUTED = (0.39, 0.45, 0.55)
PDF_BORDER = (0.82, 0.86, 0.91)
PDF_SURFACE = (1.00, 1.00, 1.00)
PDF_BACKGROUND = (0.97, 0.98, 0.99)
PDF_NAVY = (0.09, 0.13, 0.20)
PDF_TEAL = (0.03, 0.45, 0.53)


def pdf_num(value: float | int) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def pdf_color(color: tuple[float, float, float]) -> str:
    return " ".join(pdf_num(component) for component in color)


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
        commands.append(f"{pdf_color(fill)} rg")
    if stroke:
        commands.append(f"{pdf_color(stroke)} RG")
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
    color: tuple[float, float, float] = PDF_BORDER,
    line_width: float = 1,
) -> str:
    return "\n".join(
        [
            "q",
            f"{pdf_color(color)} RG",
            f"{pdf_num(line_width)} w",
            f"{pdf_num(x1)} {pdf_num(y1)} m",
            f"{pdf_num(x2)} {pdf_num(y2)} l",
            "S",
            "Q",
        ]
    )


def pdf_text(
    text: object,
    x: int | float,
    y: int | float,
    *,
    size: int = 10,
    font: str = "F1",
    color: tuple[float, float, float] = PDF_INK,
) -> str:
    return "\n".join(
        [
            "BT",
            f"{pdf_color(color)} rg",
            f"/{font} {size} Tf",
            f"1 0 0 1 {pdf_num(x)} {pdf_num(y)} Tm",
            f"({escape_pdf_text(text)}) Tj",
            "ET",
        ]
    )


def pdf_chars_for_width(width: float, size: int) -> int:
    return max(18, int(width / (size * 0.48)))


def pdf_draw_wrapped_text(
    commands: list[str],
    text: object,
    x: float,
    y: float,
    width: float,
    *,
    size: int = 9,
    font: str = "F1",
    color: tuple[float, float, float] = PDF_INK,
    line_height: float | None = None,
) -> float:
    line_height = line_height if line_height is not None else size + 4
    for line in wrap_text(text, pdf_chars_for_width(width, size)):
        commands.append(pdf_text(line, x, y, size=size, font=font, color=color))
        y -= line_height
    return y


def pdf_wrapped_height(text: object, width: float, *, size: int = 9, line_height: float | None = None) -> float:
    line_height = line_height if line_height is not None else size + 4
    return max(1, len(wrap_text(text, pdf_chars_for_width(width, size)))) * line_height


def pdf_document_bytes_from_pages(pages: list[list[str]], *, title: str) -> bytes:
    if not pages:
        pages = [[pdf_text(title, PDF_MARGIN_X, PDF_PAGE_HEIGHT - 72, size=15, font="F2")]]

    objects: list[bytes] = []
    page_object_ids: list[int] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

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
            f"<< /Title ({escape_pdf_text(title)}) /Creator (get-year-end-fx-rate) "
            f"/Producer (get-year-end-fx-rate standard-library PDF renderer) >>"
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


def pdf_document_bytes(lines: list[str], *, title: str) -> bytes:
    pages: list[list[str]] = []
    y = 664
    commands = pdf_page_frame(title=title, badge="", page_number=1)

    for raw_line in lines:
        if y < PDF_BOTTOM_MARGIN + 28:
            pages.append(commands)
            commands = pdf_page_frame(title=title, badge="", page_number=len(pages) + 1)
            y = 664
        if raw_line == "":
            y -= 12
            continue
        font = "F2" if raw_line.endswith(":") else "F1"
        color = PDF_NAVY if raw_line.endswith(":") else PDF_INK
        y = pdf_draw_wrapped_text(commands, raw_line, PDF_MARGIN_X, y, PDF_CONTENT_WIDTH, size=9, font=font, color=color)
    pages.append(commands)
    return pdf_document_bytes_from_pages(pages, title=title)


def pdf_page_frame(*, title: str, badge: str, page_number: int) -> list[str]:
    commands = [
        pdf_rect(0, 0, PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT, fill=PDF_BACKGROUND),
        pdf_rect(0, 688, PDF_PAGE_WIDTH, 104, fill=PDF_NAVY),
        pdf_rect(0, 688, PDF_PAGE_WIDTH, 4, fill=PDF_TEAL),
        pdf_text("FBAR-STYLE SUPPORT WORKPAPER", PDF_MARGIN_X, 752, size=7, font="F2", color=(0.71, 0.94, 0.96)),
        pdf_text(title, PDF_MARGIN_X, 726, size=20, font="F2", color=(1, 1, 1)),
        pdf_text("Year-end exchange-rate proof packet", PDF_MARGIN_X, 706, size=9, color=(0.82, 0.87, 0.92)),
        pdf_line(PDF_MARGIN_X, 44, PDF_PAGE_WIDTH - PDF_MARGIN_X, 44, color=(0.86, 0.89, 0.93), line_width=0.75),
        pdf_text("Generated by get-year-end-fx-rate", PDF_MARGIN_X, 28, size=7, color=PDF_MUTED),
        pdf_text(f"Page {page_number}", PDF_PAGE_WIDTH - 78, 28, size=7, color=PDF_MUTED),
    ]
    if badge:
        badge_width = max(72, min(138, 8 * len(badge) + 28))
        badge_x = PDF_PAGE_WIDTH - PDF_MARGIN_X - badge_width
        commands.extend(
            [
                pdf_rect(badge_x, 728, badge_width, 30, fill=(0.15, 0.22, 0.32), stroke=(0.27, 0.39, 0.50)),
                pdf_text(badge, badge_x + 14, 739, size=10, font="F2", color=(1, 1, 1)),
            ]
        )
    return commands


def pdf_section_heading(commands: list[str], title: str, y: float) -> float:
    commands.append(pdf_text(title, PDF_MARGIN_X, y, size=11, font="F2", color=PDF_NAVY))
    commands.append(pdf_line(PDF_MARGIN_X, y - 6, PDF_PAGE_WIDTH - PDF_MARGIN_X, y - 6, color=PDF_BORDER))
    return y - 22


def pdf_add_key_value(
    commands: list[str],
    label: str,
    value: object,
    y: float,
    *,
    x: float = PDF_MARGIN_X,
    width: float = PDF_CONTENT_WIDTH,
) -> float:
    commands.append(pdf_text(label.upper(), x, y, size=7, font="F2", color=PDF_MUTED))
    return pdf_draw_wrapped_text(commands, value, x, y - 14, width, size=9, color=PDF_INK) - 4


def pdf_add_fact_cell(
    commands: list[str],
    label: str,
    value: object,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    commands.append(pdf_rect(x, y - height, width, height, fill=PDF_SURFACE, stroke=PDF_BORDER))
    commands.append(pdf_text(label.upper(), x + 12, y - 17, size=6, font="F2", color=PDF_MUTED))
    pdf_draw_wrapped_text(commands, value, x + 12, y - 33, width - 24, size=9, font="F2", color=PDF_INK, line_height=11)


def pdf_workpaper_document_bytes(workpaper: dict[str, object]) -> bytes:
    source = workpaper["source"]
    proof = workpaper["proof"]
    assert isinstance(source, dict)
    assert isinstance(proof, dict)
    saved_files = proof.get("saved_files", [])
    limitations = proof.get("limitations", [])

    title = "Year-End FX Rate Workpaper"
    badge = f"{workpaper['currency']} {workpaper['year']}"
    pages: list[list[str]] = []
    commands = pdf_page_frame(title=title, badge=badge, page_number=1)
    y = 650

    def ensure_space(needed: float) -> None:
        nonlocal commands, y
        if y - needed >= PDF_BOTTOM_MARGIN:
            return
        pages.append(commands)
        commands = pdf_page_frame(title=title, badge=badge, page_number=len(pages) + 1)
        y = 650

    rate_line = f"1 USD = {workpaper['foreign_per_usd']} {workpaper['currency']} year-end"
    reciprocal_line = f"1 {workpaper['currency']} = {workpaper['usd_per_foreign']} USD"
    rate_height = pdf_wrapped_height(rate_line, PDF_CONTENT_WIDTH - 40, size=17, line_height=20)
    card_height = 82 + max(0, rate_height - 20)
    card_y = y - card_height
    commands.extend(
        [
            pdf_rect(PDF_MARGIN_X, card_y, PDF_CONTENT_WIDTH, card_height, fill=PDF_SURFACE, stroke=PDF_BORDER),
            pdf_rect(PDF_MARGIN_X, card_y, 5, card_height, fill=PDF_TEAL),
            pdf_text("YEAR-END RATE", PDF_MARGIN_X + 20, card_y + card_height - 24, size=7, font="F2", color=PDF_MUTED),
        ]
    )
    next_y = pdf_draw_wrapped_text(
        commands,
        rate_line,
        PDF_MARGIN_X + 20,
        card_y + card_height - 48,
        PDF_CONTENT_WIDTH - 40,
        size=17,
        font="F2",
        color=PDF_NAVY,
        line_height=20,
    )
    commands.append(pdf_text(f"Year-end date: {workpaper['year_end_date']}", PDF_MARGIN_X + 20, next_y - 3, size=8, color=PDF_MUTED))
    commands.append(pdf_text(f"Reciprocal: {reciprocal_line}", PDF_MARGIN_X + 260, next_y - 3, size=8, color=PDF_MUTED))
    y = card_y - 28

    ensure_space(132)
    y = pdf_section_heading(commands, "Workpaper Summary", y)
    cell_gap = 10
    cell_width = (PDF_CONTENT_WIDTH - cell_gap) / 2
    cell_height = 44
    facts = [
        ("Currency", workpaper["currency"]),
        ("Year", workpaper["year"]),
        ("Rate direction", workpaper["rate_direction"]),
        ("Retrieved", source.get("retrieved")),
    ]
    for index, (label, value) in enumerate(facts):
        row = index // 2
        col = index % 2
        x = PDF_MARGIN_X + col * (cell_width + cell_gap)
        top_y = y - row * (cell_height + 8)
        pdf_add_fact_cell(commands, label, value, x, top_y, cell_width, cell_height)
    y -= (cell_height * 2) + 22

    ensure_space(148)
    y = pdf_section_heading(commands, "Source", y)
    y = pdf_add_key_value(commands, "Title", source.get("title"), y)
    y = pdf_add_key_value(commands, "URL", source.get("url"), y)
    y = pdf_add_key_value(commands, "Category", source.get("category"), y)
    y = pdf_add_key_value(commands, "Note", source.get("note"), y)

    ensure_space(92)
    y = pdf_section_heading(commands, "Source Proof", y)
    if saved_files:
        for index, item in enumerate(saved_files, start=1):
            assert isinstance(item, dict)
            entry_height = 64 + pdf_wrapped_height(item.get("sha256"), PDF_CONTENT_WIDTH - 44, size=7, line_height=9)
            ensure_space(entry_height + 10)
            commands.append(pdf_rect(PDF_MARGIN_X, y - entry_height, PDF_CONTENT_WIDTH, entry_height, fill=PDF_SURFACE, stroke=PDF_BORDER))
            commands.append(pdf_text(f"Saved source {index}", PDF_MARGIN_X + 16, y - 20, size=8, font="F2", color=PDF_TEAL))
            commands.append(
                pdf_text(
                    f"{item.get('filename')} (retained in this proof packet)",
                    PDF_MARGIN_X + 16,
                    y - 38,
                    size=9,
                    font="F2",
                    color=PDF_INK,
                )
            )
            commands.append(pdf_text(f"Packet path: {item.get('packet_relative_path')}", PDF_MARGIN_X + 16, y - 54, size=8, color=PDF_MUTED))
            pdf_draw_wrapped_text(
                commands,
                f"SHA-256: {item.get('sha256')}",
                PDF_MARGIN_X + 16,
                y - 70,
                PDF_CONTENT_WIDTH - 32,
                size=7,
                font="F3",
                color=PDF_MUTED,
                line_height=9,
            )
            y -= entry_height + 12
    else:
        y = pdf_draw_wrapped_text(
            commands,
            "No saved source proof file was supplied; see proof limitations.",
            PDF_MARGIN_X,
            y,
            PDF_CONTENT_WIDTH,
            size=9,
            color=PDF_INK,
        )

    if limitations:
        ensure_space(52 + (len(limitations) * 18))
        y = pdf_section_heading(commands, "Proof Limitations", y)
        for item in limitations:
            y = pdf_draw_wrapped_text(commands, f"- {item}", PDF_MARGIN_X, y, PDF_CONTENT_WIDTH, size=9, color=PDF_INK) - 3

    ensure_space(76)
    y = pdf_section_heading(commands, "Caveats", y)
    for item in workpaper.get("caveats", []):
        y = pdf_draw_wrapped_text(commands, f"- {item}", PDF_MARGIN_X, y, PDF_CONTENT_WIDTH, size=8, color=PDF_MUTED) - 2

    pages.append(commands)
    return pdf_document_bytes_from_pages(pages, title=title)


def workpaper_folder(output_root: str, currency_code: str, year: int, source_title: str) -> Path:
    return Path(output_root) / f"{currency_code.lower()}-{year}-{slugify(source_title)}"


def copy_saved_proofs(folder: Path, saved_proofs: Iterable[Path]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for index, original_path in enumerate(saved_proofs, start=1):
        if not original_path.exists():
            raise RateError(f"Proof file does not exist: {original_path}", 2)
        proof_path = original_path
        if proof_path.parent.resolve() != folder.resolve():
            copied_name = f"source-proof-{index}{proof_path.suffix}"
            proof_path = folder / copied_name
            shutil.copy2(original_path, proof_path)
        entries.append(
            {
                "filename": proof_path.name,
                "packet_relative_path": proof_path.name,
                "path": as_abs(proof_path),
                "sha256": sha256_file(proof_path),
            }
        )
    return entries


def create_workpaper(
    *,
    output_root: str,
    currency_code: str,
    year: int,
    year_end_date: str,
    rate: Decimal,
    rate_direction: str,
    source_title: str,
    source_url: str,
    retrieval_date: str,
    source_note: str,
    source_category: str,
    saved_proofs: Iterable[Path],
    proof_limitations: list[str],
    treasury_record: dict[str, object] | None = None,
) -> dict[str, object]:
    reject_average_language(source_title, source_note, source_category)
    foreign_per_usd, usd_per_foreign = build_rate_values(rate, rate_direction)
    folder = workpaper_folder(output_root, currency_code, year, source_title)
    folder.mkdir(parents=True, exist_ok=True)

    proof_entries = copy_saved_proofs(folder, saved_proofs)
    if not proof_entries:
        proof_limitations = proof_limitations + ["No saved source proof file was supplied for this manual workpaper."]

    workpaper = {
        "skill": "get-year-end-fx-rate",
        "purpose": "FBAR-style year-end USD exchange-rate support",
        "currency": currency_code,
        "year": year,
        "year_end_date": year_end_date,
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
            "year_end_confirmed": True,
        },
        "treasury_record": treasury_record,
        "proof": {
            "workpaper_md": as_abs(folder / "workpaper.md"),
            "workpaper_json": as_abs(folder / "workpaper.json"),
            "workpaper_pdf": as_abs(folder / "workpaper.pdf"),
            "saved_files": proof_entries,
            "limitations": proof_limitations,
        },
        "caveats": [
            "This is a retained support workpaper, not legal or tax advice.",
            "Use this for year-end support only; do not reuse it as an income-tax average rate.",
        ],
    }

    (folder / "workpaper.md").write_text(render_workpaper_md(workpaper), encoding="utf-8")
    pdf_path = folder / "workpaper.pdf"
    pdf_path.write_bytes(render_workpaper_pdf_bytes(workpaper))
    workpaper["proof"]["workpaper_pdf_sha256"] = sha256_file(pdf_path)  # type: ignore[index]
    (folder / "workpaper.json").write_text(json.dumps(workpaper, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return workpaper


def render_workpaper_md(workpaper: dict[str, object]) -> str:
    source = workpaper["source"]
    proof = workpaper["proof"]
    assert isinstance(source, dict)
    assert isinstance(proof, dict)
    saved_files = proof.get("saved_files", [])
    limitations = proof.get("limitations", [])

    lines = [
        "# Year-End FX Rate Workpaper",
        "",
        f"Currency: {workpaper['currency']}",
        f"Year: {workpaper['year']}",
        f"Year-end date: {workpaper['year_end_date']}",
        f"Rate: 1 USD = {workpaper['foreign_per_usd']} {workpaper['currency']} year-end",
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
        lines.append("- No saved source proof file was supplied; see proof limitations.")

    if limitations:
        lines.extend(["", "## Proof Limitations"])
        for item in limitations:
            lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Caveats",
            "- This is a retained support workpaper, not legal or tax advice.",
            "- Use this for year-end support only; do not reuse it as an income-tax average rate.",
            "",
        ]
    )
    return "\n".join(lines)


def render_workpaper_pdf_bytes(workpaper: dict[str, object]) -> bytes:
    return pdf_workpaper_document_bytes(workpaper)


def markdown_file_link(label: str, path: object) -> str:
    target = clean_text(path)
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
            f"Rate: 1 USD = {workpaper['foreign_per_usd']} {workpaper['currency']} year-end ({workpaper['year_end_date']})",
            f"Reciprocal: 1 {workpaper['currency']} = {workpaper['usd_per_foreign']} USD",
            f"Source: {source.get('title')}, {source.get('url')}, retrieved {source.get('retrieved')}",
            f"Proof: {proof_link}",
            f"Artifacts: {artifact_links(workpaper)}",
        ]
    )


def command_lookup(args: argparse.Namespace) -> int:
    code = normalize_currency(args.currency)
    query_url = treasury_query_url(code, args.year, args.api_url)
    json_text, _source_ref = load_json_text(query_url, args.api_file)
    rate = find_treasury_rate(code, args.year, json_text, query_url)

    source_title = "Treasury Reporting Rates of Exchange - Fiscal Data"
    folder = workpaper_folder(args.output_root, code, args.year, source_title)
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "treasury-fiscal-data-response.json"
    json_path.write_text(rate.source_json, encoding="utf-8")

    note = (
        f"Treasury/Fiscal Data record date {rate.year_end_date}; row {rate.country_currency_desc or rate.country + '-' + rate.currency}; "
        f"exchange_rate {fmt_decimal(rate.rate)}. API query URL is retained in workpaper.json."
    )
    if rate.duplicate_rows:
        note += f" Additional rows with the same rate were returned: {rate.duplicate_rows}."

    workpaper = create_workpaper(
        output_root=args.output_root,
        currency_code=code,
        year=args.year,
        year_end_date=rate.year_end_date,
        rate=rate.rate,
        rate_direction="foreign-per-usd",
        source_title=source_title,
        source_url=TREASURY_DATASET_URL,
        retrieval_date=args.retrieved or today_iso(),
        source_note=note,
        source_category="Treasury/Fiscal Data year-end reporting rate",
        saved_proofs=[json_path],
        proof_limitations=[],
        treasury_record={**rate.record, "api_query_url": rate.query_url, "dataset_url": TREASURY_DATASET_URL},
    )
    print(final_text(workpaper))
    return 0


def command_manual(args: argparse.Namespace) -> int:
    code = normalize_currency(args.currency)
    if not args.year_end_confirmed:
        raise RateError("Pass --year-end-confirmed after verifying the source supports a year-end or YYYY-12-31 rate.", 2)
    reject_average_language(args.source_title, args.source_note, args.source_category)
    proof_files = [Path(args.proof_file)] if args.proof_file else []
    note = args.source_note or "Manual fallback source confirmed by user/preparer as year-end support."
    workpaper = create_workpaper(
        output_root=args.output_root,
        currency_code=code,
        year=args.year,
        year_end_date=f"{args.year}-12-31",
        rate=parse_decimal(args.rate),
        rate_direction=args.rate_direction,
        source_title=args.source_title,
        source_url=args.source_url,
        retrieval_date=args.retrieved or today_iso(),
        source_note=note,
        source_category=args.source_category,
        saved_proofs=proof_files,
        proof_limitations=[],
    )
    print(final_text(workpaper))
    return 0


def command_map_check(args: argparse.Namespace) -> int:
    code = normalize_currency(args.currency) if args.currency else None
    query_url = treasury_rows_url(args.year, args.api_url)
    json_text, source_ref = load_json_text(query_url, args.api_file)
    records = treasury_records_from_payload(json_text)
    year_end_date = f"{args.year}-12-31"
    rows = [record for record in records if clean_text(record.get("record_date")) == year_end_date]

    if code:
        if code not in TREASURY_ROWS_BY_CODE:
            raise RateError(f"No Treasury/Fiscal Data row mapping is known for {code}.", 4)
        mapping = TREASURY_ROWS_BY_CODE[code]
        expected_pair = (clean_text(mapping.get("country")).lower(), clean_text(mapping.get("currency")).lower())
        matches = [record for record in rows if treasury_row_pair(record) == expected_pair]
        print(f"Parsed Treasury/Fiscal Data rows for {year_end_date}: {len(rows)} from {source_ref}")
        if matches:
            desc = clean_text(matches[0].get("country_currency_desc")) or f"{mapping.get('country')}-{mapping.get('currency')}"
            print(f"{code} mapped row found: {desc}")
            return 0
        print(f"{code} mapped row not found in Treasury/Fiscal Data for {year_end_date}.")
        return 4 if args.strict else 0

    mapped_pairs = mapped_treasury_pairs()
    unmapped = []
    for record in rows:
        pair = treasury_row_pair(record)
        if pair not in mapped_pairs:
            desc = clean_text(record.get("country_currency_desc")) or f"{record.get('country')}-{record.get('currency')}"
            unmapped.append(clean_text(desc))

    print(f"Parsed Treasury/Fiscal Data rows for {year_end_date}: {len(rows)} from {source_ref}")
    print(f"Mapped rows: {len(rows) - len(unmapped)}")
    if unmapped:
        print("Unmapped rows:")
        for item in sorted(set(unmapped)):
            print(f"- {item}")
        if args.strict:
            return 4
    else:
        print("All parsed rows are represented in TREASURY_ROWS_BY_CODE.")
    return 0


def assert_pdf(path: Path, required_terms: Iterable[str], forbidden_terms: Iterable[str] = ()) -> None:
    data = path.read_bytes()
    assert data.startswith(b"%PDF-1."), path
    assert data.rstrip().endswith(b"%%EOF"), path
    assert b"xref" in data, path
    assert b"/Helvetica-Bold" in data, path
    assert len(data) > 1000, path
    for term in required_terms:
        assert clean_text(term).encode("latin-1", errors="replace") in data, term
    for term in forbidden_terms:
        assert clean_text(term).encode("latin-1", errors="replace") not in data, term


def command_self_test(_args: argparse.Namespace) -> int:
    sample_payload = {
        "data": [
            {
                "record_date": "2025-12-31",
                "country": "United Arab Emirates",
                "currency": "Dirham",
                "country_currency_desc": "United Arab Emirates-Dirham",
                "exchange_rate": "3.672",
                "effective_date": "2025-12-31",
                "src_line_nbr": "148",
                "record_calendar_year": "2025",
            },
            {
                "record_date": "2025-12-31",
                "country": "Colombia",
                "currency": "Peso",
                "country_currency_desc": "Colombia-Peso",
                "exchange_rate": "3900.25",
                "effective_date": "2025-12-31",
                "src_line_nbr": "39",
                "record_calendar_year": "2025",
            }
        ],
        "meta": {"count": 1},
    }
    json_text = json.dumps(sample_payload)
    query_url = treasury_query_url("COP", 2025, TREASURY_API_URL)
    rate = find_treasury_rate("colombian peso", 2025, json_text, query_url)
    assert rate.rate == Decimal("3900.25")
    assert rate.year_end_date == "2025-12-31"
    aed_rate = find_treasury_rate("AED", 2025, json_text, treasury_query_url("AED", 2025, TREASURY_API_URL))
    assert aed_rate.rate == Decimal("3.672")
    assert normalize_currency("uae dirham") == "AED"

    foreign_per_usd, usd_per_foreign = build_rate_values(Decimal("0.25"), "usd-per-foreign")
    assert foreign_per_usd == Decimal("4")
    assert usd_per_foreign == Decimal("0.25")

    try:
        normalize_currency("peso")
    except RateError as exc:
        assert exc.code == 2
    else:
        raise AssertionError("ambiguous peso should fail")

    try:
        reject_average_language("published yearly average")
    except RateError as exc:
        assert exc.code == 2
    else:
        raise AssertionError("yearly average wording should fail")

    with tempfile.TemporaryDirectory() as tmp:
        api_file = Path(tmp) / "treasury.json"
        api_file.write_text(json_text, encoding="utf-8")
        lookup_args = argparse.Namespace(
            currency="COP",
            year=2025,
            output_root=str(Path(tmp) / "fbar-proof"),
            api_url=TREASURY_API_URL,
            api_file=str(api_file),
            retrieved="2026-07-03",
        )
        lookup_output = io.StringIO()
        with contextlib.redirect_stdout(lookup_output):
            command_lookup(lookup_args)
        lookup_text = lookup_output.getvalue()
        assert "Rate: 1 USD = 3900.25 COP year-end (2025-12-31)" in lookup_text
        assert "Proof: [workpaper.pdf](<" in lookup_text
        assert "[treasury-fiscal-data-response.json](<" in lookup_text
        workpaper_json = next((Path(tmp) / "fbar-proof").glob("cop-2025-*/workpaper.json"))
        data = json.loads(workpaper_json.read_text(encoding="utf-8"))
        assert data["foreign_per_usd"] == "3900.25"
        assert data["usd_per_foreign"] == "0.000256393821"
        assert data["year_end_date"] == "2025-12-31"
        assert data["source"]["year_end_confirmed"] is True
        assert data["proof"]["saved_files"][0]["filename"] == "treasury-fiscal-data-response.json"
        assert "average" not in json.dumps(data["source"]).lower()
        assert_pdf(
            Path(data["proof"]["workpaper_pdf"]),
            [
                "Year-End FX Rate Workpaper",
                "1 USD = 3900.25 COP year-end",
                "Treasury Reporting Rates of Exchange",
                "Source Proof",
                "treasury-fiscal-data-response.json",
            ],
            ["yearly average", "annual average"],
        )

        aed_lookup_args = argparse.Namespace(
            currency="AED",
            year=2025,
            output_root=str(Path(tmp) / "aed-proof"),
            api_url=TREASURY_API_URL,
            api_file=str(api_file),
            retrieved="2026-07-03",
        )
        aed_lookup_output = io.StringIO()
        with contextlib.redirect_stdout(aed_lookup_output):
            command_lookup(aed_lookup_args)
        assert "Rate: 1 USD = 3.672 AED year-end (2025-12-31)" in aed_lookup_output.getvalue()

        map_check_args = argparse.Namespace(
            year=2025,
            currency="AED",
            api_url=TREASURY_API_URL,
            api_file=str(api_file),
            strict=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_map_check(map_check_args) == 0

        unmapped_payload = {
            "data": [
                {
                    "record_date": "2025-12-31",
                    "country": "Exampleland",
                    "currency": "Token",
                    "country_currency_desc": "Exampleland-Token",
                    "exchange_rate": "12.34",
                }
            ]
        }
        unmapped_file = Path(tmp) / "unmapped.json"
        unmapped_file.write_text(json.dumps(unmapped_payload), encoding="utf-8")
        unmapped_args = argparse.Namespace(
            year=2025,
            currency=None,
            api_url=TREASURY_API_URL,
            api_file=str(unmapped_file),
            strict=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_map_check(unmapped_args) == 4

        proof_file = Path(tmp) / "manual-source.html"
        proof_file.write_text("<p>2025-12-31 rate</p>", encoding="utf-8")
        manual_args = argparse.Namespace(
            currency="COP",
            year=2025,
            rate="0.00025",
            rate_direction="usd-per-foreign",
            source_title="Example Central Bank Year-End Rate",
            source_url="https://example.test/fx/2025",
            source_category="central bank year-end rate",
            source_note="Source labels this as the 2025-12-31 rate.",
            year_end_confirmed=True,
            retrieved="2026-07-03",
            proof_file=str(proof_file),
            output_root=str(Path(tmp) / "manual-proof"),
        )
        manual_output = io.StringIO()
        with contextlib.redirect_stdout(manual_output):
            command_manual(manual_args)
        manual_text = manual_output.getvalue()
        assert "Rate: 1 USD = 4000 COP year-end (2025-12-31)" in manual_text
        assert "[source-proof-1.html](<" in manual_text
        manual_json = next((Path(tmp) / "manual-proof").glob("cop-2025-*/workpaper.json"))
        manual_data = json.loads(manual_json.read_text(encoding="utf-8"))
        assert manual_data["foreign_per_usd"] == "4000"
        assert manual_data["usd_per_foreign"] == "0.00025"
        assert manual_data["proof"]["saved_files"][0]["filename"] == "source-proof-1.html"
        assert_pdf(
            Path(manual_data["proof"]["workpaper_pdf"]),
            [
                "Year-End FX Rate Workpaper",
                "1 USD = 4000 COP year-end",
                "Example Central Bank Year-End Rate",
                "source-proof-1.html",
            ],
            ["yearly average", "annual average"],
        )

        bad_manual_args = argparse.Namespace(**{**manual_args.__dict__, "source_note": "Published yearly average."})
        try:
            command_manual(bad_manual_args)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("manual yearly-average wording should fail")

    print("self-test passed")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--currency", required=True, help="Currency code or unambiguous currency name.")
    parser.add_argument("--year", required=True, type=int, help="Calendar year.")
    parser.add_argument("--output-root", default="work/fbar-fx-rate-proof", help="Proof output root.")
    parser.add_argument("--retrieved", help="Retrieval date YYYY-MM-DD; defaults to today.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lookup = subparsers.add_parser("lookup", help="Fetch Treasury/Fiscal Data year-end rate and create proof workpaper.")
    add_common_args(lookup)
    lookup.add_argument("--api-url", default=TREASURY_API_URL, help="Treasury/Fiscal Data API endpoint.")
    lookup.add_argument("--api-file", help="Use a local JSON response instead of fetching the API.")
    lookup.set_defaults(func=command_lookup)

    manual = subparsers.add_parser("manual", help="Create a proof workpaper for a verified manual year-end source.")
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
    manual.add_argument("--source-category", default="published year-end rate", help="Source class.")
    manual.add_argument("--source-note", default="", help="Note confirming the source supports a year-end rate.")
    manual.add_argument("--year-end-confirmed", action="store_true", help="Required confirmation that the source supports a year-end or YYYY-MM-DD rate.")
    manual.add_argument("--proof-file", help="Optional local screenshot/PDF/HTML/source proof file to copy, hash, and reference.")
    manual.set_defaults(func=command_manual)

    map_check = subparsers.add_parser("map-check", help="Compare a Treasury/Fiscal Data year-end response with the hard-coded currency map.")
    map_check.add_argument("--year", required=True, type=int, help="Calendar year to inspect.")
    map_check.add_argument("--currency", help="Optional ISO code or unambiguous currency name to check against the map.")
    map_check.add_argument("--api-url", default=TREASURY_API_URL, help="Treasury/Fiscal Data API endpoint.")
    map_check.add_argument("--api-file", help="Use a local JSON response instead of fetching the API.")
    map_check.add_argument("--strict", action="store_true", help="Exit nonzero when unmapped Treasury rows are found.")
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
