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
import stat
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from _workpaper import RateError, WorkpaperSpec, build_workpaper, final_text

getcontext().prec = 28

IRS_YEARLY_URL = (
    "https://www.irs.gov/individuals/international-taxpayers/"
    "yearly-average-currency-exchange-rates"
)
MAX_HTML_SNAPSHOT_BYTES = 5 * 1024 * 1024

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
    "ruble": "Use RUB for Russian ruble, or provide a country/ISO code.",
}

ANNUAL_AVERAGE_LANGUAGE = re.compile(
    r"\b(?:annual|yearly)[\s-]*(?:average|avg)\b|\b(?:average|avg)[\s-]*(?:annual|yearly)\b",
    re.IGNORECASE,
)
NON_ANNUAL_LANGUAGE = re.compile(
    r"\b(?:daily|weekly|monthly|quarterly|intraday|spot|year[ -]?end|fbar)\b",
    re.IGNORECASE,
)
TREASURY_REPORTING_RATE_LANGUAGE = re.compile(
    r"\b(?:u\.?\s*s\.?\s*)?treasury\s+reporting\s+rates?\b",
    re.IGNORECASE,
)

KNOWN_CURRENCY_CODES = set(IRS_ROWS_BY_CODE) | set(ALIASES.values())


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


def validate_manual_source_url(raw: str) -> str:
    """Require a real web locator before recording manual-source provenance."""
    source_url = clean_text(raw)
    parsed = urlparse(source_url)
    if (
        not source_url
        or re.search(r"\s", source_url)
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
    ):
        raise RateError(
            "Manual --source-url must be a nonempty absolute http:// or https:// URL.",
            2,
        )
    return source_url


def validate_manual_annual_metadata(
    source_title_raw: str, source_note_raw: str, source_category_raw: str
) -> tuple[str, str, str]:
    """Require explicit annual-average provenance and reject incompatible labels."""
    source_title = clean_text(source_title_raw)
    source_note = clean_text(source_note_raw)
    source_category = clean_text(source_category_raw)
    if not source_title:
        raise RateError("Pass a nonempty --source-title for the published source.", 2)
    if not source_note:
        raise RateError(
            "Pass a nonempty --source-note confirming why the source supports yearly-average use.",
            2,
        )
    if len(source_note) < 16:
        raise RateError(
            "--source-note must be a specific explanation of at least 16 characters.",
            2,
        )

    source_provenance = " ".join((source_title, source_note))
    combined = " ".join((source_provenance, source_category))
    if TREASURY_REPORTING_RATE_LANGUAGE.search(combined):
        raise RateError(
            "Treasury reporting rates are quarterly reporting data, not published yearly averages; "
            "do not use them in this skill.",
            2,
        )
    if NON_ANNUAL_LANGUAGE.search(combined):
        raise RateError(
            "This skill is for published yearly-average rates only; daily, monthly, quarterly, "
            "spot, year-end, and FBAR sources are not valid manual inputs.",
            2,
        )
    if not ANNUAL_AVERAGE_LANGUAGE.search(source_provenance):
        raise RateError(
            "Manual source metadata must explicitly identify a published yearly or annual average.",
            2,
        )
    return source_title, source_note, source_category


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


