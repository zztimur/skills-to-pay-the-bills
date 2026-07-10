#!/usr/bin/env python3
"""Create proof-backed workpapers for year-end USD exchange rates."""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import io
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from _workpaper import (
    RateError,
    WorkpaperSpec,
    as_abs,
    build_workpaper,
    final_text,
    fmt_decimal,
    slugify,
)

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
    "AFN": {"country": "Afghanistan", "currency": "Afghani"},
    "ALL": {"country": "Albania", "currency": "Lek"},
    "AMD": {"country": "Armenia", "currency": "Dram"},
    "AOA": {"country": "Angola", "currency": "Kwanza"},
    "ARS": {"country": "Argentina", "currency": "Peso"},
    "AUD": {"country": "Australia", "currency": "Dollar"},
    "AZN": {"country": "Azerbaijan", "currency": "Manat"},
    "BAM": {"country": "Bosnia", "currency": "Marka"},
    "BBD": {"country": "Barbados", "currency": "Dollar"},
    "BDT": {"country": "Bangladesh", "currency": "Taka"},
    "BGN": {"country": "Bulgaria", "currency": "Lev New"},
    "BHD": {"country": "Bahrain", "currency": "Dinar"},
    "BIF": {"country": "Burundi", "currency": "Franc"},
    "BMD": {"country": "Bermuda", "currency": "Dollar"},
    "BND": {"country": "Brunei", "currency": "Dollar"},
    "BOB": {"country": "Bolivia", "currency": "Boliviano"},
    "BRL": {"country": "Brazil", "currency": "Real"},
    "BSD": {"country": "Bahamas", "currency": "Dollar"},
    "BWP": {"country": "Botswana", "currency": "Pula"},
    "BZD": {"country": "Belize", "currency": "Dollar"},
    "CAD": {"country": "Canada", "currency": "Dollar"},
    "CDF": {"country": "Dem. Rep. of Congo", "currency": "Congolese Franc"},
    "CHF": {"country": "Switzerland", "currency": "Franc"},
    "CLP": {"country": "Chile", "currency": "Peso"},
    "CNY": {"country": "China", "currency": "Renminbi"},
    "COP": {"country": "Colombia", "currency": "Peso"},
    "CRC": {"country": "Costa Rica", "currency": "Colon"},
    "CUP": {"country": "Cuba", "currency": "Peso"},
    "CVE": {"country": "Cape Verde", "currency": "Escudo"},
    "CZK": {"country": "Czech Republic", "currency": "Koruna"},
    "DJF": {"country": "Djibouti", "currency": "Franc"},
    "DKK": {"country": "Denmark", "currency": "Krone"},
    "DOP": {"country": "Dominican Republic", "currency": "Peso"},
    "DZD": {"country": "Algeria", "currency": "Dinar"},
    "EGP": {"country": "Egypt", "currency": "Pound"},
    "ERN": {"country": "Eritrea", "currency": "Nakfa"},
    "ETB": {"country": "Ethiopia", "currency": "Birr"},
    "EUR": {"country": "Euro Zone", "currency": "Euro"},
    "FJD": {"country": "Fiji", "currency": "Dollar"},
    "GBP": {"country": "United Kingdom", "currency": "Pound"},
    "GEL": {"country": "Georgia", "currency": "Lari"},
    "GHS": {"country": "Ghana", "currency": "Cedi"},
    "GMD": {"country": "Gambia", "currency": "Dalasi"},
    "GNF": {"country": "Guinea", "currency": "Franc"},
    "GTQ": {"country": "Guatemala", "currency": "Quentzal"},
    "GYD": {"country": "Guyana", "currency": "Dollar"},
    "HKD": {"country": "Hong Kong", "currency": "Dollar"},
    "HNL": {"country": "Honduras", "currency": "Lempira"},
    "HTG": {"country": "Haiti", "currency": "Gourde"},
    "HUF": {"country": "Hungary", "currency": "Forint"},
    "IDR": {"country": "Indonesia", "currency": "Rupiah"},
    "ILS": {"country": "Israel", "currency": "Shekel"},
    "INR": {"country": "India", "currency": "Rupee"},
    "IQD": {"country": "Iraq", "currency": "Dinar"},
    "IRR": {"country": "Iran", "currency": "Rial"},
    "ISK": {"country": "Iceland", "currency": "Krona"},
    "JMD": {"country": "Jamaica", "currency": "Dollar"},
    "JOD": {"country": "Jordan", "currency": "Dinar"},
    "JPY": {"country": "Japan", "currency": "Yen"},
    "KES": {"country": "Kenya", "currency": "Shilling"},
    "KGS": {"country": "Kyrgyzstan", "currency": "Som"},
    "KHR": {"country": "Cambodia", "currency": "Riel"},
    "KMF": {"country": "Comoros", "currency": "Franc"},
    "KRW": {"country": "Korea", "currency": "Won"},
    "KWD": {"country": "Kuwait", "currency": "Dinar"},
    "KYD": {"country": "Cayman Islands", "currency": "Dollar"},
    "KZT": {"country": "Kazakhstan", "currency": "Tenge"},
    "LAK": {"country": "Laos", "currency": "Kip"},
    "LBP": {"country": "Lebanon", "currency": "Pound"},
    "LKR": {"country": "Sri Lanka", "currency": "Rupee"},
    "LRD": {"country": "Liberia", "currency": "Dollar"},
    "LSL": {"country": "Lesotho", "currency": "Maloti"},
    "LYD": {"country": "Libya", "currency": "Dinar"},
    "MAD": {"country": "Morocco", "currency": "Dirham"},
    "MDL": {"country": "Moldova", "currency": "LEU"},
    "MGA": {"country": "Madagascar", "currency": "Ariary"},
    "MKD": {"country": "Rep. of N. Macedonia", "currency": "Denar"},
    "MMK": {"country": "Myanmar", "currency": "Kyat"},
    "MNT": {"country": "Mongolia", "currency": "Tugrik"},
    "MRU": {"country": "Mauritania", "currency": "Ouguiya"},
    "MUR": {"country": "Mauritius", "currency": "Rupee"},
    "MVR": {"country": "Maldives", "currency": "Rufiyaa"},
    "MWK": {"country": "Malawi", "currency": "Kwacha"},
    "MXN": {"country": "Mexico", "currency": "Peso"},
    "MYR": {"country": "Malaysia", "currency": "Ringgit"},
    "MZN": {"country": "Mozambique", "currency": "Metical"},
    "NAD": {"country": "Nambia", "currency": "Dollar"},
    "NGN": {"country": "Nigeria", "currency": "Naira"},
    "NIO": {"country": "Nicaragua", "currency": "Cordoba"},
    "NOK": {"country": "Norway", "currency": "Krone"},
    "NPR": {"country": "Nepal", "currency": "Rupee"},
    "NZD": {"country": "New Zealand", "currency": "Dollar"},
    "OMR": {"country": "Oman", "currency": "Rial"},
    "PEN": {"country": "Peru", "currency": "Sol"},
    "PGK": {"country": "Papua New Guinea", "currency": "Kina"},
    "PHP": {"country": "Philippines", "currency": "Peso"},
    "PKR": {"country": "Pakistan", "currency": "Rupee"},
    "PLN": {"country": "Poland", "currency": "Zloty"},
    "PYG": {"country": "Paraguay", "currency": "Guarani"},
    "QAR": {"country": "Qatar", "currency": "Riyal"},
    "RON": {"country": "Romania", "currency": "New Leu"},
    "RSD": {"country": "Serbia", "currency": "Dinar"},
    "RUB": {"country": "Russia", "currency": "Ruble"},
    "RWF": {"country": "Rwanda", "currency": "Franc"},
    "SAR": {"country": "Saudi Arabia", "currency": "Riyal"},
    "SBD": {"country": "Solomon Islands", "currency": "Dollar"},
    "SCR": {"country": "Seychelles", "currency": "Rupee"},
    "SDG": {"country": "Sudan", "currency": "Pound"},
    "SEK": {"country": "Sweden", "currency": "Krona"},
    "SGD": {"country": "Singapore", "currency": "Dollar"},
    "SLE": {"country": "Sierra Leone", "currency": "Leone"},
    "SOS": {"country": "Somali", "currency": "Shilling"},
    "SRD": {"country": "Suriname", "currency": "Dollar"},
    "SSP": {"country": "South Sudan", "currency": "Sudanese Pound"},
    "STN": {"country": "Sao Tome & Principe", "currency": "New Dobras"},
    "SYP": {"country": "Syria", "currency": "Pound"},
    "SZL": {"country": "Eswatini", "currency": "Lilangeni"},
    "THB": {"country": "Thailand", "currency": "Baht"},
    "TJS": {"country": "Tajikistan", "currency": "Somoni"},
    "TMT": {"country": "Turkmenistan", "currency": "New Manat"},
    "TND": {"country": "Tunisia", "currency": "Dinar"},
    "TOP": {"country": "Tonga", "currency": "Pa'anga"},
    "TRY": {"country": "Turkey", "currency": "New Lira"},
    "TTD": {"country": "Trinidad & Tobago", "currency": "Dollar"},
    "TWD": {"country": "Taiwan", "currency": "Dollar"},
    "TZS": {"country": "Tanzania", "currency": "Shilling"},
    "UAH": {"country": "Ukraine", "currency": "Hryvnia"},
    "UGX": {"country": "Uganda", "currency": "Shilling"},
    "UYU": {"country": "Uruguay", "currency": "Peso"},
    "UZS": {"country": "Uzbekistan", "currency": "Som"},
    "VES": {"country": "Venezuela", "currency": "Bolivar Soberano"},
    "VND": {"country": "Vietnam", "currency": "Dong"},
    "VUV": {"country": "Vanuatu", "currency": "Vatu"},
    "WST": {"country": "Western Samoa", "currency": "Tala"},
    "XAF": {"country": "Cameroon", "currency": "CFA Franc"},
    "XCD": {"country": "Antigua & Barbuda", "currency": "E. Caribbean Dollar"},
    "XCG": {"country": "Curacao", "currency": "Caribbean Guilder"},
    "XOF": {"country": "Benin", "currency": "CFA Franc"},
    "YER": {"country": "Yemen", "currency": "Rial"},
    "ZAR": {"country": "South Africa", "currency": "Rand"},
    "ZMW": {"country": "Zambia", "currency": "New Kwacha"},
    "ZWG": {"country": "Zimbabwe", "currency": "Gold"},
}

