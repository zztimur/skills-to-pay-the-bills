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
    "VES": ("Venezuela", "Bolivar"),
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

    if len(upper) == 3 and upper.isalpha():
        return upper

    if text in AMBIGUOUS_TERMS:
        raise RateError(
            f"Ambiguous currency '{raw}'. {AMBIGUOUS_TERMS[text]}",
            2,
        )

    if text in ALIASES:
        return ALIASES[text]

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
        candidate_country = str(candidate["country"]).lower()
        candidate_currency = str(candidate["currency"]).lower()
        if country.lower() in candidate_country and currency.lower() in candidate_currency:
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
    folder.mkdir(parents=True, exist_ok=True)

    proof_entries = []
    for proof_path in saved_proofs:
        if proof_path.exists():
            proof_entries.append(
                {
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
            "saved_files": proof_entries,
            "limitations": proof_limitations,
        },
        "caveats": [
            "This is a retained support workpaper, not tax advice and not an official IRS determination.",
            "Do not describe this rate as IRS-approved.",
        ],
    }

    write_text(folder / "workpaper.json", json.dumps(workpaper, indent=2, sort_keys=True) + "\n")
    write_text(folder / "workpaper.md", render_workpaper_md(workpaper))

    workpaper["proof"]["workpaper_md"] = as_abs(folder / "workpaper.md")  # type: ignore[index]
    workpaper["proof"]["workpaper_json"] = as_abs(folder / "workpaper.json")  # type: ignore[index]
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


def final_text(workpaper: dict[str, object]) -> str:
    source = workpaper["source"]
    proof = workpaper["proof"]
    assert isinstance(source, dict)
    assert isinstance(proof, dict)
    return "\n".join(
        [
            f"Rate: 1 USD = {workpaper['foreign_per_usd']} {workpaper['currency']} yearly average",
            f"Reciprocal: 1 {workpaper['currency']} = {workpaper['usd_per_foreign']} USD",
            f"Source: {source.get('title')}, {source.get('url')}, retrieved {source.get('retrieved')}",
            f"Proof: {proof.get('workpaper_md')}",
        ]
    )


def command_lookup(args: argparse.Namespace) -> int:
    code = normalize_currency(args.currency)
    if code not in IRS_ROWS_BY_CODE:
        raise RateError(
            f"The IRS yearly-average table does not list {code}. Search for a published annual average from a government, tax authority, central bank, bank, or reputable FX provider; do not calculate an annual average from daily/monthly/quarterly data. Then run the manual command with the published rate/source.",
            4,
        )

    html, source_ref = load_html(args.source_url, args.html_file)
    rate = find_irs_rate(code, args.year, html, args.source_url)

    source_slug = "irs-yearly-average-currency-exchange-rates"
    folder = Path(args.output_root) / f"{code.lower()}-{args.year}-{source_slug}"
    folder.mkdir(parents=True, exist_ok=True)
    html_path = folder / "irs-yearly-average-source.html"
    html_bytes = html.encode("utf-8")
    html_path.write_bytes(html_bytes)

    note = (
        f"IRS yearly average table row: {rate.country} {rate.currency}; "
        f"source fetched from {source_ref}; HTML sha256 {sha256_bytes(html_bytes)}."
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
    rate = parse_decimal(args.rate)
    saved_proofs: list[Path] = []
    limitations: list[str] = []

    if args.proof_file:
        proof_path = Path(args.proof_file)
        if not proof_path.exists():
            raise RateError(f"Proof file does not exist: {proof_path}", 2)
        saved_proofs.append(proof_path)
    else:
        limitations.append(
            "No local screenshot/PDF/HTML proof file was supplied to the script; retain source access and retrieval metadata."
        )

    note = args.source_note or "Agent confirmed the source labels this as a published yearly/annual average."
    workpaper = create_workpaper(
        output_root=args.output_root,
        currency_code=code,
        year=args.year,
        rate=rate,
        rate_direction=args.rate_direction,
        source_title=args.source_title,
        source_url=args.source_url,
        retrieval_date=args.retrieved or today_iso(),
        source_note=note,
        source_category=args.source_category,
        saved_proofs=saved_proofs,
        proof_limitations=limitations,
    )
    print(final_text(workpaper))
    return 0


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
        with contextlib.redirect_stdout(io.StringIO()):
            command_lookup(lookup_args)
        workpaper = next((Path(tmp) / "proof").glob("cad-2024-*/workpaper.json"))
        data = json.loads(workpaper.read_text(encoding="utf-8"))
        assert data["foreign_per_usd"] == "1.37"

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
            retrieved="2026-07-03",
            proof_file=str(proof_file),
            output_root=str(Path(tmp) / "manual-proof"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            command_manual(manual_args)
        manual_workpaper = next((Path(tmp) / "manual-proof").glob("cop-2024-*/workpaper.json"))
        manual_data = json.loads(manual_workpaper.read_text(encoding="utf-8"))
        assert manual_data["foreign_per_usd"] == "4200"

    print("self-test passed")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--currency", required=True, help="Currency code or unambiguous currency name.")
    parser.add_argument("--year", required=True, type=int, help="Calendar/tax year.")
    parser.add_argument("--output-root", default="work/fx-rate-proof", help="Proof output root.")
    parser.add_argument("--retrieved", help="Retrieval date YYYY-MM-DD; defaults to today.")


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
    manual.add_argument("--source-note", help="Note confirming the source labels the value as yearly/annual average.")
    manual.add_argument("--proof-file", help="Optional local screenshot/PDF/HTML/source file to hash and reference.")
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