def load_irs_html(html_file: str | None) -> tuple[str, str]:
    """Load a bounded UTF-8 snapshot of the fixed IRS yearly-average page."""
    if html_file:
        path = Path(html_file)
        source_ref = str(path.resolve())
        try:
            mode = path.lstat().st_mode
            if path.is_symlink() or not stat.S_ISREG(mode):
                raise RateError(
                    f"Supplied IRS HTML file must be an existing regular file, not a link or device: {source_ref}",
                    2,
                )
            size = path.stat().st_size
            if size > MAX_HTML_SNAPSHOT_BYTES:
                raise RateError(
                    f"Supplied IRS HTML file exceeds the {MAX_HTML_SNAPSHOT_BYTES}-byte safety limit: {source_ref}",
                    2,
                )
            html_bytes = path.read_bytes()
        except FileNotFoundError as exc:
            raise RateError(f"Could not read supplied IRS HTML file {source_ref}: {exc}", 2) from exc
        except OSError as exc:
            raise RateError(f"Could not read supplied IRS HTML file {source_ref}: {exc}", 2) from exc
        if len(html_bytes) > MAX_HTML_SNAPSHOT_BYTES:
            raise RateError(
                f"Supplied IRS HTML file exceeds the {MAX_HTML_SNAPSHOT_BYTES}-byte safety limit: {source_ref}",
                2,
            )
        try:
            return html_bytes.decode("utf-8"), source_ref
        except UnicodeDecodeError as exc:
            raise RateError(f"Supplied IRS HTML file is not valid UTF-8: {source_ref}", 2) from exc

    request = Request(
        IRS_YEARLY_URL,
        headers={"User-Agent": "get-yearly-fx-rate/1.0 (+tax support workpaper)"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            html_bytes = response.read(MAX_HTML_SNAPSHOT_BYTES + 1)
            if len(html_bytes) > MAX_HTML_SNAPSHOT_BYTES:
                raise RateError(
                    f"IRS yearly-average response exceeds the {MAX_HTML_SNAPSHOT_BYTES}-byte safety limit.",
                    3,
                )
            return html_bytes.decode(charset, errors="replace"), IRS_YEARLY_URL
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def today_iso() -> str:
    return _dt.date.today().isoformat()


def resolve_retrieval_date(retrieved: str | None, today: _dt.date | None = None) -> str:
    """Return the actual retrieval date without permitting future provenance."""
    current_date = today or _dt.date.today()
    value = retrieved or current_date.isoformat()
    retrieval_date = _dt.date.fromisoformat(value)
    if retrieval_date > current_date:
        raise RateError(
            f"Retrieval date {value} cannot be later than today ({current_date.isoformat()}). "
            "Use the date the source was actually retrieved.",
            2,
        )
    return value


def validate_completed_year(year: int, today: _dt.date | None = None) -> int:
    """Reject a calendar year that cannot yet have a complete annual average."""
    current_year = (today or _dt.date.today()).year
    if year >= current_year:
        raise RateError(
            f"Year {year} is not a completed calendar year. A published yearly average can be "
            f"documented only for a completed year before {current_year}.",
            2,
        )
    return year


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
    saved_proofs: list[Path],
    proof_limitations: list[str],
) -> dict[str, object]:
    """Thin adapter over workpaper-kit's build_workpaper.

    Yearly is the kit's reference implementation, so this maps the skill's
    long-standing create_workpaper signature onto a WorkpaperSpec and returns
    the same workpaper dict (byte-identical workpaper.md/.json/.pdf). The
    source/currency layers above are untouched; only the downstream packet
    machine now lives in the vendored kit.
    """
    spec = WorkpaperSpec(
        output_root=output_root,
        skill_name="get-yearly-fx-rate",
        currency_code=currency_code,
        year=year,
        rate=rate,
        rate_direction=rate_direction,
        source_title=source_title,
        source_url=source_url,
        source_category=source_category,
        retrieval_date=retrieval_date,
        source_note=source_note,
        document_title="Yearly FX Rate Workpaper",
        document_subtitle="Published annual average exchange-rate support",
        rate_phrase="yearly average",
        caveats=[
            "This is a retained support workpaper, not tax advice and not an official IRS determination.",
            "Do not describe this rate as IRS-approved.",
        ],
        saved_proofs=saved_proofs,
        proof_required=True,
        proof_limitations=proof_limitations,
    )
    return build_workpaper(spec)


def command_lookup(args: argparse.Namespace) -> int:
    validate_completed_year(args.year)
    code = normalize_currency(args.currency)
    if code not in IRS_ROWS_BY_CODE:
        raise RateError(
            f"The IRS yearly-average table does not list {code}. Search for a published annual average from a government, tax authority, central bank, bank, or reputable FX provider; do not calculate an annual average from daily/monthly/quarterly data. Then run the manual command with the published rate/source.",
            4,
        )

    retrieval_date = resolve_retrieval_date(args.retrieved)
    html, _source_ref = load_irs_html(args.html_file)
    rate = find_irs_rate(code, args.year, html, IRS_YEARLY_URL)

    replay_mode = bool(args.html_file)
    source_title = (
        "IRS Yearly average currency exchange rates (local HTML replay)"
        if replay_mode
        else "IRS Yearly average currency exchange rates"
    )
    source_slug = (
        "irs-yearly-average-currency-exchange-rates-local-html-replay"
        if replay_mode
        else "irs-yearly-average-currency-exchange-rates"
    )
    folder = Path(args.output_root) / f"{code.lower()}-{args.year}-{source_slug}"
    folder.mkdir(parents=True, exist_ok=True)
    html_path = folder / (
        "irs-yearly-average-local-snapshot.html"
        if replay_mode
        else "irs-yearly-average-source.html"
    )
    html_bytes = html.encode("utf-8")
    html_path.write_bytes(html_bytes)

    fetch_mode = (
        "supplied local HTML replay; provenance was not independently verified by this script"
        if replay_mode
        else "live fetch from source URL"
    )
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
        source_title=source_title,
        source_url=rate.source_url,
        retrieval_date=retrieval_date,
        source_note=note,
        source_category=(
            "IRS yearly average table (local HTML replay)"
            if replay_mode
            else "IRS yearly average table"
        ),
        saved_proofs=[html_path],
        proof_limitations=(
            [
                "Local HTML replay retained; this script did not independently verify the snapshot came from IRS.",
                "Screenshot/PDF proof was not generated by this dependency-free script.",
            ]
            if replay_mode
            else ["HTML source snapshot retained; screenshot/PDF proof was not generated by this dependency-free script."]
        ),
    )
    print(final_text(workpaper))
    if replay_mode:
        print("Caveat: Uses a locally supplied IRS HTML snapshot; verify the retained snapshot before relying on it.")
    return 0


