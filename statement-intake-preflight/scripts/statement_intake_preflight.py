#!/usr/bin/env python3
"""Preflight machine-readable bank statement PDFs for downstream workflows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

_PDFPLUMBER_IMPORT_ERROR: BaseException | None = None
try:
    import pdfplumber
except BaseException as _exc:  # noqa: BLE001 - see below  # pragma: no cover
    # A broken native dependency (e.g. a cryptography/pyo3 ABI mismatch) raises a
    # non-Exception PanicException at import time, which "except ImportError"
    # misses -- crashing even dependency-check with a raw traceback. Degrade to
    # "unavailable" instead, but never swallow a genuine interrupt or exit.
    if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
        raise
    pdfplumber = None
    _PDFPLUMBER_IMPORT_ERROR = _exc


# Handoff-contract version, distinct from the package version in plugin.json.
# Downstream skills (fbar-threshold-check, statements-to-interest) pin the set of
# schema versions they accept, so bump this only on a breaking JSON change and
# update those consumers in lockstep.
SCHEMA_VERSION = "1.0"
MIN_TEXT_CHARS = 40
MIN_TAX_YEAR = 1970
MAX_TAX_YEAR = 2100
SUPPORTED_SCOPES = {"one-account", "one-institution"}
# Exit code returned for a review-required result only when the caller opts in
# with --exit-nonzero-on-review; the default exit stays 0 so the agent workflow
# (which reads the JSON) is unchanged.
REVIEW_EXIT_CODE = 3

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
    "us dollar": "USD",
    "u.s. dollar": "USD",
    "united states dollar": "USD",
    # NOTE: no bare "usd" entry. A three-letter code alias would substring-match
    # anywhere ("BUSD-STAKE", a card-FX disclosure line) and confirm USD with no
    # corroboration -- the one code that could then out-vote a genuinely
    # corroborated foreign code and hand off the wrong currency ungated. USD must
    # earn confirmation like every other code: via the "$"/"US$" symbol rules, a
    # currency-label word, or adjacency to an amount. Multi-word names above are
    # self-corroborating (you do not write "us dollar" incidentally) and stay.
}

# Explicit designation word ("Account Number", "A/C No.", "Cuenta Nro."):
# consumes the label so the capture starts at the identifier. The capture is
# still validated by account_token(), which rejects word-only matches such as
# "Account Number Summary".
ACCOUNT_LABEL_RE = re.compile(
    r"\b(?:account|acct|a/c|cuenta)\s*"
    # Assert the word boundary on the label word, THEN consume an optional
    # trailing dot. Putting \b after the dot ("no\.?\b") failed for
    # dot-terminated labels ("No.", "Nro.", "Núm.") because there is no
    # boundary between "." and the following space, so those forms matched
    # nothing at all.
    r"(?:numbers?|nos?|nbrs?|nros?|n[uú]ms?|id)\b\.?"
    # The capture class allows ',' and '&' so a plural label's whole list is
    # captured; detect_account_hints splits it back apart on those separators.
    r"[\s:#-]*([*Xx0-9A-Za-z][*Xx0-9A-Za-z.,&\- ]{2,33})",
    re.I,
)
# Bare designation immediately followed by a digit/masked identifier
# ("Account 12345678", "Cuenta 001234", German "Konto 12345678" / "Kontonummer
# 12345678", French "Compte 12345678"). The capture must START with a digit or
# masking char, so a following word ("Account Summary", "Account holder JUAN")
# cannot match at all. "kontonummer" precedes "konto" so the longer German label
# is consumed whole rather than leaving a stray "nummer".
ACCOUNT_BARE_RE = re.compile(
    r"\b(?:account|acct|a/c|cuenta|n[uú]mero de cuenta|"
    r"kontonummer|konto|compte|num[eé]ro de compte)\b[\s:#-]*([*Xx0-9][*Xx0-9.\- ]{3,33})",
    re.I,
)
ACCOUNT_IBAN_RE = re.compile(r"\bIBAN\b[\s:#-]*([A-Z]{2}\d{2}[A-Z0-9 ]{6,40})", re.I)
ACCOUNT_ENDING_RE = re.compile(r"\b(?:ending in|ends in|termina en)\s*([*Xx0-9]{2,8})", re.I)

# Transaction/counterparty context. An account number or IBAN on such a line
# belongs to the other party, not the statement holder, so it must not be
# harvested as an account hint (a SEPA transfer names the payee's IBAN, not
# yours). "titular"/"holder" are deliberately NOT here -- they mark the owner.
COUNTERPARTY_RE = re.compile(
    r"\b(?:transfer(?:red|s|encia|encias)?|transferido|virement|[uü]berweisung|"
    r"sepa|swift|beneficiar(?:y|io)|recipient|payee|remitter|remitente|"
    r"destinatario|empf[aä]nger)\b",
    re.I,
)
# Conjunctions/separators that join several account numbers under one plural
# label ("Account Nos. 11112222 and 33334444", "Accounts 111, 222 & 333"). The
# space lookahead keeps a bare thousands comma from splitting a single number.
_ACCOUNT_SEP_RE = re.compile(r"\b(?:and|y|und)\b|[,&](?=\s)", re.I)

TITLE_TERMS = (
    "statement",
    "extracto",
    "movimientos",
    "account summary",
    "resumen",
    "period",
    "periodo",
    "cuenta",
)

INSTITUTION_TERMS = (
    "bank",
    "banco",
    "banque",
    "sparkasse",
    "sparkassen",
    "credit union",
    "brokerage",
    "financial",
    "fiduciary",
    "trust",
    "global66",
    "wise",
    "revolut",
)

# Match institution terms only at word boundaries. Substring matching wrongly
# fired on "otherwi(se)", "like(wise)", and "(trust)ed"; a boundary match keeps
# the fintech brands ("Wise", "Revolut") while ignoring those common words.
INSTITUTION_TERM_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in INSTITUTION_TERMS) + r")\b",
    re.I,
)

# "Banco"/"Bank" also head one-token brand names ("Bancolombia", "Bancomer",
# "Bankia", "Bankinter") that a word-boundary match misses. Match them as a stem
# -- but only "banco"/"bank" (not the bare "banc" stem, which would also catch
# the Spanish adjective "bancario" and "bancarrota"), and reject the handful of
# common words that share the stem, so marketing prose ("Mobile banking made
# easy", "avoid bankruptcy") cannot become a header institution hint.
INSTITUTION_STEM_RE = re.compile(r"\b(?:banco|bank)\w+", re.I)
# A stem stopword like "banking" is excluded on its own (marketing prose), but
# a corporate entity name built on it IS a real institution ("Lloyds Banking
# Group", "First Banking Corporation", "Meridian Bank Holdings"). Match a
# capitalized/all-caps proper-noun lead word + "bank(ing)" + a corporate
# designator: the lead requirement is what separates the name "Lloyds Banking
# Group" from the marketing line "our banking group rewards today" (a lowercase
# function word before the phrase). The lead's case is verified in
# line_names_institution, since re.I would make an inline [A-Z] case-blind.
INSTITUTION_BANKING_ENTITY_RE = re.compile(
    r"(?P<lead>\w[\w&.\-]*)\s+bank(?:ing)?\s+"
    r"(?:corp(?:oration)?|company|co|group|holdings?|association|trust|union)\b",
    re.I,
)
INSTITUTION_STEM_STOPWORDS = {
    "banking",
    "banked",
    "banks",
    "banker",
    "bankers",
    "bankable",
    "bankrupt",
    "bankruptcy",
    "banknote",
    "banknotes",
    "bankroll",
    "bancos",
    "bancomat",
}
# A one-token brand can also END in "bank" ("Commerzbank", "Postbank",
# "Rabobank"), which the start-anchored prefix stem above cannot see (there is no
# word boundary before "bank" inside "Commerzbank"). But an unbounded set of
# common English nouns also ends in "bank" ("foodbank", "snowbank", "bloodbank",
# ...), which a stopword blocklist can never fully enumerate. So a suffix match is
# trusted only when its line ALSO carries banking/statement context (a document
# word, an account label, or a legal-entity suffix): that admits real mastheads
# ("Commerzbank Kontoauszug", "Rabobank Account Statement") while rejecting prose
# that merely ends in "-bank" ("Local Foodbank donation drive"). The stopword set
# is kept as a second guard for the rare common-noun-plus-context line.
INSTITUTION_STEM_SUFFIX_RE = re.compile(r"\b\w*bank\b", re.I)
INSTITUTION_CONTEXT_RE = re.compile(
    r"\b(?:statement|kontoauszug|auszug|rekeningafschrift|afschrift|relev[eé]|"
    r"estratto|account|konto|rekening|compte|conto|cuenta|iban|bic|swift|"
    r"a\.?g\.?|n\.?a\.?|n\.?v\.?|s\.?a\.?|s\.?p\.?a\.?|gmbh|plc|inc|ltd)\b",
    re.I,
)
INSTITUTION_STEM_SUFFIX_STOPWORDS = {
    "bank",  # the bare word is already an INSTITUTION_TERM, matched earlier
    "riverbank",
    "databank",
    "interbank",
    "nonbank",
    "sandbank",
    "fogbank",
    "piggybank",
    "mountebank",
    "burbank",
    "wordbank",
    "foodbank",
    "snowbank",
    "bloodbank",
    "seedbank",
    "greenbank",
    "cutbank",
    "outbank",
}


def line_names_institution(line: str) -> bool:
    """Whether a line names a financial institution.

    Word-boundary term match, plus a "banco"/"bank" stem so a one-token brand
    is recognized while common stem-sharing words are excluded -- except when a
    stopword like "banking" heads a corporate entity name led by a proper noun.
    """
    if INSTITUTION_TERM_RE.search(line):
        return True
    for match in INSTITUTION_BANKING_ENTITY_RE.finditer(line):
        # Require the lead word to be a proper noun (capitalized/all-caps), so
        # "Lloyds Banking Group" counts but "our banking group rewards" does not.
        if match.group("lead")[:1].isupper():
            return True
    if any(
        match.group(0).casefold() not in INSTITUTION_STEM_STOPWORDS
        for match in INSTITUTION_STEM_RE.finditer(line)
    ):
        return True
    # Suffix brand stems: a token ending in "bank" ("Commerzbank", "Postbank",
    # "Rabobank") that the start-anchored prefix stem cannot reach -- but only
    # when the line also carries banking/statement context, so common -bank nouns
    # in prose ("Foodbank", "snowbank") are not read as institutions. The stopword
    # set is a second guard for a common-noun-plus-context line.
    if INSTITUTION_CONTEXT_RE.search(line):
        return any(
            match.group(0).casefold() not in INSTITUTION_STEM_SUFFIX_STOPWORDS
            for match in INSTITUTION_STEM_SUFFIX_RE.finditer(line)
        )
    return False

# A customer mailing address that happens to sit on a street with a bank-like
# name (a number followed by "<Bank-word> Street") is not the statement's
# institution. Drop lines that begin with a street number and carry a street
# suffix, so an address printed above the masthead cannot become the file's
# institution signature.
STREET_ADDRESS_RE = re.compile(
    r"^\s*\d{1,6}\s+.*\b(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|"
    r"ln|lane|ct|court|way|pl|place|plaza|sq|square|suite|ste|"
    r"calle|avenida|carrera|rua|stra(?:ss|ß)e)\b",
    re.I,
)

MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

# Structural statement words stripped before comparing two institution hints, so
# that the same bank across months ("Example Bank January Statement" vs
# "... February Statement") collapses to one signature instead of looking like
# two institutions.
INSTITUTION_NOISE = {
    "statement",
    "statements",
    "account",
    "accounts",
    "monthly",
    "quarterly",
    "annual",
    "period",
    "periodo",
    "summary",
    "resumen",
    "extracto",
    "movimientos",
    "cuenta",
    "page",
    "date",
    "the",
    "of",
    "for",
    "and",
    "de",
    "del",
    "la",
    "el",
} | set(MONTH_NAMES)

# Legal-entity suffixes dropped from an institution signature so the same bank
# reads the same whether a given statement spells out its legal name or not
# ("Example Bank N.A." vs "Example Bank"). Ambiguous two-letter words that could
# be a real name part (e.g. "co", "ab") are deliberately excluded.
INSTITUTION_LEGAL_SUFFIXES = {
    "na",
    "sa",
    "nv",
    "ag",
    "plc",
    "ltd",
    "ltda",
    "llc",
    "inc",
    "corp",
    "cia",
    "sac",
    "srl",
    "spa",
    "gmbh",
    "bv",
    "oyj",
    "asa",
    # Latin-American entity forms: "S.A. de C.V." -> cv, "S.A.B." -> sab,
    # "S.A.P.I." -> sapi, "S. de R.L." -> rl.
    "cv",
    "sab",
    "sapi",
    "rl",
}

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
ISO_CURRENCY_RE = re.compile(r"\b[A-Z]{3}\b")

# A 4-digit year printed next to one of these markers describes the institution
# or the document, not the statement period: a copyright/legal footer ("© 2019",
# "(c) 2019 ... All rights reserved") or a heritage/membership tagline ("since
# 1904", "Established 1852", "Member FDIC since 1933"). Almost every real
# statement carries a copyright footer whose year rarely equals the tax year, so
# a year within YEAR_CONTEXT_WINDOW characters of a marker is dropped from year
# coverage; otherwise that footer alone would force an otherwise-clean statement
# into the mixed-years review gate. Kept tight (word-boundaried markers, small
# window) so a genuine period year on the same line is never suppressed.
# Year markers come in two kinds, handled differently in detect_years:
#   HARD copyright/legal markers ("© 2019", "(c) 2019 ... All rights reserved")
#   never head a statement period -- a year in their clause is always dropped,
#   even in "© May 2019" or a "© 2019-2024" range.
#   DUAL-USE markers ("since"/"established"/"founded"/"Member FDIC") also head
#   real dates, so a year next to them is dropped only when it is bare; a year
#   carrying a NUMERIC date ("since 2025-04-01", "...since 01.01.2025") is kept.
# A month name is NOT treated as date context for dual-use markers, because
# "Customer since March 2015" is an account-open/heritage date, not a period.
HARD_COPYRIGHT_MARKER_RE = re.compile(r"©|\(c\)|copyright|all rights reserved", re.I)
DUAL_USE_MARKER_RE = re.compile(
    r"\bsince\b|\bestablished\b|\best\.|\bfounded\b|member\s+(?:fdic|sipc)",
    re.I,
)
YEAR_CONTEXT_WINDOW = 8

# Two numeric dates (dd.mm.yyyy, dd/mm/yyyy, yyyy-mm-dd) joined by a range
# connector, so a statement that prints its period numerically ("01.01.2025 -
# 31.01.2025", "01/01/2025 al 31/01/2025") is recognized as a period even with
# no month name or period word. The dash connector allows no surrounding space;
# the alphabetic connectors ("to", "al", "bis", "hasta", "through") require it,
# so a lone hyphen elsewhere cannot bridge two unrelated numbers.
NUMERIC_PERIOD_RE = re.compile(
    r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"
    r"(?:\s*[-–—]\s*|\s+(?:to|al?|bis|hasta|through)\s+)"
    r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}",
    re.I,
)

# A three-letter ISO token is only trusted as a currency when it is corroborated:
# either its line carries a currency-label word, or the code sits directly beside
# an amount. This keeps all-caps prose ("PLEASE TRY OUR APP") and merchant names
# out of the confirmed set while still surfacing them as weak candidates.
CURRENCY_LABEL_RE = re.compile(
    # English/Spanish plus German (Währung, umlaut-stripped Wahrung) -- so a
    # European statement that labels its currency in its own language ("Währung:
    # CHF") still confirms the code. Deliberately NOT "devise" (collides with the
    # common English verb, "devise a EUR plan") nor "valuta" (means "value date"
    # in German/Nordic banking, "Valuta 15.01.2025 USD", not currency): both
    # produced false confirmations. French/Italian/Dutch statements still confirm
    # via the € symbol or an adjacent amount, so nothing real is lost.
    r"\b(?:currenc(?:y|ies)|monedas?|divisas?|w[aä]hrung|"
    r"denominat(?:ed|ion)|iso\s*4217)\b",
    re.I,
)
# A currency label only confirms a code within this many characters of it, so
# "Currency: COP" confirms COP but "currency conversion fees ... in COP and MXN"
# (disclosure boilerplate) does not.
CURRENCY_LABEL_WINDOW = 16
# A currency symbol glued to a country/currency prefix resolves an otherwise
# ambiguous sign: "US$"/"U$S"/"US $" -> USD, "R$" -> BRL (Brazil), "S/." or "S/"
# -> PEN (Peru). Each requires the disambiguating prefix, so a bare "$" is still
# flagged ambiguous. The sol pattern additionally requires a *monetary* amount
# (a decimal-cents figure) right after it, because "S/" is also serial/series
# shorthand -- "Reference S/ 0099887" is a document number, not 9,887 soles.
SYMBOL_CURRENCY_RULES = (
    (re.compile(r"\bUS ?\$|\bU\$S\b", re.I), "USD", "US$"),
    (re.compile(r"\bR\$", re.I), "BRL", "R$"),
    (re.compile(r"\bS/\.?\s?\d[\d.,]*[.,]\d{2}\b", re.I), "PEN", "S/"),
)
_CURRENCY_CODE_ALT = "|".join(sorted(CURRENCY_CODES))
# An "amount" must look monetary, not just be a digit run: a currency symbol, a
# decimal-cents figure, a thousands-grouped figure, or a long (>=5 digit) number.
# A bare 1-4 digit integer no longer counts, so a year ("2025 TRY") or a clock
# fragment ("TRY 24/7") can no longer corroborate a currency code. Thousands
# separators include the Swiss apostrophe (ASCII ' and U+2019) so "1'234.56"
# reads as an amount.
_AMOUNT = (
    r"[-+(]?\s*(?:"
    r"[$€£¥]\s?\d[\d.,'’]*"
    r"|\d{1,3}(?:[.,'’]\d{3})+(?:[.,]\d{1,2})?"
    r"|\d+[.,]\d{2}"
    r"|\d{5,}"
    r")"
    # An optional trailing close-paren (accounting negative "(1,234.56) USD") or
    # trailing minus (European debit "1.234,56- EUR", ASCII '-' or U+2212). This
    # sign sits BETWEEN the figure and the code; without consuming it the code
    # would lose amount-adjacency and drop to a weak, unconfirmed candidate on
    # every negative line. Optional and glued, so it only extends a figure that
    # already matched -- "(see note 5)" and a range "2020-2024" are not amounts.
    r"[)\-−]?"
)
CURRENCY_AMOUNT_RE = re.compile(
    # No word boundary between the code and the amount, so a glued "EUR1.234,56"
    # or "987.65GBP" is recognized; the outer boundaries still anchor the code.
    rf"\b(?P<pre>{_CURRENCY_CODE_ALT})\s*{_AMOUNT}"
    rf"|{_AMOUNT}\s*(?P<post>{_CURRENCY_CODE_ALT})\b"
)

# Unicode superscript (¹²³⁰⁴-⁹) and subscript (₀-₉) digits -- footnote/reference
# markers a PDF text layer emits. Stripped at ingestion by clean_line before
# NFKC would fold them into ordinary digits. Note ¹²³ live at U+00B9/B2/B3, apart
# from the U+2070 block, and U+2071-2073 are not digits, so the ranges are split.
FOOTNOTE_DIGIT_RE = re.compile(r"[²³¹⁰⁴-⁹₀-₉]")


class PreflightError(Exception):
    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def clean_line(value: str) -> str:
    # Drop superscript/subscript footnote markers BEFORE NFKC. On a statement a
    # "³" after a figure or code is a reference pointer ("Closing balance 100.00
    # EUR³", "see note²"), never data -- but NFKC would fold it to an ordinary
    # "3", both destroying the word boundary a currency code needs ("EUR³" ->
    # "EUR3", no longer matchable) and fabricating a digit inside what reads as an
    # amount. Stripping must happen here, while the mark is still a superscript
    # and distinguishable from a real digit; after NFKC it is too late.
    value = FOOTNOTE_DIGIT_RE.sub("", value)
    # NFKC at ingestion folds compatibility characters that a PDF text layer
    # routinely emits -- ligatures ("ﬁnancial" -> "financial"), full-width
    # digits, the numero sign -- so detectors see canonical ASCII-ish text
    # instead of missing a term spelled with a ligature. Accents are preserved
    # here (NFKC keeps precomposed "á"); they are folded only for institution
    # signature comparison, so displayed hints keep their diacritics.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def fold_accents(text: str) -> str:
    """Drop combining diacritics so 'Bogotá' and 'Bogota' compare equal."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def stable_unique(values: Iterable[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = clean_line(str(value))
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if limit is not None and len(out) >= limit:
            break
    return out


