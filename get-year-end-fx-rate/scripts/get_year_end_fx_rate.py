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


def today_iso() -> str:
    return _dt.date.today().isoformat()


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
        saved_proofs=list(saved_proofs),
        proof_required=True,
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
                "1 USD = 4000 COP",
                "2025-12-31",
                "Year-end date",
                "Example Central Bank Year-End Rate",
                "source-proof-1.html",
                "SOURCE PROOF",
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