def command_manual(args: argparse.Namespace) -> int:
    validate_completed_year(args.year)
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
    source_url = validate_manual_source_url(args.source_url)
    source_title, source_note, source_category = validate_manual_annual_metadata(
        args.source_title, args.source_note, args.source_category
    )
    retrieval_date = resolve_retrieval_date(args.retrieved)
    proof_path = Path(args.proof_file)
    staged_proof = stage_manual_proof(proof_path)
    try:
        workpaper = create_workpaper(
            output_root=args.output_root,
            currency_code=code,
            year=args.year,
            rate=rate,
            rate_direction=args.rate_direction,
            source_title=source_title,
            source_url=source_url,
            retrieval_date=retrieval_date,
            source_note=source_note,
            source_category=source_category,
            saved_proofs=[staged_proof],
            proof_limitations=[],
        )
    finally:
        staged_proof.unlink(missing_ok=True)
    print(final_text(workpaper))
    return 0


def stage_manual_proof(proof_path: Path) -> Path:
    """Copy a manual proof before creating a packet folder.

    The staged copy closes the gap between CLI validation and the workpaper
    builder's later copy. A proof that disappears at either point fails cleanly
    without creating an empty output root or packet folder.
    """
    try:
        mode = proof_path.lstat().st_mode
    except OSError as exc:
        raise RateError(f"Proof file does not exist or is not a regular file: {proof_path}", 2) from exc
    if proof_path.is_symlink() or not stat.S_ISREG(mode):
        raise RateError(f"Proof file does not exist or is not a regular file: {proof_path}", 2)

    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="get-yearly-fx-rate-proof-",
            suffix=proof_path.suffix,
            delete=False,
        ) as staged_file:
            staged_path = Path(staged_file.name)
        shutil.copy2(proof_path, staged_path)
    except FileNotFoundError as exc:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
        raise RateError(f"Proof file does not exist or is not a regular file: {proof_path}", 2) from exc
    except OSError as exc:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
        raise RateError(f"Could not stage proof file: {proof_path}: {exc}", 2) from exc

    assert staged_path is not None
    return staged_path