def normalize_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def add_gate(gates: list[dict[str, str]], code: str, message: str, severity: str = "review") -> None:
    if not any(gate.get("code") == code and gate.get("message") == message for gate in gates):
        gates.append({"code": code, "severity": severity, "message": message})


def split_lines(text: str) -> list[str]:
    return [clean_line(line) for line in text.splitlines() if clean_line(line)]


def load_pdf_files(paths: list[str]) -> list[dict[str, object]]:
    if pdfplumber is None:
        raise PreflightError("pdfplumber is required for preflight. Use a Python environment that has pdfplumber installed.")

    files: list[dict[str, object]] = []
    for raw_path in paths:
        path = Path(raw_path)
        warnings: list[str] = []
        pages: list[dict[str, object]] = []
        if path.suffix.lower() != ".pdf":
            warnings.append(f"{path.name} is not a PDF.")
            files.append(file_profile(path, [], warnings, is_pdf=False))
            continue
        if not path.exists():
            warnings.append(f"{path} was not found.")
            files.append(file_profile(path, [], warnings, is_pdf=True, text_layer_expected=False))
            continue
        read_failed = False
        try:
            with pdfplumber.open(str(path)) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    pages.append({"page": index, "text": text})
        except Exception as exc:  # pragma: no cover - depends on malformed PDF internals.
            warnings.append(f"{path.name} could not be read as a PDF: {exc}")
            read_failed = True
        files.append(
            file_profile(
                path, pages, warnings, is_pdf=True, text_layer_expected=not read_failed,
                content_sha256=file_sha256(path),
            )
        )
    return files


def file_sha256(path: Path) -> str | None:
    """SHA-256 of a file's bytes, or None if it cannot be read.

    Two statements with different names but identical bytes -- the classic
    duplicated download -- share a digest even though their paths differ, which
    path-based duplicate detection alone cannot see.
    """
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:  # pragma: no cover - unreadable file already warned elsewhere.
        return None


