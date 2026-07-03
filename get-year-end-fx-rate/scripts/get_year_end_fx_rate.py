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
    "uruguayan peso": "UYU",
    "south african rand": "ZAR",
    "rand": "ZAR",
}

AMBIGUOUS_TERMS = {
    "peso": "Use a country or ISO code, for example COP, MXN, ARS, CLP, or UYU.",
    "dollar": "Use a country or ISO code, for example CAD, AUD, NZD, SGD, HKD, or TWD.",
    "pound": "Use a country or ISO code, for example GBP.",
    "franc": "Use a country or ISO code, for example CHF.",
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


def find_treasury_rate(currency_raw: str, year: int, json_text: str, query_url: str) -> TreasuryRate:
    code = normalize_currency(currency_raw)
    if code not in TREASURY_ROWS_BY_CODE:
        raise RateError(
            f"No Treasury/Fiscal Data row mapping is known for {code}. Use a verified manual year-end source.",
            4,
        )

    mapping = TREASURY_ROWS_BY_CODE[code]
    payload = parse_json_payload(json_text)
    records = payload.get("data")
    if not isinstance(records, list):
        raise RateError("Treasury/Fiscal Data response did not contain a data list.", 3)

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


def pdf_text(text: object, x: int, y: int, *, size: int = 10, font: str = "F1") -> str:
    return "\n".join(
        [
            "BT",
            f"/{font} {size} Tf",
            f"1 0 0 1 {x} {y} Tm",
            f"({escape_pdf_text(text)}) Tj",
            "ET",
        ]
    )


def pdf_document_bytes(lines: list[str], *, title: str) -> bytes:
    page_width = 612
    page_height = 792
    margin_x = 54
    start_y = 744
    line_height = 14
    lines_per_page = 48
    pages: list[list[str]] = []

    for start in range(0, len(lines), lines_per_page):
        page_lines = lines[start : start + lines_per_page]
        commands = [
            pdf_text(title, margin_x, start_y, size=15, font="F2"),
            pdf_text("Retained support workpaper", margin_x, start_y - 20, size=9),
        ]
        y = start_y - 48
        for raw_line in page_lines:
            if raw_line == "":
                y -= line_height
                continue
            font = "F2" if raw_line.endswith(":") else "F1"
            for wrapped in wrap_text(raw_line, 92):
                commands.append(pdf_text(wrapped, margin_x, y, size=9, font=font))
                y -= line_height
        pages.append(commands)

    objects: list[bytes] = []
    page_object_ids: list[int] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

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
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Resources << /ProcSet [/PDF /Text] /Font << /F1 3 0 R /F2 4 0 R >> >> "
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
    source = workpaper["source"]
    proof = workpaper["proof"]
    assert isinstance(source, dict)
    assert isinstance(proof, dict)
    saved_files = proof.get("saved_files", [])
    limitations = proof.get("limitations", [])

    lines = [
        "Rate Details:",
        f"Currency: {workpaper['currency']}",
        f"Year: {workpaper['year']}",
        f"Year-end date: {workpaper['year_end_date']}",
        f"Rate: 1 USD = {workpaper['foreign_per_usd']} {workpaper['currency']} year-end",
        f"Reciprocal: 1 {workpaper['currency']} = {workpaper['usd_per_foreign']} USD",
        "",
        "Source:",
        f"Title: {source.get('title')}",
        f"URL: {source.get('url')}",
        f"Category: {source.get('category')}",
        f"Retrieved: {source.get('retrieved')}",
        f"Note: {source.get('note')}",
        "",
        "Source Proof:",
    ]
    if saved_files:
        for index, item in enumerate(saved_files, start=1):
            assert isinstance(item, dict)
            lines.append(f"Saved source {index}: {item.get('filename')} (retained in this proof packet)")
            lines.append(f"SHA-256 {index}: {item.get('sha256')}")
    else:
        lines.append("No saved source proof file was supplied; see proof limitations.")

    if limitations:
        lines.extend(["", "Proof Limitations:"])
        lines.extend(str(item) for item in limitations)

    lines.extend(
        [
            "",
            "Caveats:",
            "This is a retained support workpaper, not legal or tax advice.",
            "Use this for year-end support only; do not reuse it as an income-tax average rate.",
        ]
    )
    return pdf_document_bytes(lines, title="Year-End FX Rate Workpaper")


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