def command_map_check(args: argparse.Namespace) -> int:
    html, source_ref = load_irs_html(args.html_file)
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

    # Label matching tolerates IRS wording drift but keeps currencies disjoint.
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

    # Table-less HTML resolves through the text fallback parser.
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
        normalize_currency("ruble")
    except RateError as exc:
        assert exc.code == 2
        assert "RUB" in str(exc)
    else:
        raise AssertionError("ambiguous ruble should fail")

    assert resolve_retrieval_date("2024-01-01", _dt.date(2026, 1, 1)) == "2024-01-01"
    try:
        resolve_retrieval_date("2099-01-01", _dt.date(2026, 1, 1))
    except RateError as exc:
        assert exc.code == 2
        assert "cannot be later than today" in str(exc)
    else:
        raise AssertionError("future retrieval dates should fail")

    assert validate_completed_year(2025, _dt.date(2026, 1, 1)) == 2025
    for incomplete_year in (2026, 2099):
        try:
            validate_completed_year(incomplete_year, _dt.date(2026, 1, 1))
        except RateError as exc:
            assert exc.code == 2
            assert "not a completed calendar year" in str(exc)
        else:
            raise AssertionError(f"incomplete year {incomplete_year} should fail")

    try:
        find_irs_rate("COP", 2024, sample_html, IRS_YEARLY_URL)
    except RateError as exc:
        assert exc.code == 4
    else:
        raise AssertionError("COP should require a non-IRS published annual source")

    with tempfile.TemporaryDirectory() as tmp:
        # Kit smoke test: the lookup path drives workpaper-kit end to end and
        # emits a valid, linked proof packet. Byte-identity to the pre-extraction
        # baseline is asserted separately; the PDF internals are the kit's own
        # golden test (workpaper-kit/test_workpaper.py).
        html_path = Path(tmp) / "irs.html"
        html_path.write_text(sample_html, encoding="utf-8")
        lookup_args = argparse.Namespace(
            currency="CAD",
            year=2024,
            output_root=str(Path(tmp) / "proof"),
            html_file=str(html_path),
            retrieved="2024-01-01",
        )
        lookup_output = io.StringIO()
        with contextlib.redirect_stdout(lookup_output):
            command_lookup(lookup_args)
        lookup_text = lookup_output.getvalue()
        assert "Proof: [workpaper.pdf](<" in lookup_text
        assert "Artifacts: [workpaper.pdf](<" in lookup_text
        assert "[workpaper.md](<" in lookup_text
        assert "[workpaper.json](<" in lookup_text
        assert "[irs-yearly-average-local-snapshot.html](<" in lookup_text
        assert "Caveat: Uses a locally supplied IRS HTML snapshot" in lookup_text
        workpaper = next((Path(tmp) / "proof").glob("cad-2024-*/workpaper.json"))
        data = json.loads(workpaper.read_text(encoding="utf-8"))
        assert data["skill"] == "get-yearly-fx-rate"
        assert data["foreign_per_usd"] == "1.37"
        assert data["source"]["url"] == IRS_YEARLY_URL
        assert data["source"]["title"].endswith("(local HTML replay)")
        assert data["source"]["category"].endswith("(local HTML replay)")
        assert data["proof"]["workpaper_pdf_sha256"]
        for key in ("workpaper_pdf", "workpaper_md", "workpaper_json"):
            assert Path(data["proof"][key]).exists(), key
        assert data["proof"]["saved_files"][0]["filename"] == "irs-yearly-average-local-snapshot.html"
        assert data["proof"]["saved_files"][0]["packet_relative_path"] == "irs-yearly-average-local-snapshot.html"
        assert "provenance was not independently verified" in workpaper.read_text(encoding="utf-8")

        # Overwrite guard still fires (now inherited from the kit).
        rerun_err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(rerun_err):
            command_lookup(lookup_args)
        assert "replacing existing workpaper packet" in rerun_err.getvalue()

        future_lookup_args = argparse.Namespace(**vars(lookup_args))
        future_lookup_root = Path(tmp) / "incomplete-year-lookup"
        future_lookup_args.year = _dt.date.today().year
        future_lookup_args.output_root = str(future_lookup_root)
        try:
            command_lookup(future_lookup_args)
        except RateError as exc:
            assert exc.code == 2
            assert "not a completed calendar year" in str(exc)
        else:
            raise AssertionError("incomplete lookup year should be rejected")
        assert not future_lookup_root.exists(), "incomplete lookup year must not create a packet"

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
            retrieved="2024-01-01",
            proof_file=str(proof_file),
            output_root=str(Path(tmp) / "manual-proof"),
        )
        manual_output = io.StringIO()
        with contextlib.redirect_stdout(manual_output):
            command_manual(manual_args)
        manual_text = manual_output.getvalue()
        assert "Artifacts: [workpaper.pdf](<" in manual_text
        assert "[source-proof-1.html](<" in manual_text
        manual_workpaper = next((Path(tmp) / "manual-proof").glob("cop-2024-*/workpaper.json"))
        manual_data = json.loads(manual_workpaper.read_text(encoding="utf-8"))
        assert manual_data["foreign_per_usd"] == "4200"
        for key in ("workpaper_pdf", "workpaper_md", "workpaper_json"):
            assert Path(manual_data["proof"][key]).exists(), key
        saved_file = manual_data["proof"]["saved_files"][0]
        assert saved_file["filename"] == "source-proof-1.html"
        saved_path = Path(saved_file["path"])
        assert saved_path.exists()
        assert saved_path.parent.resolve() == manual_workpaper.parent.resolve()
        assert saved_file["sha256"] == hashlib.sha256(proof_file.read_bytes()).hexdigest()

        # Manual metadata is a proof gate, not a suggestion. None of these
        # may create a workpaper folder.
        invalid_metadata_cases = (
            ("empty-url", {"source_url": ""}, "absolute http"),
            ("empty-note", {"source_note": ""}, "nonempty --source-note"),
            (
                "daily-source",
                {
                    "source_title": "Daily spot quote",
                    "source_note": "This source gives a daily spot rate.",
                    "source_category": "daily spot rate",
                },
                "yearly-average rates only",
            ),
            (
                "treasury-reporting-rate",
                {
                    "source_title": "U.S. Treasury Reporting Rates of Exchange",
                    "source_note": "Source labels this as a published annual average.",
                    "source_category": "government published annual average",
                },
                "Treasury reporting rates are quarterly reporting data",
            ),
            (
                "missing-annual-language",
                {
                    "source_title": "Central Bank Rate",
                    "source_note": "Published exchange rate for the calendar year.",
                    "source_category": "central bank published annual average",
                },
                "yearly or annual average",
            ),
            (
                "incomplete-year",
                {"year": _dt.date.today().year},
                "not a completed calendar year",
            ),
            ("future-retrieval", {"retrieved": "2099-01-01"}, "cannot be later than today"),
        )
        for label, overrides, expected_message in invalid_metadata_cases:
            rejected_args = argparse.Namespace(**vars(manual_args))
            for key, value in overrides.items():
                setattr(rejected_args, key, value)
            rejected_root = Path(tmp) / f"manual-{label}"
            rejected_args.output_root = str(rejected_root)
            try:
                command_manual(rejected_args)
            except RateError as exc:
                assert exc.code == 2
                assert expected_message in str(exc)
            else:
                raise AssertionError(f"{label} manual metadata should be rejected")
            assert not rejected_root.exists(), f"{label} should not create a packet"

        directory_proof = Path(tmp) / "proof-directory"
        directory_proof.mkdir()
        broken_link = Path(tmp) / "broken-proof-link.html"
        broken_link.symlink_to(Path(tmp) / "missing-proof-target.html")
        valid_link = Path(tmp) / "valid-proof-link.html"
        valid_link.symlink_to(proof_file)
        for label, invalid_proof in (
            ("directory", directory_proof),
            ("missing", Path(tmp) / "missing-proof.html"),
            ("broken-link", broken_link),
            ("valid-link", valid_link),
        ):
            rejected_args = argparse.Namespace(**vars(manual_args))
            rejected_root = Path(tmp) / f"manual-{label}-proof"
            rejected_args.proof_file = str(invalid_proof)
            rejected_args.output_root = str(rejected_root)
            try:
                command_manual(rejected_args)
            except RateError as exc:
                assert exc.code == 2
            else:
                raise AssertionError(f"{label} proof should be rejected")
            assert not rejected_root.exists(), f"{label} proof must not create a packet"

        # The source can disappear after its initial is_file() check. Staging
        # must fail before the builder creates an output root in that case.
        vanishing_proof = Path(tmp) / "vanishing-proof.html"
        vanishing_proof.write_text("<p>published annual average</p>", encoding="utf-8")
        vanishing_args = argparse.Namespace(**vars(manual_args))
        vanishing_root = Path(tmp) / "manual-vanishing-proof"
        vanishing_args.proof_file = str(vanishing_proof)
        vanishing_args.output_root = str(vanishing_root)
        original_copy2 = shutil.copy2

        def delete_source_before_copy(source: str | Path, destination: str | Path, *copy_args: object, **copy_kwargs: object) -> str:
            if Path(source) == vanishing_proof:
                vanishing_proof.unlink()
            return original_copy2(source, destination, *copy_args, **copy_kwargs)

        shutil.copy2 = delete_source_before_copy
        try:
            try:
                command_manual(vanishing_args)
            except RateError as exc:
                assert exc.code == 2
                assert "Proof file does not exist or is not a regular file" in str(exc)
            else:
                raise AssertionError("proof deleted during staging should be rejected")
        finally:
            shutil.copy2 = original_copy2
        assert not vanishing_root.exists(), "vanishing proof must not create an output root"

        # Offline IRS replay accepts only bounded, regular UTF-8 files.
        html_directory = Path(tmp) / "html-directory"
        html_directory.mkdir()
        html_link = Path(tmp) / "html-link"
        html_link.symlink_to(html_path)
        for label, invalid_html in (("directory", html_directory), ("symlink", html_link)):
            try:
                load_irs_html(str(invalid_html))
            except RateError as exc:
                assert exc.code == 2
                assert "regular file" in str(exc)
            else:
                raise AssertionError(f"{label} IRS HTML input should be rejected")
        oversized_html = Path(tmp) / "oversized.html"
        oversized_html.write_bytes(b"x" * (MAX_HTML_SNAPSHOT_BYTES + 1))
        try:
            load_irs_html(str(oversized_html))
        except RateError as exc:
            assert exc.code == 2
            assert "safety limit" in str(exc)
        else:
            raise AssertionError("oversized IRS HTML input should be rejected")

        # Once staging succeeds, later source deletion must not affect the
        # packet: the staged copy is the artifact copied and hashed by the kit.
        late_deleted_proof = Path(tmp) / "late-deleted-proof.html"
        late_deleted_proof.write_text("<p>published annual average</p>", encoding="utf-8")
        late_deleted_args = argparse.Namespace(**vars(manual_args))
        late_deleted_root = Path(tmp) / "manual-late-deleted-proof"
        late_deleted_args.proof_file = str(late_deleted_proof)
        late_deleted_args.output_root = str(late_deleted_root)
        original_create_workpaper = create_workpaper

        def delete_source_after_staging(**kwargs: object) -> dict[str, object]:
            late_deleted_proof.unlink()
            return original_create_workpaper(**kwargs)

        globals()["create_workpaper"] = delete_source_after_staging
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                command_manual(late_deleted_args)
        finally:
            globals()["create_workpaper"] = original_create_workpaper
        late_deleted_workpaper = next(late_deleted_root.glob("cop-2024-*/workpaper.json"))
        late_deleted_data = json.loads(late_deleted_workpaper.read_text(encoding="utf-8"))
        late_saved_file = late_deleted_data["proof"]["saved_files"][0]
        assert not late_deleted_proof.exists()
        assert Path(late_saved_file["path"]).exists()
        assert late_saved_file["sha256"] == hashlib.sha256(b"<p>published annual average</p>").hexdigest()

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
    parser.add_argument(
        "--year",
        required=True,
        type=_year_arg,
        help="Completed calendar/tax year (1970-2100; current/future year rejected).",
    )
    parser.add_argument("--output-root", default="work/fx-rate-proof", help="Proof output root.")
    parser.add_argument("--retrieved", type=_iso_date_arg, help="Retrieval date YYYY-MM-DD; defaults to today.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lookup = subparsers.add_parser("lookup", help="Fetch/parse IRS yearly-average table and create proof workpaper.")
    add_common_args(lookup)
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