# The Fiscal Data table has several country rows for one current ISO currency.
# Lookups use each code's canonical row above; map-check still classifies every
# equivalent live row as mapped instead of treating it as an unsupported gap.
TREASURY_ROW_ALTERNATES_BY_CODE = {
    "EUR": ({"country": "Cyprus", "currency": "Euro"},),
    "XAF": (
        {"country": "Central African Rep.", "currency": "CFA Franc"},
        {"country": "Chad", "currency": "CFA Franc"},
        {"country": "Congo", "currency": "CFA Franc"},
        {"country": "Equatorial Guinea", "currency": "CFA Franc"},
        {"country": "Gabon", "currency": "CFA Franc"},
    ),
    "XCD": (
        {"country": "Grenada", "currency": "E.Caribbean Dollar"},
        {"country": "St. Lucia", "currency": "E. Caribbean Dollar"},
    ),
    "XOF": (
        {"country": "Burkina Faso", "currency": "CFA Franc"},
        {"country": "Cote D'ivoire", "currency": "CFA Franc"},
        {"country": "Guinea Bissau", "currency": "CFA Franc"},
        {"country": "Mali", "currency": "CFA Franc"},
        {"country": "Niger", "currency": "CFA Franc"},
        {"country": "Senegal", "currency": "CFA Franc"},
        {"country": "Togo", "currency": "CFA Franc"},
    ),
}