def file_profile(
    path: Path,
    pages: list[dict[str, object]],
    warnings: list[str],
    is_pdf: bool,
    text_layer_expected: bool = True,
    content_sha256: str | None = None,
) -> dict[str, object]:
    text = "\n".join(str(page.get("text", "")) for page in pages)
    lines = split_lines(text)
    char_count = len(text.strip())
    if is_pdf and text_layer_expected and char_count < MIN_TEXT_CHARS:
        warnings.append(f"{path.name} has little machine-readable text; scanned/image-only PDFs are out of scope for v1.")
    return {
        "file": str(path),
        "resolved_file": normalize_path(path),
        "is_pdf": is_pdf,
        "page_count": len(pages),
        "character_count": char_count,
        "content_sha256": content_sha256,
        "lines": lines,
        "warnings": stable_unique(warnings),
    }


# ISO_DATE_TAIL_RE matches the "-MM-DD" that FOLLOWS a year heading a numeric
# date ("2025-04-01"); the tail form ("01.01.2025") is caught by the preceding
# separator instead. MONTH_ADJ_RE finds a month name; it is used ONLY to bridge a
# hard copyright marker to its year across an intervening month ("© May 2019"),
# never to rescue a year next to a dual-use marker.
ISO_DATE_TAIL_RE = re.compile(r"[-/.]\d{1,2}[-/.]\d{1,2}")
MONTH_ADJ_RE = re.compile(r"\b(?:" + "|".join(MONTH_NAMES) + r")\b", re.I)
MONTH_YEAR_WINDOW = 5


def _marker_reaches_year(marker_spans: list, yspan: tuple, month_spans: list) -> bool:
    """Whether a marker sits near a year on the line -- directly (within
    YEAR_CONTEXT_WINDOW), or bridged by an intervening month name ("© May 2019",
    "Member since January 2015"). The month bridge makes suppression independent
    of the month name's length, so "since March"/"since January"/"since
    September" all behave identically."""
    if any(_span_gap(marker, yspan) <= YEAR_CONTEXT_WINDOW for marker in marker_spans):
        return True
    return any(
        _span_gap(marker, mon) <= YEAR_CONTEXT_WINDOW and _span_gap(mon, yspan) <= MONTH_YEAR_WINDOW
        for marker in marker_spans
        for mon in month_spans
    )


def detect_years(lines: Iterable[str]) -> list[int]:
    years: set[int] = set()
    for line in lines:
        hard_spans = [match.span() for match in HARD_COPYRIGHT_MARKER_RE.finditer(line)]
        dual_spans = [match.span() for match in DUAL_USE_MARKER_RE.finditer(line)]
        month_spans = [match.span() for match in MONTH_ADJ_RE.finditer(line)]
        for match in YEAR_RE.finditer(line):
            yspan = match.span()
            # Hard copyright/legal marker: a year in its clause is never a
            # statement period. Drop it whether it abuts the marker directly
            # ("© 2019", each end of a "© 2019-2024" range) or through a month
            # name ("© May 2019", "Copyright January 2020").
            # Known limitation (low, fail-safe): a rare comma-separated multi-year
            # copyright list ("© 2019, 2020, 2021") can leak a trailing year past
            # the window; transitive year-run suppression is deliberately not added
            # for so uncommon a footer. Worst case is a mixed-years review prompt,
            # never a wrong result -- and copyright *ranges* are fully covered.
            if _marker_reaches_year(hard_spans, yspan, month_spans):
                continue
            # Dual-use marker ("since"/"established"/...): keep a year that carries
            # a NUMERIC date -- the tail of one ("...since 01.01.2025") or the head
            # of one ("since 2025-04-01") -- and suppress a bare or month-only year
            # ("since 1904", "Customer since March 2015"), which is a heritage/
            # account-open date, not a period, and would else false-trip
            # mixed-years on an otherwise-clean statement.
            numeric_date = (
                (match.start() > 0 and line[match.start() - 1] in "./-")
                or ISO_DATE_TAIL_RE.match(line, match.end()) is not None
            )
            if not numeric_date and _marker_reaches_year(dual_spans, yspan, month_spans):
                continue
            years.add(int(match.group(1)))
    return sorted(years)


def detect_statement_titles(lines: Iterable[str]) -> list[str]:
    titles: list[str] = []
    for line in lines:
        low = line.casefold()
        if any(term in low for term in TITLE_TERMS):
            titles.append(line)
    return stable_unique(titles, limit=12)


def detect_periods(lines: Iterable[str]) -> list[str]:
    periods: list[str] = []
    for line in lines:
        low = line.casefold()
        has_period_term = any(term in low for term in ("period", "periodo", "from", "to", "desde", "hasta", "statement date"))
        has_month = any(month in low for month in MONTH_NAMES)
        has_year = bool(YEAR_RE.search(line))
        if (has_period_term and has_year) or (has_month and has_year) or NUMERIC_PERIOD_RE.search(line):
            periods.append(line)
    return stable_unique(periods, limit=20)


def account_token(raw: str) -> str | None:
    """Reduce a captured account match to its identifier, or reject free text.

    Keeps the leading run of digits/masking (allowing a short alpha prefix like
    "ABX" and internal separators), dropping any trailing words the greedy
    capture pulled in, and rejects captures that are not identifier-shaped --
    e.g. a holder name or "Summary for January".
    """
    cleaned = clean_line(raw)
    match = re.match(r"[A-Za-z]{0,4}[*Xx0-9](?:[*Xx0-9]|[ .\-](?=[*Xx0-9]))*", cleaned)
    if not match:
        return None
    token = match.group(0).strip(" .-")
    compact = re.sub(r"[ .\-]", "", token)
    digits = sum(char.isdigit() for char in compact)
    masks = sum(char in "*Xx" for char in compact)
    # Require at least four digit/mask characters. A real account identifier has
    # them; a one-or-two-digit run captured after a label (a page count or table
    # index such as "12 of 34") is not an account.
    if digits + masks < 4:
        return None
    return token


def iban_mod97_ok(iban: str) -> bool:
    """ISO 13616 check: move the first four chars to the end, map letters to
    their base-36 values, and require the whole number mod 97 == 1."""
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def iban_token(raw: str) -> str | None:
    """Reduce a captured IBAN to its checksum-valid compact form, or reject.

    The capture can swallow trailing words on the line (a holder name after the
    IBAN); trimming to the longest mod-97-valid prefix drops that tail and, as a
    bonus, collapses grouped and compact spellings of one IBAN to a single hint.
    """
    compact = re.sub(r"\s+", "", raw).upper()
    match = re.match(r"[A-Z]{2}\d{2}[A-Z0-9]+", compact)
    if not match:
        return None
    candidate = match.group(0)
    for end in range(min(len(candidate), 34), 14, -1):
        prefix = candidate[:end]
        if iban_mod97_ok(prefix):
            return prefix
    return None


def _account_compact(hint: str) -> str:
    """Separator-free uppercase form of an account/IBAN hint, for comparison."""
    return re.sub(r"[ .\-]", "", hint).upper()


def _account_trailing_digits(compact: str) -> str:
    match = re.search(r"\d+$", compact)
    return match.group(0) if match else ""


def _same_account(a: str, a_partial: bool, b: str, b_partial: bool) -> bool:
    """Whether two compacted hints denote the same account.

    Separator-free equality always counts, so a spaced header and a compact
    footer with the same digits, or a grouped IBAN and its compact spelling,
    collapse to one account. Beyond exact equality, only a *partial* hint -- one
    that is masked ("****6666") or an "ending in" suffix -- may absorb into
    another by matching trailing digits, so two distinct full numbers never
    merge on a shared tail (that stays a real possible-mixed-accounts signal).
    """
    if a == b:
        return True
    if not (a_partial or b_partial):
        return False
    ta, tb = _account_trailing_digits(a), _account_trailing_digits(b)
    if len(ta) < 3 or len(tb) < 3:
        return False
    return ta.endswith(tb) or tb.endswith(ta)


def _dedupe_accounts(raw: list[tuple[str, str, bool]], limit: int = 20) -> list[str]:
    """Collapse hints that are the same account printed in different forms.

    Each raw entry is (display, compact, is_partial). Full (non-partial) hints
    are considered first so partial hints attach to them and the fuller spelling
    wins as the display form. Conservative by construction: genuinely different
    full account numbers stay separate and still trip possible-mixed-accounts.
    """
    ordered = [item for item in raw if not item[2]] + [item for item in raw if item[2]]
    kept: list[tuple[str, str, bool]] = []
    for display, compact, partial in ordered:
        if not compact:
            continue
        merged = False
        for index, (_kept_display, kept_compact, kept_partial) in enumerate(kept):
            if _same_account(compact, partial, kept_compact, kept_partial):
                # Prefer the fuller spelling: a masked/suffix hint yields to a
                # full number once one is seen for the same account.
                if kept_partial and not partial:
                    kept[index] = (display, compact, partial)
                merged = True
                break
        if not merged:
            kept.append((display, compact, partial))

    out: list[str] = []
    seen: set[str] = set()
    for display, _compact, _partial in kept:
        cleaned = clean_line(display)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def detect_account_hints(lines: Iterable[str]) -> list[str]:
    raw: list[tuple[str, str, bool]] = []
    for line in lines:
        if COUNTERPARTY_RE.search(line):
            # A transfer/counterparty line names the other party's account or
            # IBAN, not the statement holder's; skip it entirely.
            continue
        for pattern in (ACCOUNT_LABEL_RE, ACCOUNT_BARE_RE):
            for match in pattern.finditer(line):
                # Split on conjunctions so a plural label ("Account Nos. X and Y")
                # surfaces every account, not just the first.
                for part in _ACCOUNT_SEP_RE.split(match.group(1)):
                    token = account_token(part)
                    if token:
                        compact = _account_compact(token)
                        # A masked token ("****6666") only pins a suffix.
                        raw.append((token, compact, "*" in compact or "X" in compact))
        for match in ACCOUNT_IBAN_RE.finditer(line):
            token = iban_token(match.group(1))
            if token:
                raw.append((token, _account_compact(token), False))
        for match in ACCOUNT_ENDING_RE.finditer(line):
            fragment = clean_line(match.group(1))
            # An "ending in N" fragment is inherently a partial (suffix) hint.
            raw.append((fragment, _account_compact(fragment), True))
    return _dedupe_accounts(raw)


def detect_institution_hints(lines: Iterable[str]) -> list[str]:
    hints: list[str] = []
    for line in list(lines)[:80]:
        # A statement's institution name lives in the header. Keep any early line
        # that names an institution term or a "banco"/"bank" brand stem; do not
        # drop it just because it also says "statement" ("Alpha Bank Statement",
        # "Wise Account Statement" are exactly the headers we want).
        if line_names_institution(line) and not STREET_ADDRESS_RE.search(line):
            hints.append(line)
    return stable_unique(hints, limit=12)