# These rows are deliberately not lookup targets. Each must stay explicit so a
# strict full-table map check distinguishes a known exclusion from a stale map.
TREASURY_ROW_EXCEPTIONS = {
    ("Cuba", "Chavito"): "legacy CUC-style unit; not a current ISO 4217 lookup target",
    ("Ecuador", "Dolares"): "USD-denominated row",
    ("El Salvador", "Dollar"): "USD-denominated row",
    ("Liberia", "Dollar"): "USD-denominated row",
    ("Marshall Islands", "U.S. Dollar"): "USD-denominated row",
    ("Micronesia", "U.S. Dollar"): "USD-denominated row",
    ("Palau", "Dollar"): "USD-denominated row",
    ("Panama", "Dolares"): "USD-denominated row",
    ("Timor", "Leste-Dili"): "legacy local unit; Timor-Leste uses USD",
    ("Venezuela", "Fuerte (OLD)"): "obsolete VEF currency",
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

KNOWN_CURRENCY_CODES = set(TREASURY_ROWS_BY_CODE) | set(ALIASES.values())

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

AVERAGE_LANGUAGE = re.compile(r"\b(?:annual(?:ly)?|yearly|average(?:s|d|ing)?)\b", re.IGNORECASE)


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
    if AVERAGE_LANGUAGE.search(combined):
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


def parse_user_rate(raw: str) -> Decimal:
    """Parse a manual rate without guessing at locale or separator intent."""
    text = clean_text(raw)
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise RateError(
            f"Rate '{raw}' is not an unambiguous number. Use digits with an optional "
            f"period decimal and no thousands separators, e.g. 3900, 3900.00, or 0.00025.",
            3,
        )
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise RateError(f"Could not parse rate value '{raw}'.", 3) from exc


def today_iso() -> str:
    return _dt.date.today().isoformat()


def treasury_query_url(code: str, year: int, api_url: str) -> str:
    if code not in TREASURY_ROWS_BY_CODE:
        raise RateError(
            f"No Treasury/Fiscal Data row mapping is known for {code}. This is a map-maintenance issue, not evidence that Treasury lacks a rate; update the map and rerun map-check.",
            4,
        )

    mapping = TREASURY_ROWS_BY_CODE[code]
    filters = [
        f"record_date:eq:{year}-12-31",
        f"currency:eq:{mapping['currency']}",
        f"country:eq:{mapping['country']}",
    ]
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


_LABEL_TOKEN_NORMALIZATIONS = {
    "dem": "democratic",
    "n": "north",
    "nambia": "namibia",
    "rep": "republic",
    "st": "saint",
    "turkey": "turkiye",
}


def _label_tokens(value: object) -> list[str]:
    text = re.sub(r"\(.*?\)", " ", clean_text(value).casefold())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [_LABEL_TOKEN_NORMALIZATIONS.get(token, token) for token in text.split() if token]


def labels_match(expected: object, candidate: object) -> bool:
    """Match Treasury labels through small wording changes without guessing.

    Country and currency are matched independently. Prefix tolerance covers
    stable Treasury variations such as ``Rep.``/``Republic`` and
    ``New Lira``/``Lira``; it does not collapse genuinely distinct labels such
    as ``Rial`` and ``Riyal``.
    """
    expected_tokens = _label_tokens(expected)
    candidate_tokens = _label_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return False
    # Treasury sometimes drops ``New`` (for example New Lira/Lira). Do not
    # permit a broader subset match: it would confuse Guinea with Equatorial
    # Guinea, Franc with CFA Franc, and Sudan with South Sudan.
    expected_tokens = [token for token in expected_tokens if token != "new"]
    candidate_tokens = [token for token in candidate_tokens if token != "new"]
    if len(expected_tokens) != len(candidate_tokens):
        return False
    used = [False] * len(candidate_tokens)
    for expected_token in expected_tokens:
        matched = False
        for index, candidate_token in enumerate(candidate_tokens):
            if used[index]:
                continue
            if expected_token == candidate_token or (
                len(expected_token) >= 3
                and len(candidate_token) >= 3
                and (
                    expected_token.startswith(candidate_token)
                    or candidate_token.startswith(expected_token)
                )
            ):
                used[index] = True
                matched = True
                break
        if not matched:
            return False
    return True


def mappings_for_code(code: str) -> tuple[dict[str, str], ...]:
    if code not in TREASURY_ROWS_BY_CODE:
        return ()
    return (TREASURY_ROWS_BY_CODE[code], *TREASURY_ROW_ALTERNATES_BY_CODE.get(code, ()))


def treasury_mapping_matches(mapping: dict[str, str], record: dict[str, object]) -> bool:
    return labels_match(mapping["country"], record.get("country")) and labels_match(
        mapping["currency"], record.get("currency")
    )


def mapped_treasury_code(record: dict[str, object]) -> str | None:
    for code in TREASURY_ROWS_BY_CODE:
        if any(treasury_mapping_matches(mapping, record) for mapping in mappings_for_code(code)):
            return code
    return None


def treasury_exception_reason(record: dict[str, object]) -> str | None:
    for (country, currency), reason in TREASURY_ROW_EXCEPTIONS.items():
        if labels_match(country, record.get("country")) and labels_match(currency, record.get("currency")):
            return reason
    return None


def find_treasury_rate(currency_raw: str, year: int, json_text: str, query_url: str) -> TreasuryRate:
    code = normalize_currency(currency_raw)
    if code not in TREASURY_ROWS_BY_CODE:
        raise RateError(
            f"No Treasury/Fiscal Data row mapping is known for {code}. This is a map-maintenance issue, not evidence that Treasury lacks a rate; update the map and rerun map-check.",
            4,
        )

    records = treasury_records_from_payload(json_text)

    year_end_date = f"{year}-12-31"
    matches: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if clean_text(record.get("record_date")) != year_end_date:
            continue
        if not any(treasury_mapping_matches(mapping, record) for mapping in mappings_for_code(code)):
            continue
        matches.append(record)

    if not matches:
        mapping = TREASURY_ROWS_BY_CODE[code]
        mapped_name = f"{mapping['country']} {mapping['currency']}"
        raise RateError(
            f"Treasury/Fiscal Data did not match the current {code} mapping ({mapped_name}) for {year_end_date}. Treat this as a map-maintenance issue and rerun map-check before concluding Treasury lacks a rate.",
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


def workpaper_folder(output_root: str, currency_code: str, year: int, source_title: str) -> Path:
    return Path(output_root) / f"{currency_code.lower()}-{year}-{slugify(source_title)}"


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
    """Thin adapter over workpaper-kit's build_workpaper.

    Keeps the year-end domain rule in the skill (reject average language before
    building) and maps this skill's create_workpaper signature onto a
    WorkpaperSpec. proof_required=True preserves the strict "raise on a missing
    proof file" policy; the year-end-only fields (year_end_date, treasury_record,
    purpose, source.year_end_confirmed) ride extra_json so workpaper.json keeps
    its exact top-level shape. The PDF now uses the kit's (yearly's) engine.
    """
    reject_average_language(source_title, source_note, source_category)
    saved_proof_list = list(saved_proofs)
    spec = WorkpaperSpec(
        output_root=output_root,
        skill_name="get-year-end-fx-rate",
        currency_code=currency_code,
        year=year,
        rate=rate,
        rate_direction=rate_direction,
        source_title=source_title,
        source_url=source_url,
        source_category=source_category,
        retrieval_date=retrieval_date,
        source_note=source_note,
        document_title="Year-End FX Rate Workpaper",
        document_subtitle="Year-end exchange-rate proof packet",
        rate_phrase=f"year-end ({year_end_date})",
        caveats=[
            "This is a retained support workpaper, not legal or tax advice.",
            "Use this for year-end support only; do not reuse it as an income-tax average rate.",
        ],
        extra_rows=[("Year-end date", year_end_date)],
        extra_json={
            "purpose": "FBAR-style year-end USD exchange-rate support",
            "year_end_date": year_end_date,
            "treasury_record": treasury_record,
            "source": {"year_end_confirmed": True},
        },
        saved_proofs=saved_proof_list,
        proof_required=bool(saved_proof_list),
        proof_limitations=proof_limitations,
    )
    return build_workpaper(spec)


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
    if code not in KNOWN_CURRENCY_CODES and not args.allow_unknown_code:
        raise RateError(
            f"Currency code {code} is not in this skill's known Treasury/alias set. If it is a "
            f"real ISO 4217 code, re-run with --allow-unknown-code; otherwise fix the currency.",
            2,
        )
    if not args.year_end_confirmed:
        raise RateError("Pass --year-end-confirmed after verifying the source supports a year-end or YYYY-12-31 rate.", 2)
    source_note = clean_text(args.source_note)
    if not source_note:
        raise RateError("Pass a nonempty --source-note confirming why this source supports year-end use.", 2)
    reject_average_language(args.source_title, source_note, args.source_category)
    proof_files = [Path(args.proof_file)] if args.proof_file else []
    no_proof_reason = clean_text(args.no_proof_file_reason)
    if proof_files and no_proof_reason:
        raise RateError("Pass either --proof-file or --no-proof-file-reason, not both.", 2)
    if not proof_files and not no_proof_reason:
        raise RateError("Pass --proof-file or explain its absence with --no-proof-file-reason.", 2)
    proof_limitations = []
    if not proof_files:
        proof_limitations.append(f"No saved source proof file was supplied. Reason: {no_proof_reason}")
    workpaper = create_workpaper(
        output_root=args.output_root,
        currency_code=code,
        year=args.year,
        year_end_date=f"{args.year}-12-31",
        rate=parse_user_rate(args.rate),
        rate_direction=args.rate_direction,
        source_title=args.source_title,
        source_url=args.source_url,
        retrieval_date=args.retrieved or today_iso(),
        source_note=source_note,
        source_category=args.source_category,
        saved_proofs=proof_files,
        proof_limitations=proof_limitations,
    )
    print(final_text(workpaper))
    if proof_limitations:
        print(f"Caveat: {proof_limitations[0]}")
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
            raise RateError(
                f"No Treasury/Fiscal Data row mapping is known for {code}. This is a map-maintenance issue; update the map before treating Treasury as unavailable.",
                4,
            )
        matches = [
            record
            for record in rows
            if any(treasury_mapping_matches(mapping, record) for mapping in mappings_for_code(code))
        ]
        print(f"Parsed Treasury/Fiscal Data rows for {year_end_date}: {len(rows)} from {source_ref}")
        if matches:
            desc = clean_text(matches[0].get("country_currency_desc")) or f"{matches[0].get('country')}-{matches[0].get('currency')}"
            print(f"{code} mapped row found: {desc}")
            return 0
        print(f"{code} mapping did not match a Treasury/Fiscal Data row for {year_end_date}; map maintenance is required.")
        return 4 if args.strict else 0

    mapped_count = 0
    exception_count = 0
    unexplained = []
    for record in rows:
        if mapped_treasury_code(record):
            mapped_count += 1
            continue
        if treasury_exception_reason(record):
            exception_count += 1
            continue
        desc = clean_text(record.get("country_currency_desc")) or f"{record.get('country')}-{record.get('currency')}"
        unexplained.append(clean_text(desc))

    print(f"Parsed Treasury/Fiscal Data rows for {year_end_date}: {len(rows)} from {source_ref}")
    print(f"Mapped rows: {mapped_count}")
    print(f"Classified exceptions: {exception_count}")
    if unexplained:
        print("Unexplained rows (map maintenance required):")
        for item in sorted(set(unexplained)):
            print(f"- {item}")
        if args.strict:
            return 4
    else:
        print("All parsed rows are mapped or explicitly classified.")
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
    assert labels_match("Rep. of N. Macedonia", "Republic of North Macedonia")
    assert labels_match("New Lira", "Lira")
    assert not labels_match("Guinea", "Equatorial Guinea")
    assert not labels_match("Franc", "CFA Franc")
    assert not labels_match("Rial", "Riyal")

    try:
        normalize_currency("peso")
    except RateError as exc:
        assert exc.code == 2
    else:
        raise AssertionError("ambiguous peso should fail")

    for average_source in (
        "published yearly average",
        "Central Bank 2025 Average",
        "Central Bank average for 2025",
        "annual rate",
        "annual-average rate",
    ):
        try:
            reject_average_language(average_source)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"average source should fail: {average_source}")

    assert parse_user_rate("3900") == Decimal("3900")
    assert parse_user_rate("3900.00") == Decimal("3900.00")
    assert parse_user_rate("0.00025") == Decimal("0.00025")
    for bad in ("1.234,56", "4,200", "1.2.3", "1e3", "abc"):
        try:
            parse_user_rate(bad)
        except RateError as exc:
            assert exc.code == 3, bad
        else:
            raise AssertionError(f"parse_user_rate should reject {bad!r}")

    assert _year_arg("1900") == 1900
    assert _year_arg("2025") == 2025
    for bad in ("1899", "2101", "abc"):
        try:
            _year_arg(bad)
        except argparse.ArgumentTypeError:
            pass
        else:
            raise AssertionError(f"_year_arg should reject {bad!r}")

    assert _iso_date_arg("2026-07-09") == "2026-07-09"
    for bad in ("not-a-date", "2026-13-01", "07/09/2026", "20260709", "2026-7-9"):
        try:
            _iso_date_arg(bad)
        except argparse.ArgumentTypeError:
            pass
        else:
            raise AssertionError(f"_iso_date_arg should reject {bad!r}")

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
        assert data["skill"] == "get-year-end-fx-rate"
        assert data["foreign_per_usd"] == "3900.25"
        assert data["usd_per_foreign"] == "0.000256393821"
        assert data["year_end_date"] == "2025-12-31"
        assert data["source"]["year_end_confirmed"] is True
        assert data["proof"]["saved_files"][0]["filename"] == "treasury-fiscal-data-response.json"
        assert "average" not in json.dumps(data["source"]).lower()
        # PDF now uses the kit (yearly) engine; assert the generalized layout and
        # that year-end never renders average language.
        assert_pdf(
            Path(data["proof"]["workpaper_pdf"]),
            [
                "Year-End FX Rate Workpaper",
                "RETAINED SUPPORT WORKPAPER",
                "1 USD = 3900.25 COP",
                "2025-12-31",
                "Year-end date",
                "Treasury Reporting Rates of Exchange",
                "SOURCE PROOF",
                "treasury-fiscal-data-response.json",
                "get-year-end-fx-rate support workpaper",
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

        fixture_path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "treasury-2025-12-31.json"
        fixture_args = argparse.Namespace(
            year=2025,
            currency=None,
            api_url=TREASURY_API_URL,
            api_file=str(fixture_path),
            strict=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_map_check(fixture_args) == 0
        fixture_text = fixture_path.read_text(encoding="utf-8")
        thb_rate = find_treasury_rate("THB", 2025, fixture_text, treasury_query_url("THB", 2025, TREASURY_API_URL))
        assert thb_rate.rate == Decimal("31.66")

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
            no_proof_file_reason="",
            allow_unknown_code=False,
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
                "1 USD = 4000 COP",
                "2025-12-31",
                "Year-end date",
                "Example Central Bank Year-End Rate",
                "source-proof-1.html",
                "SOURCE PROOF",
            ],
            ["yearly average", "annual average"],
        )

        bad_manual_args = argparse.Namespace(**{**manual_args.__dict__, "source_note": "Central Bank 2025 Average"})
        try:
            command_manual(bad_manual_args)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("manual yearly-average wording should fail")

        missing_note_args = argparse.Namespace(**{**manual_args.__dict__, "source_note": ""})
        try:
            command_manual(missing_note_args)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("manual source note should be required")

        missing_proof_args = argparse.Namespace(**{**manual_args.__dict__, "proof_file": None})
        try:
            command_manual(missing_proof_args)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("manual proof or reason should be required")

        both_proof_args = argparse.Namespace(
            **{**manual_args.__dict__, "no_proof_file_reason": "A reason"}
        )
        try:
            command_manual(both_proof_args)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("manual proof and reason should be mutually exclusive")

        no_proof_args = argparse.Namespace(
            **{
                **manual_args.__dict__,
                "proof_file": None,
                "no_proof_file_reason": "The source page could not be saved during retrieval.",
                "output_root": str(Path(tmp) / "manual-no-proof"),
            }
        )
        no_proof_output = io.StringIO()
        with contextlib.redirect_stdout(no_proof_output):
            assert command_manual(no_proof_args) == 0
        assert "Caveat: No saved source proof file was supplied. Reason: The source page could not be saved during retrieval." in no_proof_output.getvalue()
        no_proof_json = next((Path(tmp) / "manual-no-proof").glob("cop-2025-*/workpaper.json"))
        no_proof_data = json.loads(no_proof_json.read_text(encoding="utf-8"))
        assert no_proof_data["proof"]["saved_files"] == []
        assert no_proof_data["proof"]["limitations"] == [
            "No saved source proof file was supplied. Reason: The source page could not be saved during retrieval."
        ]

        unknown_args = argparse.Namespace(
            **{
                **manual_args.__dict__,
                "currency": "XQZ",
                "allow_unknown_code": False,
                "output_root": str(Path(tmp) / "unknown-proof"),
            }
        )
        try:
            command_manual(unknown_args)
        except RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("unknown currency code should require --allow-unknown-code")

        allowed_unknown_args = argparse.Namespace(
            **{**unknown_args.__dict__, "allow_unknown_code": True}
        )
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_manual(allowed_unknown_args) == 0

    print("self-test passed")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--currency", required=True, help="Currency code or unambiguous currency name.")
    parser.add_argument("--year", required=True, type=_year_arg, help="Calendar year (1900-2100).")
    parser.add_argument("--output-root", default="work/fbar-fx-rate-proof", help="Proof output root.")
    parser.add_argument("--retrieved", type=_iso_date_arg, help="Retrieval date YYYY-MM-DD; defaults to today.")


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
    manual.add_argument("--source-note", required=True, help="Note confirming why the source supports year-end use.")
    manual.add_argument("--year-end-confirmed", action="store_true", help="Required confirmation that the source supports a year-end or YYYY-MM-DD rate.")
    manual.add_argument("--allow-unknown-code", action="store_true", help="Confirm a real ISO 4217 code that is not in the skill's known Treasury/alias set.")
    manual.add_argument("--proof-file", help="Local screenshot/PDF/HTML/source proof file to copy, hash, and reference.")
    manual.add_argument("--no-proof-file-reason", default="", help="Required explanation when no local source proof file is available; cannot be used with --proof-file.")
    manual.set_defaults(func=command_manual)

    map_check = subparsers.add_parser("map-check", help="Compare a Treasury/Fiscal Data year-end response with the hard-coded currency map.")
    map_check.add_argument("--year", required=True, type=_year_arg, help="Calendar year to inspect (1900-2100).")
    map_check.add_argument("--currency", help="Optional ISO code or unambiguous currency name to check against the map.")
    map_check.add_argument("--api-url", default=TREASURY_API_URL, help="Treasury/Fiscal Data API endpoint.")
    map_check.add_argument("--api-file", help="Use a local JSON response instead of fetching the API.")
    map_check.add_argument("--strict", action="store_true", help="Exit nonzero when unmapped Treasury rows are found.")
    map_check.set_defaults(func=command_map_check)

    self_test = subparsers.add_parser("self-test", help="Run dependency-free parser/workpaper tests.")
    self_test.set_defaults(func=command_self_test)
    return parser


def _year_arg(raw: str) -> int:
    try:
        year = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"year must be an integer, got {raw!r}")
    if not (1900 <= year <= 2100):
        raise argparse.ArgumentTypeError(f"year {year} is outside the supported range 1900-2100")
    return year


def _iso_date_arg(raw: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise argparse.ArgumentTypeError(f"retrieved date must be YYYY-MM-DD, got {raw!r}")
    try:
        _dt.date.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"retrieved date must be a real YYYY-MM-DD date, got {raw!r}")
    return raw


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