def institution_signature(hint: str) -> str:
    """Normalize an institution hint for equality comparison.

    Merges runs of single letters ("U.S." -> "us", "M&T" -> "mt") so
    initial-based names stay distinct instead of collapsing to their shared
    word, then strips structural statement words and legal-entity suffixes so
    cosmetic per-statement differences do not read as different institutions.
    """
    # Fold accents first: otherwise "[^a-z ]" would turn "bogotá" into "bogot "
    # (accent -> space, truncating the word) while "bogota" stays intact, so the
    # same bank across two text layers would read as two institutions.
    lowered = re.sub(r"[^a-z ]+", " ", fold_accents(hint.casefold()))
    merged: list[str] = []
    letters = ""
    for token in lowered.split():
        if len(token) == 1:
            letters += token
        else:
            if letters:
                merged.append(letters)
                letters = ""
            merged.append(token)
    if letters:
        merged.append(letters)
    tokens = [
        token
        for token in merged
        if len(token) > 1 and token not in INSTITUTION_NOISE and token not in INSTITUTION_LEGAL_SUFFIXES
    ]
    return " ".join(tokens)


def _span_gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Character gap between two spans on a line (0 if they overlap)."""
    if a[1] <= b[0]:
        return b[0] - a[1]
    if b[1] <= a[0]:
        return a[0] - b[1]
    return 0


def detect_currency(lines: Iterable[str]) -> dict[str, object]:
    lines = list(lines)
    joined = "\n".join(lines)
    confirmed: list[str] = []
    weak: list[str] = []
    symbol_markers: set[str] = set()
    alias_evidence: list[str] = []

    for line in lines:
        label_spans = [match.span() for match in CURRENCY_LABEL_RE.finditer(line)]
        adjacent = {
            (match.group("pre") or match.group("post")).upper()
            for match in CURRENCY_AMOUNT_RE.finditer(line)
            if (match.group("pre") or match.group("post"))
        }
        # A code beside a monetary amount is confirmed -- even glued
        # ("EUR1.234,56"), where the bare three-letter scan below can't see it.
        confirmed.extend(adjacent)
        for match in ISO_CURRENCY_RE.finditer(line):
            token = match.group(0).upper()
            if token not in CURRENCY_CODES or token in adjacent:
                continue
            near_label = any(_span_gap(span, match.span()) <= CURRENCY_LABEL_WINDOW for span in label_spans)
            if near_label:
                confirmed.append(token)
            else:
                weak.append(token)

    low = joined.casefold()
    for alias, code in CURRENCY_ALIASES.items():
        if alias in low:
            confirmed.append(code)
            alias_evidence.append(alias)
    for pattern, code, marker in SYMBOL_CURRENCY_RULES:
        if pattern.search(joined):
            confirmed.append(code)
            symbol_markers.add(marker)
    if "$" in joined:
        symbol_markers.add("$")
    for symbol, code in (("€", "EUR"), ("£", "GBP"), ("¥", "JPY")):
        if symbol in joined:
            symbol_markers.add(symbol)
            confirmed.append(code)

    confirmed_codes = sorted(set(confirmed))
    weak_candidates = sorted(set(weak) - set(confirmed_codes))
    if len(confirmed_codes) == 1:
        code = confirmed_codes[0]
    elif len(confirmed_codes) > 1:
        code = "MIXED"
    else:
        code = "UNKNOWN"
    return {
        "code": code,
        "candidates": confirmed_codes,
        "weak_candidates": weak_candidates,
        "symbol_markers": sorted(symbol_markers),
        "alias_evidence": stable_unique(alias_evidence),
        "ambiguous_dollar": "$" in symbol_markers and not confirmed_codes,
    }


def build_preflight(files: list[dict[str, object]], tax_year: int, scope: str, out_path: Path, csv_path: Path) -> dict[str, object]:
    if scope not in SUPPORTED_SCOPES:
        raise PreflightError(f"Unsupported scope {scope!r}. Use one-account or one-institution.")
    if not (MIN_TAX_YEAR <= tax_year <= MAX_TAX_YEAR):
        raise PreflightError(f"tax_year {tax_year} is outside the supported range {MIN_TAX_YEAR}-{MAX_TAX_YEAR}.")

    warnings: list[str] = []
    gates: list[dict[str, str]] = []
    all_lines: list[str] = []
    statement_files: list[dict[str, object]] = []
    for item in files:
        file_warnings = [str(warning) for warning in item.get("warnings", [])]
        warnings.extend(file_warnings)
        is_pdf = bool(item.get("is_pdf", True))
        missing_file = any("was not found" in warning for warning in file_warnings)
        unreadable_pdf = any("could not be read as a PDF" in warning for warning in file_warnings)
        structural_stop = False
        if not is_pdf:
            add_gate(gates, "non-pdf-input", f"{Path(str(item.get('file'))).name} is not a PDF.", "stop")
            structural_stop = True
        if missing_file:
            add_gate(gates, "missing-file", f"{item.get('file')} was not found.", "stop")
            structural_stop = True
        if unreadable_pdf:
            add_gate(gates, "unreadable-pdf", f"{Path(str(item.get('file'))).name} could not be read as a PDF.", "stop")
            structural_stop = True
        if not structural_stop and int(item.get("character_count", 0)) < MIN_TEXT_CHARS:
            add_gate(gates, "low-text-pdf", f"{Path(str(item.get('file'))).name} has little machine-readable text.", "stop")
            structural_stop = True
        lines = [str(line) for line in item.get("lines", [])]
        if not structural_stop:
            all_lines.extend(lines)
        years = detect_years(lines)
        statement_files.append(
            {
                "file": item.get("file"),
                "resolved_file": item.get("resolved_file"),
                "is_pdf": item.get("is_pdf"),
                "page_count": item.get("page_count"),
                "character_count": item.get("character_count"),
                "content_sha256": item.get("content_sha256"),
                "detected_years": years,
                "detected_periods": detect_periods(lines),
                "statement_titles": detect_statement_titles(lines),
                "currency": detect_currency(lines),
                "account_hints": detect_account_hints(lines),
                "institution_hints": detect_institution_hints(lines),
                "warnings": file_warnings,
            }
        )

    resolved_counts: Counter[str] = Counter(
        str(item.get("resolved_file") or item.get("file") or "") for item in statement_files
    )
    duplicate_inputs = sorted(path for path, count in resolved_counts.items() if path and count > 1)
    if duplicate_inputs:
        message = f"The same statement file was supplied more than once: {', '.join(Path(path).name for path in duplicate_inputs)}."
        warnings.append(message)
        add_gate(gates, "duplicate-input", message)

    # Byte-identical files supplied under different names (a re-downloaded
    # statement) share a content digest even though their paths differ, which
    # the path check above cannot see. Only flag when the matching digest spans
    # more than one distinct path, so a same-path repeat stays a duplicate-input.
    content_groups: dict[str, list[dict[str, object]]] = {}
    for item in statement_files:
        digest = item.get("content_sha256")
        if digest:
            content_groups.setdefault(str(digest), []).append(item)
    for group in content_groups.values():
        distinct_paths = {str(entry.get("resolved_file") or entry.get("file")) for entry in group}
        if len(distinct_paths) > 1:
            names = sorted({Path(str(entry.get("file"))).name for entry in group})
            message = f"Byte-identical statements were supplied under different names: {', '.join(names)}."
            warnings.append(message)
            add_gate(gates, "duplicate-content", message)

    detected_years = sorted({year for item in statement_files for year in item.get("detected_years", [])})
    outside_years = [year for year in detected_years if year != tax_year]
    if outside_years:
        message = f"Detected year(s) outside requested tax year {tax_year}: {', '.join(str(year) for year in outside_years)}."
        warnings.append(message)
        add_gate(gates, "mixed-years", message)
    if all_lines and not detected_years:
        warnings.append("No statement years were detected; verify the PDFs belong to the requested tax year.")
        add_gate(gates, "unknown-year-coverage", "No statement years were detected; verify statement periods manually.")

    currency = detect_currency(all_lines)
    if currency["ambiguous_dollar"]:
        message = "Currency uses '$' but no unambiguous ISO code or currency name was found."
        warnings.append(message)
        add_gate(gates, "ambiguous-dollar", message)
    if all_lines and currency["code"] == "UNKNOWN":
        message = "No account currency marker was found; downstream extraction may need an explicit account currency."
        warnings.append(message)
        add_gate(gates, "unknown-currency", message)
    if currency["code"] == "MIXED":
        message = f"Multiple currency candidates were found: {', '.join(currency['candidates'])}."
        warnings.append(message)
        add_gate(gates, "mixed-currencies", message)

    account_hints = detect_account_hints(all_lines)
    if scope == "one-account" and len(account_hints) > 1:
        message = f"Multiple account hints found; verify this is one account: {', '.join(account_hints[:8])}."
        warnings.append(message)
        add_gate(gates, "possible-mixed-accounts", message)
    if all_lines and scope == "one-account" and not account_hints:
        message = "No account number/designation hint was found; verify this is one account."
        warnings.append(message)
        add_gate(gates, "unknown-account", message)

    # Compare institutions from the union of per-file hints, not a re-scan of the
    # first 80 lines of every file concatenated: a long first statement used to
    # push later files out of view, hiding a second bank entirely. Compare one
    # representative header signature per file so two banks are caught while the
    # same bank across months is not falsely split.
    institution_hints = stable_unique(
        hint for item in statement_files for hint in item.get("institution_hints", [])
    )
    per_file_institution_signatures: set[str] = set()
    for item in statement_files:
        file_hints = [str(hint) for hint in item.get("institution_hints", []) if str(hint).strip()]
        if not file_hints:
            continue
        signature = institution_signature(file_hints[0])
        if signature:
            per_file_institution_signatures.add(signature)
    if scope == "one-institution" and len(per_file_institution_signatures) > 1:
        message = f"Multiple institution hints found; verify this is one institution: {', '.join(institution_hints[:6])}."
        warnings.append(message)
        add_gate(gates, "possible-mixed-institutions", message)
    if all_lines and scope == "one-institution" and not institution_hints:
        message = "No institution hint was found in early statement text; verify the institution manually."
        warnings.append(message)
        add_gate(gates, "unknown-institution", message)

    title_values = stable_unique((title for item in statement_files for title in item.get("statement_titles", [])), limit=20)
    period_values = stable_unique((period for item in statement_files for period in item.get("detected_periods", [])), limit=30)
    primary_institution = most_common_hint(institution_hints)
    status = "review-required" if gates else "ready-for-domain-extraction"

    return {
        "schema_version": SCHEMA_VERSION,
        "skill": "statement-intake-preflight",
        "status": status,
        "tax_year": tax_year,
        "scope": scope,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "profile": {
            "primary_institution": primary_institution,
            "statement_titles": title_values,
            "statement_file_count": len(statement_files),
            "total_page_count": sum(int(item.get("page_count") or 0) for item in statement_files),
            "total_character_count": sum(int(item.get("character_count") or 0) for item in statement_files),
        },
        "statement_files": statement_files,
        "currency": currency,
        "account_hints": account_hints,
        "institution_hints": institution_hints,
        "coverage_hints": {
            "requested_tax_year": tax_year,
            "detected_years": detected_years,
            "outside_requested_years": outside_years,
            "detected_periods": period_values,
            "low_text_files": [
                str(item.get("file"))
                for item in statement_files
                if item.get("is_pdf")
                and int(item.get("character_count") or 0) < MIN_TEXT_CHARS
                and not any(
                    marker in str(warning)
                    for warning in item.get("warnings", [])
                    for marker in ("was not found", "could not be read as a PDF")
                )
            ],
        },
        "warnings": stable_unique(warnings),
        "review_gates": gates,
        "artifacts": {"review_csv": str(csv_path)},
    }


def most_common_hint(hints: list[str]) -> str:
    if not hints:
        return ""
    normalized = [re.sub(r"[^a-z0-9]+", " ", hint.casefold()).strip() for hint in hints]
    counts = Counter(normalized)
    target = counts.most_common(1)[0][0]
    for hint, normalized_hint in zip(hints, normalized, strict=True):
        if normalized_hint == target:
            return hint
    return hints[0]


def review_csv_path(out_path: Path) -> Path:
    return out_path.with_name(f"{out_path.stem}-review.csv")


def write_json(path: Path, data: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        # e.g. --out points beneath an existing file, or an unwritable directory.
        raise PreflightError(f"Could not write {path}: {exc}") from exc


def csv_safe(value: object) -> str:
    """Neutralize spreadsheet formula injection in a review-CSV cell.

    A statement is untrusted input: a cell beginning with =, +, -, @, or a
    leading control character can execute as a formula when the CSV is opened in
    Excel or Sheets. Prefix such values with an apostrophe so they render as
    literal text (CWE-1236).
    """
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def write_review_csv(path: Path, data: dict[str, object]) -> None:
    fields = [
        "file",
        "is_pdf",
        "page_count",
        "character_count",
        "detected_years",
        "detected_periods",
        "statement_titles",
        "currency_code",
        "currency_candidates",
        "account_hints",
        "institution_hints",
        "warnings",
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("w", newline="", encoding="utf-8")
    except OSError as exc:
        # e.g. --out points beneath an existing file, or an unwritable directory.
        raise PreflightError(f"Could not write {path}: {exc}") from exc
    with handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in data.get("statement_files", []):
            if not isinstance(item, dict):
                continue
            currency = item.get("currency") if isinstance(item.get("currency"), dict) else {}
            row = {
                "file": item.get("file"),
                "is_pdf": item.get("is_pdf"),
                "page_count": item.get("page_count"),
                "character_count": item.get("character_count"),
                "detected_years": "; ".join(str(year) for year in item.get("detected_years", [])),
                "detected_periods": "; ".join(str(period) for period in item.get("detected_periods", [])),
                "statement_titles": "; ".join(str(title) for title in item.get("statement_titles", [])),
                "currency_code": currency.get("code") if isinstance(currency, dict) else "",
                "currency_candidates": "; ".join(str(code) for code in currency.get("candidates", []) if isinstance(currency, dict)),
                "account_hints": "; ".join(str(hint) for hint in item.get("account_hints", [])),
                "institution_hints": "; ".join(str(hint) for hint in item.get("institution_hints", [])),
                "warnings": "; ".join(str(warning) for warning in item.get("warnings", [])),
            }
            writer.writerow({key: csv_safe(value) for key, value in row.items()})


def check_output_paths(out_path: Path, csv_path: Path, pdf_paths: list[str]) -> None:
    """Refuse output paths that would clobber each other or an input PDF.

    Writing the CSV over the JSON (or over a source statement) silently destroys
    data, so fail fast with a clear message instead.
    """
    out_resolved = normalize_path(out_path)
    csv_resolved = normalize_path(csv_path)
    if out_resolved == csv_resolved:
        raise PreflightError(f"--out and --csv resolve to the same path ({out_path}); choose distinct files.")
    inputs = {normalize_path(pdf): pdf for pdf in pdf_paths}
    for label, resolved, original in (("--out", out_resolved, out_path), ("--csv", csv_resolved, csv_path)):
        if resolved in inputs:
            raise PreflightError(f"{label} ({original}) would overwrite an input PDF ({inputs[resolved]}); choose another path.")


def review_exit_code(status: str, exit_nonzero_on_review: bool) -> int:
    if exit_nonzero_on_review and status == "review-required":
        return REVIEW_EXIT_CODE
    return 0


def command_preflight(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    csv_path = Path(args.csv) if args.csv else review_csv_path(out_path)
    check_output_paths(out_path, csv_path, args.pdf)
    files = load_pdf_files(args.pdf)
    data = build_preflight(files, int(args.tax_year), args.scope, out_path, csv_path)
    write_json(out_path, data)
    write_review_csv(csv_path, data)
    print(f"Wrote preflight JSON: {out_path}")
    print(f"Wrote review CSV: {csv_path}")
    print(f"Status: {data['status']}")
    gates = data.get("review_gates", [])
    if gates:
        print("Review gates:")
        for gate in gates:
            if isinstance(gate, dict):
                print(f"- {gate.get('code')}: {gate.get('message')}")
    return review_exit_code(str(data["status"]), args.exit_nonzero_on_review)


def command_dependency_check(_args: argparse.Namespace) -> int:
    if pdfplumber is None:
        if _PDFPLUMBER_IMPORT_ERROR is not None:
            print(f"pdfplumber missing (installed but failed to import: {type(_PDFPLUMBER_IMPORT_ERROR).__name__})")
        else:
            print("pdfplumber missing")
        return 1
    print("pdfplumber ok")
    return 0


def command_smoke_test(_args: argparse.Namespace) -> int:
    if pdfplumber is None:
        raise PreflightError("pdfplumber is required for smoke-test. Use a Python environment that has pdfplumber installed.")
    try:
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise PreflightError("reportlab is required for smoke-test. Use a Python environment that has reportlab installed.") from exc

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pdf_path = root / "smoke-statement.pdf"
        out_path = root / "smoke-preflight.json"
        csv_path = root / "smoke-preflight-review.csv"

        doc = canvas.Canvas(str(pdf_path))
        doc.drawString(72, 740, "Example Bank Monthly Statement")
        doc.drawString(72, 720, "Account 12345678")
        doc.drawString(72, 700, "Statement period January 1 2025 to January 31 2025")
        doc.drawString(72, 680, "Currency EUR")
        doc.drawString(72, 660, "Closing balance 100.00 EUR")
        doc.save()

        data = build_preflight(load_pdf_files([str(pdf_path)]), 2025, "one-account", out_path, csv_path)
        write_json(out_path, data)
        write_review_csv(csv_path, data)

        if data["status"] != "ready-for-domain-extraction":
            failures.append(f"status: expected ready, got {data['status']}")
        if data["currency"]["code"] != "EUR":  # type: ignore[index]
            failures.append(f"currency: expected EUR, got {data['currency']}")
        if data["account_hints"] != ["12345678"]:  # type: ignore[index]
            failures.append(f"account: expected account hint 12345678, got {data['account_hints']}")
        if not out_path.exists():
            failures.append("json: expected smoke JSON to be written")
        if not csv_path.exists():
            failures.append("csv: expected smoke review CSV to be written")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Smoke-test passed: real PDF preflight path")
    return 0


def synthetic_file(name: str, text: str, warnings: list[str] | None = None, is_pdf: bool = True) -> dict[str, object]:
    return file_profile(Path(name), [{"page": 1, "text": text}], warnings or [], is_pdf=is_pdf)


def command_self_test(_args: argparse.Namespace) -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clean = build_preflight(
            [
                synthetic_file(
                    "clean.pdf",
                    "Example Bank\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD\nClosing balance 100.00",
                )
            ],
            2025,
            "one-account",
            root / "clean.json",
            root / "clean-review.csv",
        )
        if clean["status"] != "ready-for-domain-extraction":
            failures.append(f"clean-status: expected ready, got {clean['status']}")
        if clean["currency"]["code"] != "USD":  # type: ignore[index]
            failures.append(f"clean-currency: expected USD, got {clean['currency']}")
        if clean["account_hints"] != ["12345678"]:  # type: ignore[index]
            failures.append(f"clean-account: expected account hint 12345678, got {clean['account_hints']}")

        mixed_year = build_preflight(
            [synthetic_file("mixed-year.pdf", "Example Bank\nAccount 12345678\nStatement period December 2024 to January 2025\nCurrency USD")],
            2025,
            "one-account",
            root / "mixed-year.json",
            root / "mixed-year-review.csv",
        )
        if not any(gate.get("code") == "mixed-years" for gate in mixed_year["review_gates"]):  # type: ignore[index]
            failures.append("mixed-year: expected mixed-years gate")

        mixed_currency = build_preflight(
            [synthetic_file("mixed-currency.pdf", "Example Bank\nAccount 12345678\nStatement period January 2025\nCurrency USD\nCurrency COP")],
            2025,
            "one-account",
            root / "mixed-currency.json",
            root / "mixed-currency-review.csv",
        )
        if not any(gate.get("code") == "mixed-currencies" for gate in mixed_currency["review_gates"]):  # type: ignore[index]
            failures.append("mixed-currency: expected mixed-currencies gate")

        dollar = build_preflight(
            [synthetic_file("dollar.pdf", "Example Bank\nAccount 12345678\nStatement period January 2025\nClosing balance $100.00")],
            2025,
            "one-account",
            root / "dollar.json",
            root / "dollar-review.csv",
        )
        if not any(gate.get("code") == "ambiguous-dollar" for gate in dollar["review_gates"]):  # type: ignore[index]
            failures.append("dollar: expected ambiguous-dollar gate")

        # Marketing prose that merely contains a code word ("TRY") must not be
        # confirmed as a currency: the account stays single-currency USD, and the
        # prose token is surfaced only as a weak candidate.
        marketing = build_preflight(
            [
                synthetic_file(
                    "marketing.pdf",
                    "Example Bank\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD\nClosing balance 100.00 USD\nPLEASE TRY OUR NEW MOBILE APP TODAY",
                )
            ],
            2025,
            "one-account",
            root / "marketing.json",
            root / "marketing-review.csv",
        )
        if marketing["currency"]["code"] != "USD":  # type: ignore[index]
            failures.append(f"marketing: expected USD, got {marketing['currency']}")
        if any(gate.get("code") == "mixed-currencies" for gate in marketing["review_gates"]):  # type: ignore[index]
            failures.append("marketing: all-caps prose 'TRY' must not trip mixed-currencies")
        if "TRY" not in marketing["currency"]["weak_candidates"]:  # type: ignore[index]
            failures.append(f"marketing: expected TRY as a weak candidate, got {marketing['currency']}")

        # An amount adjacent to a code confirms it even with no 'currency' label.
        adjacency = build_preflight(
            [synthetic_file("adjacency.pdf", "Example Bank\nAccount 12345678\nStatement period January 2025\nEnding balance 1,234.56 GBP")],
            2025,
            "one-account",
            root / "adjacency.json",
            root / "adjacency-review.csv",
        )
        if adjacency["currency"]["code"] != "GBP":  # type: ignore[index]
            failures.append(f"adjacency: expected GBP from amount adjacency, got {adjacency['currency']}")

        # A code next to a bare year or a clock fragment is not adjacent to an
        # amount and must not confirm; a real monetary figure still does.
        if detect_currency(["Currency USD", "Closing balance 100.00 USD", "PLEASE TRY 24/7 ONLINE BANKING"])["code"] != "USD":
            failures.append("currency-amount: 'TRY 24/7' must not confirm a second currency")
        if detect_currency(["Currency USD", "IN 2025 TRY OUR NEW APP"])["code"] != "USD":
            failures.append("currency-amount: a bare year beside 'TRY' must not confirm it")
        if detect_currency(["Ending balance 9.999,99 GBP"])["code"] != "GBP":
            failures.append("currency-amount: a monetary figure adjacent to a code must still confirm")

        # A label confirms only a nearby code; distant disclosure boilerplate
        # ("conversion fees ... in COP and MXN") stays weak, not confirmed.
        disclosure = detect_currency(
            ["Currency USD", "Closing balance 1,234.56 USD", "conversion fees apply to transactions in COP and MXN"]
        )
        if disclosure["code"] != "USD" or "COP" not in disclosure["weak_candidates"]:  # type: ignore[index]
            failures.append(f"currency-label: distant boilerplate codes must stay weak, got {disclosure}")
        # 'US$' notation resolves to USD instead of ambiguous-dollar.
        us_dollar = detect_currency(["Saldo final US$ 1,234.56"])
        if us_dollar["code"] != "USD" or us_dollar["ambiguous_dollar"]:  # type: ignore[index]
            failures.append(f"currency-symbol: 'US$' must resolve to USD, got {us_dollar}")
        # A code glued to its amount is still recognized.
        if detect_currency(["Ending balance EUR1.234,56"])["code"] != "EUR":
            failures.append("currency-glued: a code glued to its amount (EUR1.234,56) must confirm")

        # --- Round-7 currency hardening (F1/F2/F3) ---
        # F1a: USD has no bare substring alias, so it can no longer confirm out of
        # thin air. A 'BUSD-STAKE' token (or any incidental "usd" substring) must
        # NOT become a confirmed currency; USD still confirms via label or amount.
        if "USD" in detect_currency(["Reference BUSD-STAKE-2210"])["candidates"]:  # type: ignore[index]
            failures.append("currency-usd-alias: a 'usd' substring must not confirm USD")
        if detect_currency(["Currency USD"])["code"] != "USD":  # type: ignore[index]
            failures.append("currency-usd-label: a labeled 'Currency USD' must still confirm")
        if detect_currency(["Closing balance 100.00 USD"])["code"] != "USD":  # type: ignore[index]
            failures.append("currency-usd-amount: 'USD' beside an amount must still confirm")
        # F1b: the German currency label confirms the code beside it, so a German
        # statement labeled only in its own language is not left unknown.
        if detect_currency([clean_line("Währung: CHF")])["code"] != "CHF":  # type: ignore[index]
            failures.append("currency-label-de: 'Währung: CHF' must confirm CHF")
        # Round-8 A: "devise" and "valuta" are deliberately NOT currency labels --
        # "devise" collides with the English verb ("devise a EUR plan") and
        # "valuta" means "value date" in German/Nordic banking ("Valuta 15.01.2025
        # USD"). Neither may confirm a code; French/Dutch statements confirm via €
        # or amount adjacency instead.
        if "EUR" in detect_currency(["We can devise a EUR plan for you"])["candidates"]:  # type: ignore[index]
            failures.append("currency-devise-verb: the English verb 'devise' must not confirm a currency")
        if "USD" in detect_currency(["Valuta 15.01.2025 USD", "Saldo EUR 1.234,56"])["candidates"]:  # type: ignore[index]
            failures.append("currency-valuta-valuedate: a 'Valuta <date>' value-date line must not confirm USD")
        # F2: a negative amount keeps code-adjacency -- accounting parens and the
        # European trailing minus both sit between figure and code.
        if detect_currency(["Service charge (1.234,56) EUR"])["code"] != "EUR":  # type: ignore[index]
            failures.append("currency-neg-paren: '(1.234,56) EUR' must confirm EUR")
        if detect_currency(["Fee 1.234,56- EUR"])["code"] != "EUR":  # type: ignore[index]
            failures.append("currency-neg-minus: '1.234,56- EUR' must confirm EUR")
        # F2 guard: the trailing sign only extends a real figure. A bare "5)" or a
        # year range "2020-2024" is not an amount, so it must not confirm a code.
        if "USD" in detect_currency(["(see note 5) USD"])["candidates"]:  # type: ignore[index]
            failures.append("currency-neg-guard: '(see note 5)' must not confirm USD")
        if "EUR" in detect_currency(["Period 2020-2024 EUR"])["candidates"]:  # type: ignore[index]
            failures.append("currency-neg-guard: a year range must not confirm EUR")
        # F3: a footnote superscript on a code is stripped before NFKC, so "EUR¹"
        # stays "EUR" (not "EUR1") and still confirms beside its amount.
        if not clean_line("Closing balance 100.00 EUR¹").endswith("EUR"):
            failures.append("currency-footnote: a superscript marker must be stripped from a code")
        if detect_currency([clean_line("Closing balance 1.234,56 EUR¹")])["code"] != "EUR":  # type: ignore[index]
            failures.append("currency-footnote: a footnoted 'EUR¹' beside an amount must confirm EUR")

        # --- Round-7 coverage (F4/F5/F6), corrected by round 8 ---
        # F4/B: beside a DUAL-USE marker a year is kept only with NUMERIC date
        # context ("since 2025-04-01"); a bare or month-only year is suppressed,
        # independent of the month name's length. A HARD copyright marker
        # suppresses its year even through a month or a range.
        if 2025 not in detect_years(["Portfolio value since 2025-04-01"]):
            failures.append("year-iso-head: numeric 'since 2025-04-01' must keep the year")
        march = detect_years(["Interest earned since March 2025"])
        january = detect_years(["Interest earned since January 2025"])
        if (2025 in march) or (2025 in january) or (march != january):
            failures.append(f"year-month-since: month-only 'since <Month> 2025' must be suppressed and month-length independent, got march={march} january={january}")
        if 1904 in detect_years(["Serving our community since 1904"]):
            failures.append("year-heritage: bare 'since 1904' must stay suppressed")
        for leak, line in [
            (2019, "© May 2019 Example Bank. All rights reserved."),
            (2020, "Copyright January 2020 Example Bancorp"),
            (2024, "© 2019-2024 Example Bank"),
        ]:
            if leak in detect_years([line]):
                failures.append(f"year-hard-copyright: {leak} must stay suppressed in {line!r}")
        if detect_years(["Comparative 2024-2025 summary"]) != [2024, 2025]:
            failures.append("year-range: a bare year range (no marker) must keep both years")
        # F5: a one-token brand ending in "bank" (with banking context on the
        # line) is recognized, as is a German savings bank; common -bank nouns and
        # place-names are not institutions.
        if not line_names_institution("Commerzbank Kontoauszug"):
            failures.append("inst-suffix: 'Commerzbank' must be recognized as an institution")
        if not line_names_institution("Rabobank Account Statement"):
            failures.append("inst-suffix: 'Rabobank' must be recognized as an institution")
        if not line_names_institution("Sparkasse Berlin"):
            failures.append("inst-sparkasse: 'Sparkasse' must be recognized as an institution")
        if line_names_institution("Our riverbank picnic area is open"):
            failures.append("inst-guard: 'riverbank' must not be an institution")
        if line_names_institution("See our nonbank lender disclosure"):
            failures.append("inst-guard: 'nonbank' must not be an institution")
        # Round-8 C: the -bank suffix needs banking context, so common -bank nouns
        # in prose are not institutions even when capitalized.
        for noun in ["Local Foodbank donation drive", "snowbank cleared from the lot", "Community Bloodbank notice"]:
            if line_names_institution(noun):
                failures.append(f"inst-bank-noun: {noun!r} must not be read as an institution")
        # F6: German/French bare account labels yield the account number.
        if detect_account_hints(["Konto 12345678"]) != ["12345678"]:
            failures.append("acct-konto: German 'Konto 12345678' must yield the account")
        if "12345678" not in detect_account_hints(["Kontonummer: 12345678"]):
            failures.append("acct-kontonummer: 'Kontonummer: 12345678' must yield the account")
        if "12345678" not in detect_account_hints(["Compte 12345678"]):
            failures.append("acct-compte: French 'Compte 12345678' must yield the account")
        # F5+F6+F1b end-to-end: a German statement resolves institution, account,
        # and currency, and draws no unknown-institution / unknown-account gate.
        de_stmt = build_preflight(
            [
                synthetic_file(
                    "kontoauszug.pdf",
                    "Commerzbank Kontoauszug\nKonto 12345678\nStatement period January 1 2025 to January 31 2025\nWährung EUR\nSaldo 1.234,56",
                )
            ],
            2025,
            "one-institution",
            root / "kontoauszug.json",
            root / "kontoauszug-review.csv",
        )
        de_gates = {gate.get("code") for gate in de_stmt["review_gates"]}  # type: ignore[index]
        if de_stmt["account_hints"] != ["12345678"]:  # type: ignore[index]
            failures.append(f"de-e2e: expected account ['12345678'], got {de_stmt['account_hints']}")
        if not de_stmt["institution_hints"] or "unknown-institution" in de_gates:  # type: ignore[index]
            failures.append(f"de-e2e: expected a German institution hint, got {de_stmt['institution_hints']} gates={de_gates}")

        accounts = build_preflight(
            [synthetic_file("accounts.pdf", "Example Bank\nAccount 11112222\nAccount 33334444\nStatement period January 2025\nCurrency USD")],
            2025,
            "one-account",
            root / "accounts.json",
            root / "accounts-review.csv",
        )
        if not any(gate.get("code") == "possible-mixed-accounts" for gate in accounts["review_gates"]):  # type: ignore[index]
            failures.append("accounts: expected possible-mixed-accounts gate")

        # A realistic single-account statement: only the real number is a hint.
        # "Account Summary" and the holder name must not be captured (they used
        # to trip a false possible-mixed-accounts gate and leak PII downstream).
        one_account = build_preflight(
            [
                synthetic_file(
                    "one-account.pdf",
                    "Example Bank\nMonthly Account Statement\nAccount Summary\nAccount holder JUAN PEREZ GARCIA\nAccount ID 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD",
                )
            ],
            2025,
            "one-account",
            root / "one-account.json",
            root / "one-account-review.csv",
        )
        if one_account["account_hints"] != ["12345678"]:  # type: ignore[index]
            failures.append(f"one-account: expected only ['12345678'], got {one_account['account_hints']}")
        if any(gate.get("code") == "possible-mixed-accounts" for gate in one_account["review_gates"]):  # type: ignore[index]
            failures.append("one-account: a single account must not trip possible-mixed-accounts")

        low_text = build_preflight(
            [synthetic_file("scan.pdf", "", [], is_pdf=True)],
            2025,
            "one-account",
            root / "scan.json",
            root / "scan-review.csv",
        )
        if not any(gate.get("code") == "low-text-pdf" for gate in low_text["review_gates"]):  # type: ignore[index]
            failures.append("low-text: expected low-text-pdf gate")

        # Two banks where the first statement is long enough (>80 lines) to have
        # hidden the second bank from the old combined-line scan.
        long_alpha = "Alpha Bank N.A.\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD\n" + "\n".join(
            f"01/{(day % 28) + 1:02d}/2025 card purchase ref {day:04d} 10.00 balance 90.00" for day in range(1, 90)
        )
        mixed_institutions = build_preflight(
            [
                synthetic_file("alpha.pdf", long_alpha),
                synthetic_file("beta.pdf", "Beta Banco S.A.\nAccount 12345678\nStatement period February 1 2025 to February 28 2025\nCurrency USD"),
            ],
            2025,
            "one-institution",
            root / "mixed-institutions.json",
            root / "mixed-institutions-review.csv",
        )
        if not any(gate.get("code") == "possible-mixed-institutions" for gate in mixed_institutions["review_gates"]):  # type: ignore[index]
            failures.append("mixed-institutions: expected possible-mixed-institutions gate across files")

        # The same bank across two months must not read as two institutions.
        same_institution = build_preflight(
            [
                synthetic_file("jan.pdf", "Example Bank Monthly Statement January 2025\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD"),
                synthetic_file("feb.pdf", "Example Bank Monthly Statement February 2025\nAccount 12345678\nStatement period February 1 2025 to February 28 2025\nCurrency USD"),
            ],
            2025,
            "one-institution",
            root / "same-institution.json",
            root / "same-institution-review.csv",
        )
        if any(gate.get("code") == "possible-mixed-institutions" for gate in same_institution["review_gates"]):  # type: ignore[index]
            failures.append("same-institution: same bank across months must not trip possible-mixed-institutions")

        # Common words that merely contain a term substring are not institutions.
        substring_noise = build_preflight(
            [
                synthetic_file(
                    "substring.pdf",
                    "Example Bank Monthly Statement\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD\nOtherwise please contact the branch\nWe value your trusted partnership likewise",
                )
            ],
            2025,
            "one-institution",
            root / "substring.json",
            root / "substring-review.csv",
        )
        substring_hints = substring_noise["institution_hints"]  # type: ignore[index]
        if any("Otherwise" in hint or "likewise" in hint for hint in substring_hints):
            failures.append(f"substring: word-boundary match should exclude Otherwise/likewise, got {substring_hints}")
        if any(gate.get("code") == "possible-mixed-institutions" for gate in substring_noise["review_gates"]):  # type: ignore[index]
            failures.append("substring: single real institution must not trip possible-mixed-institutions")

        # A fintech header ("Wise Account Statement") must still be recognized as
        # an institution even though the line also says "Statement".
        fintech = build_preflight(
            [synthetic_file("wise.pdf", "Wise Account Statement\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency EUR\nClosing balance 100.00 EUR")],
            2025,
            "one-institution",
            root / "wise.json",
            root / "wise-review.csv",
        )
        if not fintech["institution_hints"]:  # type: ignore[index]
            failures.append("fintech: expected an institution hint from a 'Wise Account Statement' header")
        if any(gate.get("code") == "unknown-institution" for gate in fintech["review_gates"]):  # type: ignore[index]
            failures.append("fintech: 'Wise Account Statement' should satisfy the institution check")

        # Initial-based bank names must stay distinct instead of collapsing to
        # their shared word, and legal-suffix drift on one bank must not split it.
        if institution_signature("U.S. Bank") == institution_signature("M&T Bank"):
            failures.append("institution-signature: 'U.S. Bank' and 'M&T Bank' must not collide")
        if institution_signature("Example Bank, N.A.") != institution_signature("Example Bank NA"):
            failures.append("institution-signature: 'N.A.' and 'NA' must match")
        if institution_signature("Example Bank N.A.") != institution_signature("Example Bank"):
            failures.append("institution-signature: a legal suffix must not split the same bank")
        # Latin-American entity forms must not split the same institution.
        if institution_signature("Banco Ejemplo S.A. de C.V.") != institution_signature("Banco Ejemplo"):
            failures.append("institution-signature: 'S.A. de C.V.' must not split the same bank")
        if institution_signature("Empresa Grande S.A.P.I. de C.V.") != institution_signature("Empresa Grande"):
            failures.append("institution-signature: 'S.A.P.I. de C.V.' must not split the same institution")
        # A customer street address is not an institution hint (it used to become
        # the file's masthead signature and trip a false mixed gate).
        address_hints = detect_institution_hints(["12 Bank Street", "Example Bank N.A."])  # privacy-gate: allow (synthetic address)
        if address_hints != ["Example Bank N.A."]:
            failures.append(f"address: a street address must not be an institution hint, got {address_hints}")

        # A one-token brand ("Bancolombia") heads a name the word-boundary match
        # misses; the stem catches it, but a marketing line sharing the stem
        # ("Personal banking made easy") must not become an institution hint.
        if not detect_institution_hints(["Bancolombia S.A."]):
            failures.append("institution-stem: 'Bancolombia S.A.' must be recognized as an institution")
        if not line_names_institution("Bankinter"):
            failures.append("institution-stem: 'Bankinter' must be recognized as an institution")
        if line_names_institution("Personal banking made easy"):
            failures.append("institution-stem: a 'banking' marketing line must not name an institution")
        if line_names_institution("Please avoid bankruptcy fees"):
            failures.append("institution-stem: 'bankruptcy' must not name an institution")
        # "banking" is a stopword on its own, but a corporate entity name led by
        # a proper noun ("First Banking Corporation", "Lloyds Banking Group") is
        # a real institution.
        if not line_names_institution("First Banking Corporation"):
            failures.append("institution-stem: 'Banking Corporation' entity name must name an institution")
        if not line_names_institution("Lloyds Banking Group"):
            failures.append("institution-stem: 'Lloyds Banking Group' must name an institution")
        if not detect_institution_hints(["First Banking Corporation"]):
            failures.append("institution-stem: a 'Banking Corporation' header must yield an institution hint")
        if line_names_institution("Enjoy online banking anywhere"):
            failures.append("institution-stem: bare 'online banking' marketing must stay excluded")
        # A lowercase function word before the entity phrase marks marketing
        # prose ("our banking group rewards"), not a name; it must stay excluded.
        if line_names_institution("Join our banking group rewards today and save"):
            failures.append("institution-stem: a lowercase-led 'banking group' marketing line must stay excluded")

        # Accent drift between two text layers must not split one bank, and a
        # ligature in the extracted text must not hide an institution term.
        if institution_signature("Banco Bogotá Ejemplo") != institution_signature("Banco Bogota Ejemplo"):
            failures.append("unicode: accented and unaccented spellings of one bank must share a signature")
        if clean_line("Example ﬁnancial Group") != "Example financial Group":
            failures.append("unicode: an NFKC ligature must fold to ASCII at ingestion")
        if not detect_institution_hints([clean_line("Example ﬁnancial Group Statement")]):
            failures.append("unicode: a ligatured 'financial' must still name an institution after NFKC")

        short_name_banks = build_preflight(
            [
                synthetic_file("usbank.pdf", "U.S. Bank\nStatement period January 1 2025 to January 31 2025\nCurrency USD\nClosing balance 100.00 USD"),
                synthetic_file("mtbank.pdf", "M&T Bank\nStatement period February 1 2025 to February 28 2025\nCurrency USD\nClosing balance 200.00 USD"),
            ],
            2025,
            "one-institution",
            root / "short-banks.json",
            root / "short-banks-review.csv",
        )
        if not any(gate.get("code") == "possible-mixed-institutions" for gate in short_name_banks["review_gates"]):  # type: ignore[index]
            failures.append("short-name-banks: 'U.S. Bank' vs 'M&T Bank' must trip possible-mixed-institutions")

        suffix_drift = build_preflight(
            [
                synthetic_file("drift-a.pdf", "EXAMPLE BANK, N.A.\nStatement period January 1 2025 to January 31 2025\nCurrency USD"),
                synthetic_file("drift-b.pdf", "Example Bank NA - Monthly Statement\nStatement period February 1 2025 to February 28 2025\nCurrency USD"),
            ],
            2025,
            "one-institution",
            root / "drift.json",
            root / "drift-review.csv",
        )
        if any(gate.get("code") == "possible-mixed-institutions" for gate in suffix_drift["review_gates"]):  # type: ignore[index]
            failures.append("suffix-drift: same bank with and without 'N.A.' must not trip possible-mixed-institutions")

        # Dot-terminated account labels ("No.", "Nro.", "Núm.") must capture, not
        # silently miss. Fixtures avoid the "account no NNNN" spelling that the
        # repo privacy scan flags, while still exercising every label branch.
        account_label_forms = {
            "Account No. 12 34 56": ["12 34 56"],
            "Cuenta Nro. 4567890": ["4567890"],
            "Cuenta Núm. 4567890": ["4567890"],
            "Account ID 12345678": ["12345678"],
            "Account 12345678": ["12345678"],
        }
        for line, expected in account_label_forms.items():
            got = detect_account_hints([line])
            if got != expected:
                failures.append(f"account-label {line!r}: expected {expected}, got {got}")
        if detect_account_hints(["Account Nro. 11112222", "Account 33334444"]) != ["11112222", "33334444"]:
            failures.append("account-label: a dot-label account plus a bare account must yield two hints")
        if detect_account_hints(["Account No. Statement of activity"]):
            failures.append("account-label: a label followed by a word must not yield a hint")
        if detect_account_hints(["Account No. 12 of 34 pages"]):
            failures.append("account-label: a short number embedded in text after a label must not be captured as an account")

        # IBAN captures trim a trailing holder name to the checksum-valid IBAN,
        # and grouped vs compact spellings collapse to one hint.
        grouped_iban = "GB82 WEST 1234 5698 7654 32"  # privacy-gate: allow (public documentation IBAN, synthetic test value)
        compact_iban = grouped_iban.replace(" ", "")
        if iban_token(grouped_iban + " HOLDER JANE DOE") != compact_iban:
            failures.append("iban: expected the trailing name trimmed off the IBAN")
        if iban_token(compact_iban) != compact_iban:
            failures.append("iban: a valid compact IBAN should validate")
        if iban_token("GB00 0000 0000 0000 0000 00") is not None:  # privacy-gate: allow (synthetic invalid IBAN)
            failures.append("iban: an invalid checksum must be rejected")

        # A header IBAN is the holder's and is captured; the same IBAN on a
        # SEPA/transfer line is the counterparty's and must be skipped.
        if not detect_account_hints(["IBAN GB82 WEST 1234 5698 7654 32"]):  # privacy-gate: allow (synthetic IBAN)
            failures.append("counterparty: a header IBAN must still be captured")
        if detect_account_hints(["SEPA transfer to IBAN GB82 WEST 1234 5698 7654 32 rent 850.00 EUR"]):  # privacy-gate: allow (synthetic IBAN)
            failures.append("counterparty: an IBAN on a SEPA/transfer line must not be harvested as the holder's account")

        # A plural label with a conjunction surfaces every account, not just the
        # first (the Spanish 'Nros.'/'y' form is privacy-clean).
        if detect_account_hints(["Cuenta Nros. 11112222 y 33334444"]) != ["11112222", "33334444"]:
            failures.append("plural-label: a plural label joined by a conjunction must yield every account")

        # One account printed in several forms is one account, not a mixed set:
        # spaced header vs compact footer, masked header vs "ending in" footer,
        # and a grouped IBAN under a label vs its compact spelling under IBAN.
        # Two genuinely different full numbers must still surface as two.
        account_alias_forms = {
            "spaced+compact": (["Account No. 5555 6666", "questions about account 55556666"], ["5555 6666"]),  # privacy-gate: allow (synthetic account fixture)
            "masked+ending": (["Account No. ****6666", "your account ending in 6666"], ["****6666"]),  # privacy-gate: allow (synthetic account fixture)
            "iban-label+keyword": (
                ["Account No: DE89 3704 0044 0532 0130 00", "IBAN: DE89370400440532013000"],  # privacy-gate: allow (synthetic IBAN)
                ["DE89 3704 0044 0532 0130 00"],
            ),
        }
        for label, (alias_lines, expected) in account_alias_forms.items():
            got = detect_account_hints(alias_lines)
            if got != expected:
                failures.append(f"account-alias {label}: expected {expected}, got {got}")
        if _same_account("55556666", False, "12346666", False):
            failures.append("account-alias: two distinct full numbers sharing a suffix must not merge")
        if not _same_account("****6666", True, "6666", True):
            failures.append("account-alias: a masked token and its 'ending in' suffix must merge")
        # Documented tradeoff: a masked token DOES absorb into a full number
        # sharing its revealed digits, because on a real statement the mask is
        # almost always the same account shown redacted. This can hide the rare
        # case of two different accounts sharing a last-4 when one is masked; the
        # alternative (never merging masked->full) re-opens a false mixed gate on
        # every statement that prints its own number masked, which is far more
        # common. Kept intentional here so a future edit does not flip it blindly.
        if not _same_account("****6666", True, "12346666", False):
            failures.append("account-alias: a masked token is intentionally absorbed by a full number sharing its tail")

        aliased = build_preflight(
            [
                synthetic_file(
                    "aliased.pdf",
                    "Example Bank Monthly Statement\nAccount No. 5555 6666\nStatement period January 1 2025 to January 31 2025\nCurrency USD\nQuestions about account 55556666 call us anytime",
                )
            ],
            2025,
            "one-account",
            root / "aliased.json",
            root / "aliased-review.csv",
        )
        if any(gate.get("code") == "possible-mixed-accounts" for gate in aliased["review_gates"]):  # type: ignore[index]
            failures.append("account-alias: one account in two printed forms must not trip possible-mixed-accounts")

        # A copyright footer or heritage tagline year describes the institution,
        # not the period; it must not trip mixed-years on an otherwise-clean file.
        if detect_years(["(c) 2019 Example Bancorp. All rights reserved."]) != []:
            failures.append("boilerplate-year: a copyright footer year must not count as a statement year")
        if detect_years(["Serving customers since 1904"]) != []:
            failures.append("boilerplate-year: a heritage 'since 1904' year must not count as a statement year")
        if detect_years(["Statement period January 1 2025 to January 31 2025"]) != [2025]:
            failures.append("boilerplate-year: a real period year must still be detected")
        # "since" is dual-use: heritage ("since 1904") suppresses, but temporal
        # ("since 01.01.2025", a date tail) must keep the real period year.
        if detect_years(["Account activity since 01.01.2025"]) != [2025]:
            failures.append("boilerplate-year: a date-form year after 'since' must be kept, not suppressed")
        if detect_years(["Serving customers since 1904"]) != []:
            failures.append("boilerplate-year: a bare heritage 'since 1904' must still be suppressed")
        # Suppression is per-token: a real out-of-year period must still gate
        # even when the same year also appears in a footer.
        if 2024 not in detect_years(["Statement period March 1 2024 to March 31 2024", "(c) 2024 Example Bancorp."]):
            failures.append("boilerplate-year: a real period year must survive a same-year footer")
        boilerplate = build_preflight(
            [
                synthetic_file(
                    "boilerplate.pdf",
                    "Example Bank Monthly Statement\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD\nClosing balance 1,234.56 USD\n(c) 2019 Example Bancorp. All rights reserved. Member FDIC.",
                )
            ],
            2025,
            "one-account",
            root / "boilerplate.json",
            root / "boilerplate-review.csv",
        )
        if any(gate.get("code") == "mixed-years" for gate in boilerplate["review_gates"]):  # type: ignore[index]
            failures.append("boilerplate-year: a copyright-footer year must not trip mixed-years")
        if boilerplate["status"] != "ready-for-domain-extraction":  # type: ignore[index]
            failures.append(f"boilerplate-year: a clean statement with a footer year must stay ready, got {boilerplate['status']}")

        # The same statement supplied twice is flagged, not silently double-counted.
        duplicate = build_preflight(
            [
                synthetic_file("dup.pdf", "Example Bank\nAccount 12345678\nStatement period January 2025\nCurrency USD"),
                synthetic_file("dup.pdf", "Example Bank\nAccount 12345678\nStatement period January 2025\nCurrency USD"),
            ],
            2025,
            "one-account",
            root / "dup.json",
            root / "dup-review.csv",
        )
        if not any(gate.get("code") == "duplicate-input" for gate in duplicate["review_gates"]):  # type: ignore[index]
            failures.append("duplicate: expected duplicate-input gate")

        # A byte-for-byte copy under a different name shares a content digest and
        # trips duplicate-content, which the path check alone cannot see.
        probe = root / "hashme.bin"
        probe.write_bytes(b"example statement bytes")
        if file_sha256(probe) != hashlib.sha256(b"example statement bytes").hexdigest():
            failures.append("file-sha256: digest must match hashlib over the same bytes")
        if file_sha256(root / "does-not-exist.bin") is not None:
            failures.append("file-sha256: an unreadable path must return None")
        copy_a = synthetic_file("download.pdf", "Example Bank\nAccount 12345678\nStatement period January 2025\nCurrency USD")
        copy_b = synthetic_file("download (1).pdf", "Example Bank\nAccount 12345678\nStatement period January 2025\nCurrency USD")
        copy_a["content_sha256"] = copy_b["content_sha256"] = "0" * 64
        content_dup = build_preflight(
            [copy_a, copy_b], 2025, "one-account", root / "cdup.json", root / "cdup-review.csv"
        )
        if not any(gate.get("code") == "duplicate-content" for gate in content_dup["review_gates"]):  # type: ignore[index]
            failures.append("duplicate-content: byte-identical files under different names must gate")

        # Swiss apostrophe grouping ("CHF 1'234.56") reads as a monetary amount.
        if detect_currency(["Saldo CHF 1'234.56"])["code"] != "CHF":
            failures.append("currency-swiss: \"CHF 1'234.56\" must confirm CHF")
        # Prefixed dollar/sol notations resolve the ambiguous sign.
        brl = detect_currency(["Saldo final R$ 1.234,56"])
        if brl["code"] != "BRL" or brl["ambiguous_dollar"]:  # type: ignore[index]
            failures.append(f"currency-brl: 'R$' must resolve to BRL, got {brl}")
        if detect_currency(["Saldo S/. 1,234.56"])["code"] != "PEN":
            failures.append("currency-pen: 'S/.' before a monetary amount must resolve to PEN")
        if detect_currency(["Closing balance 100.00 USD"])["code"] != "USD":
            failures.append("currency-pen: a plain statement must not spuriously resolve to PEN")
        # "S/" is also serial/series shorthand; a bare integer after it is a
        # document number, not soles, and must not confirm PEN.
        serial = detect_currency(["Currency USD", "Closing balance 100.00 USD", "Reference S/ 0099887 processed"])
        if serial["code"] != "USD" or "PEN" in serial["candidates"]:  # type: ignore[index]
            failures.append(f"currency-pen: 'S/ 0099887' (a serial) must not confirm PEN, got {serial}")

        # A numeric-only period range is detected even with no month or period word.
        if not detect_periods(["Kontoauszug 01.01.2025 - 31.01.2025"]):
            failures.append("numeric-period: a dd.mm.yyyy range must be detected as a period")
        if not detect_periods(["01/01/2025 al 31/01/2025"]):
            failures.append("numeric-period: a dd/mm/yyyy 'al' range must be detected as a period")
        if detect_periods(["Ref 12/34 amount 56.00"]):
            failures.append("numeric-period: a lone fraction-like token must not read as a period range")

        # CSV cells that begin with a formula lead are neutralized.
        if csv_safe("=HYPERLINK(\"http://x\")") != "'=HYPERLINK(\"http://x\")":
            failures.append("csv_safe: expected leading '=' to be quoted")
        if csv_safe("@SUM(A1:A9)") != "'@SUM(A1:A9)":
            failures.append("csv_safe: expected leading '@' to be quoted")
        if csv_safe("Example Bank") != "Example Bank":
            failures.append("csv_safe: ordinary text must be unchanged")

        # Output paths that collide are rejected before anything is written.
        for label, out_arg, csv_arg, pdfs in (
            ("out==csv", root / "same.json", root / "same.json", []),
            ("out over input", root / "in.pdf", root / "other.csv", [str(root / "in.pdf")]),
        ):
            try:
                check_output_paths(out_arg, csv_arg, [str(p) for p in pdfs])
                failures.append(f"paths: expected {label} collision to be rejected")
            except PreflightError:
                pass

        # A filesystem error on write (e.g. --out beneath an existing file)
        # degrades to a clean PreflightError, not a raw traceback.
        blocker_file = root / "blocker-file"
        blocker_file.write_text("i am a file, not a directory")
        try:
            write_json(blocker_file / "out.json", {"x": 1})
            failures.append("write: expected a filesystem error to become PreflightError")
        except PreflightError:
            pass

        # The tax year is bounded at both layers.
        try:
            _year_arg("20025")
            failures.append("year: expected _year_arg to reject 20025")
        except argparse.ArgumentTypeError:
            pass
        try:
            build_preflight([synthetic_file("y.pdf", "Example Bank\nCurrency USD")], 3000, "one-account", root / "y.json", root / "y-review.csv")
            failures.append("year: expected build_preflight to reject year 3000")
        except PreflightError:
            pass

        # Exit code stays 0 by default; opt-in flag makes review-required nonzero.
        if review_exit_code("review-required", False) != 0:
            failures.append("exit-code: default must stay 0 on review-required")
        if review_exit_code("review-required", True) != REVIEW_EXIT_CODE:
            failures.append(f"exit-code: opt-in must return {REVIEW_EXIT_CODE} on review-required")
        if review_exit_code("ready-for-domain-extraction", True) != 0:
            failures.append("exit-code: a ready result must exit 0 even with the opt-in flag")

        write_json(root / "clean.json", clean)
        write_review_csv(root / "clean-review.csv", clean)
        if not (root / "clean-review.csv").exists():
            failures.append("review-csv: expected review CSV to be written")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Self-test passed: 42 preflight cases")
    return 0


def _year_arg(raw: str) -> int:
    try:
        year = int(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"year {raw!r} is not an integer") from None
    if not (MIN_TAX_YEAR <= year <= MAX_TAX_YEAR):
        raise argparse.ArgumentTypeError(f"year {year} is outside the supported range {MIN_TAX_YEAR}-{MAX_TAX_YEAR}")
    return year


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Preflight one statement PDF set.")
    preflight.add_argument("--pdf", nargs="+", required=True, help="Statement PDFs for one tax year and one scope.")
    preflight.add_argument("--tax-year", type=_year_arg, required=True, help=f"Calendar/tax year to verify ({MIN_TAX_YEAR}-{MAX_TAX_YEAR}).")
    preflight.add_argument("--scope", choices=sorted(SUPPORTED_SCOPES), required=True, help="Expected downstream scope.")
    preflight.add_argument("--out", required=True, help="Output preflight JSON path.")
    preflight.add_argument("--csv", help="Optional review CSV path.")
    preflight.add_argument(
        "--exit-nonzero-on-review",
        action="store_true",
        help=f"Exit {REVIEW_EXIT_CODE} (instead of 0) when the result is review-required, for scripted callers.",
    )
    preflight.set_defaults(func=command_preflight)

    dependency = subparsers.add_parser("dependency-check", help="Check extraction dependency availability.")
    dependency.set_defaults(func=command_dependency_check)

    smoke = subparsers.add_parser("smoke-test", help="Run a real PDF extraction smoke test.")
    smoke.set_defaults(func=command_smoke_test)

    self_test = subparsers.add_parser("self-test", help="Run deterministic preflight tests.")
    self_test.set_defaults(func=command_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
