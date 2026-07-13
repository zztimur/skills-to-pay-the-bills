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
from datetime import UTC, date, datetime, timedelta
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
SCHEMA_VERSION = "1.1"
PREFLIGHT_SKILL = "statement-intake-preflight"
READY_STATUS = "ready-for-domain-extraction"
REVIEW_REQUIRED_STATUS = "review-required"
REVIEWED_HANDOFF_STATUS = "reviewed-for-domain-extraction"
REVIEWED_HANDOFF_TYPE = "reviewed-handoff"
OUT_OF_PERIOD_GENERATED_DATE_GATE = "out-of-period-generated-date"
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
# Some Spanish statement headers split the product and identifier across two
# lines (for example, ``CUENTA DE AHORROS`` then ``NÚMERO 12345678``).  Keep
# this narrow: a bare "NÚMERO" elsewhere is too easily a page, transaction,
# phone, branch, or address number.
STANDALONE_ACCOUNT_NUMBER_RE = re.compile(
    r"^\s*(?:n[uú]mero|nro|n[uú]m)\.?\s*[:#-]?\s*([*Xx0-9][*Xx0-9.\- ]{3,33})\s*$",
    re.I,
)
ACCOUNT_PRODUCT_HEADER_RE = re.compile(r"^\s*cuenta(?:\s+de\s+(?:ahorros|corriente))?\s*$", re.I)
ACCOUNT_HEADER_CONTEXT_LINES = 3
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
# A transaction description such as "Administración de Cuenta 12345678" is an
# account-administration fee plus a movement/reference identifier, not a holder
# account label. Keep this deliberately narrow and apply it only to the bare
# account-label matcher below: an explicit "Account ID" or "Cuenta Número"
# label on the same line remains usable evidence.
ACCOUNT_ADMINISTRATION_TRANSACTION_RE = re.compile(
    r"\b(?:administraci[oó]n|mantenimiento|manejo|cargo|comisi[oó]n)\s+de\s+cuenta\b",
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

# "Banco"/"Bank" also head one-token brand names that a word-boundary match
# misses. Match them as a stem -- but only "banco"/"bank" (not the bare
# "banc" stem, which would also catch the Spanish adjective "bancario" and
# "bancarrota"), and reject the handful of common words that share the stem, so
# marketing prose ("Mobile banking made easy", "avoid bankruptcy") cannot
# become a header institution hint.
INSTITUTION_STEM_RE = re.compile(r"\b(?:banco|bank)\w+", re.I)
# An alphanumeric token alone is not issuer evidence: common page and report
# labels such as "Page1" and "Report2025" match the same shape as a brand. A
# candidate counts only when it is immediately followed by a legal-entity
# suffix. Bare brands intentionally fall back to the reviewed confirmation.
INSTITUTION_ALNUM_BRAND_LEGAL_RE = re.compile(
    r"\b(?=[A-Za-z]*\d)[A-Za-z][A-Za-z0-9]{2,}\s+"
    r"(?:a\.?g\.?|n\.?a\.?|n\.?v\.?|s\.?a\.?|s\.?p\.?a\.?|s\.?a\.?s\.?|"
    r"gmbh|plc|inc(?:orporated)?|ltd|limited|llc|llp)\b",
    re.I,
)
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
    is recognized while common stem-sharing words are excluded. An alphanumeric
    brand needs an adjacent legal-entity suffix; otherwise it is review-only
    evidence, never an automatic issuer. A stopword like "banking" can still
    head a corporate entity name led by a proper noun.
    """
    if INSTITUTION_TERM_RE.search(line):
        return True
    if INSTITUTION_ALNUM_BRAND_LEGAL_RE.search(line):
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

MONTH_ABBREVIATIONS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "ene": 1,
    "abr": 4,
    "ago": 8,
    "dic": 12,
}

MONTH_NUMBERS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    **MONTH_ABBREVIATIONS,
}

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
# the alphabetic connectors ("to", "a", "al", "bis", "hasta", "through") require it,
# so a lone hyphen elsewhere cannot bridge two unrelated numbers.
NUMERIC_PERIOD_RE = re.compile(
    r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"
    r"(?:\s*[-–—]\s*|\s+(?:to|a(?:l)?|bis|hasta|through)\s+)"
    r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}",
    re.I,
)

# Structured period evidence is deliberately narrower than the existing
# ``detect_periods`` display helper. It accepts only complete date ranges on one
# line, so a page number and extracted-line number can be retained without
# emitting raw statement text in the machine-readable handoff.
NUMERIC_DATE_RE = re.compile(
    r"\b(?P<first>\d{1,4})[./-](?P<second>\d{1,2})[./-](?P<third>\d{1,4})\b"
)
_MONTH_TOKEN = "|".join(re.escape(month) for month in sorted(MONTH_NUMBERS, key=len, reverse=True))
MONTH_TOKEN_RE = re.compile(rf"\b(?:{_MONTH_TOKEN})\.?(?!\w)", re.I)
TEXT_DATE_MONTH_FIRST_RE = re.compile(
    rf"\b(?P<month>{_MONTH_TOKEN})\.?(?:[\s-]+)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,\s*|[\s-]+)(?P<year>19\d{{2}}|20\d{{2}})\b",
    re.I,
)
TEXT_DATE_DAY_FIRST_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+de\s+|[-\s]+)(?P<month>{_MONTH_TOKEN})\.?(?:\s+de\s+|[-,\s]+)(?P<year>19\d{{2}}|20\d{{2}})\b",
    re.I,
)
# A generated-on date describes when the issuer produced the document, not the
# account activity it covers. Keep this vocabulary narrow and source-bound: a
# generic date elsewhere on a page must still follow the ordinary evidence
# path. The record is preserved for review, but it never establishes statement
# period or fallback year coverage.
DOCUMENT_GENERATED_ON_RE = re.compile(
    r"\b(?:extracto|estado)\s+de\s+cuenta\s+generad[oa]\s+el\b|"
    r"\baccount\s+(?:statement|extract)\s+generated\s+(?:on|at)\b",
    re.I,
)
# This is evaluated only between two complete parsed dates. Include the Spanish
# range connector ``a`` as well as ``al`` so a labelled ``YYYY/MM/DD a
# YYYY/MM/DD`` period becomes source-bound coverage evidence instead of a
# display-only hint.
PERIOD_CONNECTOR_RE = re.compile(r"(?:[-–—]|\b(?:to|through|a(?:l)?|hasta|bis|desde)\b)", re.I)
SPLIT_PERIOD_START_RE = re.compile(r"\b(?:desde|from)\b", re.I)
SPLIT_PERIOD_END_RE = re.compile(r"\b(?:hasta|through)\b", re.I)
SPLIT_PERIOD_MAX_LINE_DISTANCE = 2
# ``detected_periods`` is a compact review aid, not a general date search.
# Keep only labels that look like a statement-period heading; transaction
# narratives frequently mention a month and year plus their own identifiers.
PERIOD_HEADING_PREFIX_RE = re.compile(
    r"^\s*(?:statement\s+(?:period|date)|period(?:o)?|desde|from|hasta|through)\b",
    re.I,
)
MONTH_YEAR_HEADING_WORDS = {
    "account",
    "bancario",
    "bank",
    "banco",
    "cuenta",
    "de",
    "del",
    "el",
    "estado",
    "extracto",
    "extractos",
    "for",
    "la",
    "mes",
    "month",
    "monthly",
    "mensual",
    "movimientos",
    "of",
    "period",
    "periodo",
    "resumen",
    "statement",
    "statements",
    "summary",
    "the",
}

# A prior-year date belongs in evidence when the statement labels it as an
# opening/prior balance. It must not silently become a second statement period.
# Keep the vocabulary focused on balance context so transaction rows do not get
# reclassified just because they contain a prior-year date.
CONTEXTUAL_BALANCE_RE = re.compile(
    r"\b(?:opening|beginning|prior|previous)\s+balance\b|"
    r"\b(?:saldo|balance)\s+(?:inicial|anterior|previo)\b",
    re.I,
)

# Some banks print the prior December 31 as the opening boundary of a January
# (or quarterly) statement. That one-day boundary is not a second statement
# year when the period ends in the requested year. Keep the raw range and its
# source reference, but classify the prior date separately so a genuine 2024
# statement period remains a non-overridable mixed-years gate.


# A three-letter ISO token is only trusted as a currency when it is corroborated:
# either its line carries a currency-label word, sits directly beside an amount,
# or appears in an explicit account-movements currency header. This keeps
# all-caps prose ("PLEASE TRY OUR APP") and merchant names out of the confirmed
# set while still surfacing them as weak candidates.
CURRENCY_LABEL_RE = re.compile(
    # English/Spanish plus German (Währung, umlaut-stripped Wahrung) -- so a
    # European statement that labels its currency in its own language ("Währung:
    # CHF") still confirms the code. Deliberately NOT "devise" (collides with the
    # common English verb, "devise a EUR plan") nor "valuta" (means "value date"
    # in German/Nordic banking, "Valuta 15.01.2025 USD", not currency): both
    # produced false confirmations. French/Italian/Dutch statements still confirm
    # via the € symbol or an adjacent amount, so nothing real is lost.
    r"\b(?:currenc(?:y|ies)|monedas?|divisas?|w[aä]hrung|"
    r"denominat(?:ed|ion)|iso\s*4217|movimientos?\s+de\s+cuenta\s+en)\b",
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
                content_sha256=file_sha256(path), content_bytes=file_byte_size(path),
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


def file_byte_size(path: Path) -> int | None:
    """Byte size of a file, or None if it cannot be statted."""
    try:
        return path.stat().st_size
    except OSError:  # pragma: no cover - unreadable file already warned elsewhere.
        return None


def file_profile(
    path: Path,
    pages: list[dict[str, object]],
    warnings: list[str],
    is_pdf: bool,
    text_layer_expected: bool = True,
    content_sha256: str | None = None,
    content_bytes: int | None = None,
) -> dict[str, object]:
    text = "\n".join(str(page.get("text", "")) for page in pages)
    lines = split_lines(text)
    page_lines = [
        {
            "page": int(page.get("page", index)),
            "lines": split_lines(str(page.get("text", ""))),
        }
        for index, page in enumerate(pages, start=1)
    ]
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
        "content_bytes": content_bytes,
        "lines": lines,
        # Internal parsing aid. build_preflight converts this to compact source
        # references and never serializes the extracted page text itself.
        "page_lines": page_lines,
        "warnings": stable_unique(warnings),
    }


# ISO_DATE_TAIL_RE matches the "-MM-DD" that FOLLOWS a year heading a numeric
# date ("2025-04-01"); the tail form ("01.01.2025") is caught by the preceding
# separator instead. MONTH_ADJ_RE finds a month name; it is used ONLY to bridge a
# hard copyright marker to its year across an intervening month ("© May 2019"),
# never to rescue a year next to a dual-use marker.
ISO_DATE_TAIL_RE = re.compile(r"[-/.]\d{1,2}[-/.]\d{1,2}")
MONTH_ADJ_RE = MONTH_TOKEN_RE
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


def _hard_suppressed_years(hard_spans: list, month_spans: list, year_spans: list) -> set:
    """Indices of ``year_spans`` a hard copyright marker suppresses, transitively.

    Two stages, on purpose:
      1. Seed -- a year the marker reaches directly or via one month name
         ("© 2019", "© May 2019", "Copyright January 2020").
      2. Extend -- absorb any year DIRECTLY adjacent (within the window, with NO
         month hop) to an already-suppressed year, which closes over a
         comma/space-separated copyright list ("© 2019, 2020, 2021").

    The extend step is deliberately month-free: a month hop there would let
    suppression chain backward through a period phrase whose years are separated
    by a month word ("January 2025 to January 2025 ... © 2010"), wrongly dropping
    the period year. Direct-only extension stops at the first prose gap wider than
    the window, so a real period year sharing the line is untouched.
    """
    suppressed = {
        i for i, yspan in enumerate(year_spans)
        if _marker_reaches_year(hard_spans, yspan, month_spans)
    }
    changed = True
    while changed:
        changed = False
        for i, yspan in enumerate(year_spans):
            if i not in suppressed and any(
                _span_gap(year_spans[j], yspan) <= YEAR_CONTEXT_WINDOW for j in suppressed
            ):
                suppressed.add(i)
                changed = True
    return suppressed


def detect_years(lines: Iterable[str]) -> list[int]:
    years: set[int] = set()
    for line in lines:
        hard_spans = [match.span() for match in HARD_COPYRIGHT_MARKER_RE.finditer(line)]
        dual_spans = [match.span() for match in DUAL_USE_MARKER_RE.finditer(line)]
        month_spans = [match.span() for match in MONTH_ADJ_RE.finditer(line)]
        year_matches = list(YEAR_RE.finditer(line))
        year_spans = [match.span() for match in year_matches]
        hard_suppressed = _hard_suppressed_years(hard_spans, month_spans, year_spans)
        for i, match in enumerate(year_matches):
            # Hard copyright/legal marker: a year in its clause is never a
            # statement period. Suppressed directly ("© 2019", each end of a
            # "© 2019-2024" range), through a month ("© May 2019", "Copyright
            # January 2020"), or transitively down a comma/space-separated year
            # list ("© 2019, 2020, 2021"). The run stops at the first prose gap, so
            # a real period year sharing the line (far from the marker) survives.
            if i in hard_suppressed:
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
            if not numeric_date and _marker_reaches_year(dual_spans, match.span(), month_spans):
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


def is_month_year_period_heading(line: str) -> bool:
    """Whether a compact line is a month/year statement heading.

    A bare ``January 2025`` or ``Extracto Bancario de Enero de 2025`` helps a
    reviewer when a PDF has no complete date range. A sentence that happens to
    mention a month/year, especially with transaction identifiers or amounts,
    does not. Keep the accepted vocabulary deliberately small and reject extra
    digits after removing the month and year.
    """
    cleaned = clean_line(line)
    if len(cleaned) > 80:
        return False
    month_matches = list(MONTH_TOKEN_RE.finditer(cleaned))
    year_matches = list(YEAR_RE.finditer(cleaned))
    if len(month_matches) != 1 or len(year_matches) != 1:
        return False
    # A full day-month-year date is handled only by a connected range or a
    # labelled endpoint, never by this display-only month/year fallback.
    if line_dates(cleaned):
        return False
    retained = list(cleaned)
    for match in month_matches + year_matches:
        for index in range(match.start(), match.end()):
            retained[index] = " "
    remainder = "".join(retained)
    if re.search(r"\d", remainder):
        return False
    words = re.findall(r"[a-z]+", fold_accents(remainder).casefold())
    return all(word in MONTH_YEAR_HEADING_WORDS for word in words)


def has_connected_period_dates(line: str) -> bool:
    """Whether one line contains two complete dates joined as a period."""
    dates = line_dates(line)
    if len(dates) < 2:
        return False
    _start, start_span, _start_confidence = dates[0]
    _end, end_span, _end_confidence = dates[-1]
    return bool(PERIOD_CONNECTOR_RE.search(line[start_span[1]:end_span[0]]))


def detect_periods(lines: Iterable[str]) -> list[str]:
    """Return reviewable statement-period labels without transaction narratives."""
    periods: list[str] = []
    for raw_line in lines:
        line = clean_line(str(raw_line))
        if not line:
            continue
        has_year = bool(YEAR_RE.search(line))
        # ``desde``/``from`` also occur inside transaction descriptions. Treat
        # them as statement-period evidence only when they begin a compact
        # heading, never merely because the word and one transaction date share
        # a line.
        period_heading = bool(PERIOD_HEADING_PREFIX_RE.search(line))
        if (
            NUMERIC_PERIOD_RE.search(line)
            or has_connected_period_dates(line)
            or (period_heading and has_year)
            or is_month_year_period_heading(line)
        ):
            periods.append(line)
    return stable_unique(periods, limit=20)


def _calendar_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _numeric_date(match: re.Match[str]) -> tuple[date, str] | None:
    first, second, third = (match.group("first"), match.group("second"), match.group("third"))
    a, b, c = int(first), int(second), int(third)
    if len(first) == 4:
        parsed = _calendar_date(a, b, c)
        return (parsed, "high") if parsed else None
    if len(third) != 4:
        return None
    if a > 12 and b <= 12:
        parsed = _calendar_date(c, b, a)
        return (parsed, "high") if parsed else None
    if b > 12 and a <= 12:
        parsed = _calendar_date(c, a, b)
        return (parsed, "high") if parsed else None
    # dd/mm and mm/dd are indistinguishable when both components are <= 12.
    # Preserve a usable conservative interpretation, but mark it medium so a
    # downstream reviewer can see that the numeric ordering was ambiguous.
    parsed = _calendar_date(c, b, a)
    return (parsed, "medium") if parsed else None


def line_dates(line: str) -> list[tuple[date, tuple[int, int], str]]:
    """Extract complete calendar dates with spans and parse confidence."""
    found: list[tuple[date, tuple[int, int], str]] = []
    for match in NUMERIC_DATE_RE.finditer(line):
        parsed = _numeric_date(match)
        if parsed:
            found.append((parsed[0], match.span(), parsed[1]))
    for pattern in (TEXT_DATE_MONTH_FIRST_RE, TEXT_DATE_DAY_FIRST_RE):
        for match in pattern.finditer(line):
            month = MONTH_NUMBERS[match.group("month").casefold().rstrip(".")]
            day = int(match.group("day"))
            parsed = _calendar_date(int(match.group("year")), month, day)
            if parsed:
                found.append((parsed, match.span(), "high"))
    # The textual patterns are intentionally disjoint, but retain this guard so
    # a future locale expansion cannot create duplicate date evidence.
    deduped: list[tuple[date, tuple[int, int], str]] = []
    seen: set[tuple[date, tuple[int, int]]] = set()
    for item in sorted(found, key=lambda value: value[1]):
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def detect_document_metadata_dates(page_lines: Iterable[object]) -> list[dict[str, object]]:
    """Return source-bound generated-on dates without promoting them to coverage.

    Only date tokens after a narrow document-generation label are retained. The
    compact artifact intentionally carries the parsed date and source anchor,
    never the extracted statement line itself.
    """
    evidence: list[dict[str, object]] = []
    seen: set[tuple[int, int, str]] = set()
    for raw_page in page_lines:
        if not isinstance(raw_page, dict):
            continue
        page = int(raw_page.get("page", 0) or 0)
        raw_lines = raw_page.get("lines")
        if not isinstance(raw_lines, list):
            continue
        for line_number, raw_line in enumerate(raw_lines, start=1):
            line = str(raw_line)
            marker = DOCUMENT_GENERATED_ON_RE.search(line)
            if not marker:
                continue
            for parsed, span, confidence in line_dates(line):
                if span[0] < marker.end():
                    continue
                key = (page, line_number, parsed.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                evidence.append(
                    {
                        "kind": "generated-on",
                        "date": parsed.isoformat(),
                        "confidence": confidence,
                        "source_ref": {"page": page, "line": line_number},
                    }
                )
    return evidence


def detect_period_intervals(page_lines: Iterable[object]) -> list[dict[str, object]]:
    """Return complete, source-referenced statement-period intervals.

    Prefer two complete dates joined in one extracted line. Some columnar PDFs
    instead put a labelled ``Desde`` date immediately before a labelled
    ``Hasta`` date; accept that narrow same-page pair while retaining both
    source references. Month-only labels remain in ``detected_periods`` for
    human review, but are not upgraded into calendar coverage claims.
    """
    intervals: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, int, int]] = set()

    def add_interval(
        start: date,
        end: date,
        start_confidence: str,
        end_confidence: str,
        page: int,
        start_line: int,
        end_line: int | None = None,
    ) -> None:
        if end < start:
            return
        actual_end_line = end_line if end_line is not None else start_line
        key = (start.isoformat(), end.isoformat(), page, start_line, actual_end_line)
        if key in seen:
            return
        seen.add(key)
        actual_end_line = end_line if end_line is not None else start_line
        item: dict[str, object] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "confidence": "high" if start_confidence == end_confidence == "high" else "medium",
            "source_ref": {"page": page, "line": start_line},
            "end_source_ref": {"page": page, "line": actual_end_line},
        }
        intervals.append(item)

    for raw_page in page_lines:
        if not isinstance(raw_page, dict):
            continue
        page = int(raw_page.get("page", 0) or 0)
        raw_lines = raw_page.get("lines")
        if not isinstance(raw_lines, list):
            continue
        pending_start: tuple[date, int, str] | None = None
        for line_number, raw_line in enumerate(raw_lines, start=1):
            line = str(raw_line)
            dates = line_dates(line)
            if len(dates) >= 2:
                start, start_span, start_confidence = dates[0]
                end, end_span, end_confidence = dates[-1]
                connector = line[start_span[1]:end_span[0]]
                if PERIOD_CONNECTOR_RE.search(connector):
                    add_interval(start, end, start_confidence, end_confidence, page, line_number)

            if len(dates) != 1:
                continue
            endpoint, _span, confidence = dates[0]
            if SPLIT_PERIOD_START_RE.search(line):
                pending_start = (endpoint, line_number, confidence)
                continue
            if SPLIT_PERIOD_END_RE.search(line) and pending_start is not None:
                start, start_line, start_confidence = pending_start
                if line_number - start_line <= SPLIT_PERIOD_MAX_LINE_DISTANCE:
                    add_interval(start, endpoint, start_confidence, confidence, page, start_line, line_number)
                pending_start = None
    return intervals


def detect_contextual_date_evidence(
    page_lines: Iterable[object], period_intervals: Iterable[dict[str, object]]
) -> list[dict[str, object]]:
    """Keep labelled opening/prior dates without promoting them to periods."""
    period_refs: set[tuple[int, int]] = set()
    for interval in period_intervals:
        if not isinstance(interval, dict):
            continue
        for key in ("source_ref", "end_source_ref"):
            source_ref = interval.get(key)
            if isinstance(source_ref, dict):
                period_refs.add((int(source_ref.get("page", 0)), int(source_ref.get("line", 0))))
    evidence: list[dict[str, object]] = []
    seen: set[tuple[int, int, int, str | None]] = set()
    for raw_page in page_lines:
        if not isinstance(raw_page, dict):
            continue
        page = int(raw_page.get("page", 0) or 0)
        raw_lines = raw_page.get("lines")
        if not isinstance(raw_lines, list):
            continue
        for line_number, raw_line in enumerate(raw_lines, start=1):
            line = str(raw_line)
            if (page, line_number) in period_refs or not CONTEXTUAL_BALANCE_RE.search(line):
                continue
            dates = line_dates(line)
            if dates:
                values = [(parsed.year, parsed.isoformat()) for parsed, _span, _confidence in dates]
            else:
                values = [(int(match.group(1)), None) for match in YEAR_RE.finditer(line)]
            for year, value in values:
                key = (page, line_number, year, value)
                if key in seen:
                    continue
                seen.add(key)
                item: dict[str, object] = {
                    "year": year,
                    "classification": "opening-or-prior-balance",
                    "source_ref": {"page": page, "line": line_number},
                }
                if value:
                    item["date"] = value
                evidence.append(item)
    return evidence


def is_tax_year_boundary_opening(start: date, end: date, tax_year: int) -> bool:
    """Whether a period starts on the prior year-end opening boundary."""
    return start == date(tax_year - 1, 12, 31) and end.year == tax_year and end >= date(tax_year, 1, 1)


def detect_tax_year_boundary_openings(
    period_intervals: Iterable[dict[str, object]], tax_year: int
) -> list[dict[str, object]]:
    """Retain exact prior-year opening boundaries as contextual evidence."""
    evidence: list[dict[str, object]] = []
    seen: set[tuple[int, int]] = set()
    for interval in period_intervals:
        try:
            start = date.fromisoformat(str(interval.get("start")))
            end = date.fromisoformat(str(interval.get("end")))
        except ValueError:
            continue
        if not is_tax_year_boundary_opening(start, end, tax_year):
            continue
        source_ref = interval.get("source_ref") if isinstance(interval.get("source_ref"), dict) else {}
        page = int(source_ref.get("page", 0) or 0)
        line = int(source_ref.get("line", 0) or 0)
        key = (page, line)
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            {
                "year": start.year,
                "date": start.isoformat(),
                "classification": "tax-year-boundary-opening",
                "source_ref": {"page": page, "line": line},
            }
        )
    return evidence


def statement_period_years_for_tax_year(intervals: Iterable[dict[str, object]], tax_year: int) -> list[int]:
    """Return period years after demoting an exact prior-year opening boundary."""
    years: set[int] = set()
    for interval in intervals:
        try:
            start = date.fromisoformat(str(interval.get("start")))
            end = date.fromisoformat(str(interval.get("end")))
        except ValueError:
            continue
        interval_years_found = set(range(start.year, end.year + 1))
        if is_tax_year_boundary_opening(start, end, tax_year):
            interval_years_found.discard(tax_year - 1)
        years.update(interval_years_found)
    return sorted(years)


def interval_years(intervals: Iterable[dict[str, object]]) -> list[int]:
    years: set[int] = set()
    for interval in intervals:
        try:
            start = date.fromisoformat(str(interval.get("start")))
            end = date.fromisoformat(str(interval.get("end")))
        except ValueError:
            continue
        years.update(range(start.year, end.year + 1))
    return sorted(years)


def period_coverage_review(intervals: Iterable[dict[str, object]], tax_year: int) -> dict[str, object]:
    """Describe gaps in multiple labelled periods without asserting completeness."""
    year_start = date(tax_year, 1, 1)
    year_end = date(tax_year, 12, 31)
    clipped: list[tuple[date, date]] = []
    for interval in intervals:
        try:
            start = date.fromisoformat(str(interval.get("start")))
            end = date.fromisoformat(str(interval.get("end")))
        except ValueError:
            continue
        if end < year_start or start > year_end:
            continue
        clipped.append((max(start, year_start), min(end, year_end)))
    clipped.sort()
    # A lone monthly statement is common and cannot establish a statement-set
    # cadence. Do not add a coverage gap gate until two labelled periods exist.
    if len(clipped) < 2:
        return {
            "intervals_detected": len(clipped),
            "calendar_gaps": [],
            "review_note": "Detected period labels are intake hints, not proof of transaction or balance coverage.",
        }

    merged: list[list[date]] = []
    for start, end in clipped:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end

    gaps: list[dict[str, object]] = []
    if merged[0][0] > year_start:
        gaps.append({"start": year_start.isoformat(), "end": (merged[0][0] - timedelta(days=1)).isoformat()})
    for previous, current in zip(merged, merged[1:]):
        if current[0] > previous[1] + timedelta(days=1):
            gaps.append(
                {
                    "start": (previous[1] + timedelta(days=1)).isoformat(),
                    "end": (current[0] - timedelta(days=1)).isoformat(),
                }
            )
    if merged[-1][1] < year_end:
        gaps.append({"start": (merged[-1][1] + timedelta(days=1)).isoformat(), "end": year_end.isoformat()})
    return {
        "intervals_detected": len(clipped),
        "calendar_gaps": gaps,
        "review_note": "Detected period labels are intake hints, not proof of transaction or balance coverage.",
    }


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


def _dedupe_account_records(raw: list[tuple[str, str, bool]], limit: int = 20) -> list[tuple[str, str, bool]]:
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

    out: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for display, compact, partial in kept:
        cleaned = clean_line(display)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append((cleaned, compact, partial))
        if len(out) >= limit:
            break
    return out


def _collect_account_hint_records(lines: Iterable[str]) -> list[tuple[str, str, bool]]:
    """Return normalized account hints plus comparison metadata.

    The display form is retained for review artifacts, while the compact and
    partial values preserve the cautious alias rules used to decide whether a
    masked/footer hint belongs to a fully printed account identifier.
    """
    raw: list[tuple[str, str, bool]] = []
    cleaned_lines = [clean_line(line) for line in lines]
    for line in cleaned_lines:
        if COUNTERPARTY_RE.search(line):
            # A transfer/counterparty line names the other party's account or
            # IBAN, not the statement holder's; skip it entirely.
            continue
        patterns = (ACCOUNT_LABEL_RE,)
        if not ACCOUNT_ADMINISTRATION_TRANSACTION_RE.search(line):
            patterns += (ACCOUNT_BARE_RE,)
        for pattern in patterns:
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

    for index, line in enumerate(cleaned_lines):
        if COUNTERPARTY_RE.search(line):
            continue
        match = STANDALONE_ACCOUNT_NUMBER_RE.match(line)
        if not match:
            continue
        context_start = max(0, index - ACCOUNT_HEADER_CONTEXT_LINES)
        if not any(ACCOUNT_PRODUCT_HEADER_RE.match(item) for item in cleaned_lines[context_start:index]):
            continue
        token = account_token(match.group(1))
        if token:
            compact = _account_compact(token)
            raw.append((token, compact, "*" in compact or "X" in compact))
    return _dedupe_account_records(raw)


def detect_account_hints(lines: Iterable[str]) -> list[str]:
    """Return human-reviewable account hints without exposing comparison metadata."""
    return [display for display, _compact, _partial in _collect_account_hint_records(lines)]


def account_linkage_review(
    statement_files: Iterable[dict[str, object]], canonical_records: list[tuple[str, str, bool]]
) -> dict[str, object]:
    """Summarize whether every supplied PDF is linked to one detected account.

    A single account hint somewhere in a set is not enough to prove that every
    PDF belongs to that account.  When exactly one canonical account is known,
    require each non-structural statement to carry an equivalent source hint;
    otherwise a reviewer must explicitly confirm one-account scope.
    """
    files = list(statement_files)
    if len(canonical_records) != 1:
        return {
            "status": "not-applicable",
            "canonical_account_hint_count": len(canonical_records),
            "matched_file_count": 0,
            "unlinked_files": [],
        }

    _display, canonical_compact, canonical_partial = canonical_records[0]
    matched_files: list[str] = []
    unlinked_files: list[str] = []
    for item in files:
        file_name = str(item.get("file") or item.get("resolved_file") or "statement.pdf")
        raw_records = item.get("_account_hint_records")
        records = raw_records if isinstance(raw_records, list) else []
        linked = any(
            isinstance(record, tuple)
            and len(record) == 3
            and _same_account(canonical_compact, canonical_partial, str(record[1]), bool(record[2]))
            for record in records
        )
        if linked:
            matched_files.append(file_name)
        else:
            unlinked_files.append(file_name)

    return {
        "status": "linked-by-source-hint" if not unlinked_files else "incomplete",
        "canonical_account_hint_count": 1,
        "matched_file_count": len(matched_files),
        "unlinked_files": unlinked_files,
    }


INSTITUTION_HEADER_LINE_LIMIT = 24
INSTITUTION_TRANSACTION_RE = re.compile(
    r"\b(?:pago|payment|transfer(?:encia)?|purchase|compra|withdrawal|deposit|"
    r"debit|credit|merchant|beneficiary|beneficiario|transaction|transacci[oó]n|pse)\b",
    re.I,
)
INSTITUTION_FOOTER_RE = re.compile(r"@|\b(?:https?://|www\.)", re.I)
INSTITUTION_AMOUNT_OR_DATE_RE = re.compile(
    r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}|[$€£¥]|\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+[.,]\d{2}"
)


def institution_header_lines(page_lines_or_lines: Iterable[object]) -> list[str]:
    """Return only the first-page header window, preserving direct helper calls."""
    items = list(page_lines_or_lines)
    if not items:
        return []
    if all(isinstance(item, str) for item in items):
        return [str(item) for item in items[:INSTITUTION_HEADER_LINE_LIMIT]]
    for raw_page in items:
        if not isinstance(raw_page, dict):
            continue
        if int(raw_page.get("page", 0) or 0) != 1:
            continue
        raw_lines = raw_page.get("lines")
        if not isinstance(raw_lines, list):
            continue
        return [str(line) for line in raw_lines[:INSTITUTION_HEADER_LINE_LIMIT]]
    return []


def detect_institution_hints(page_lines_or_lines: Iterable[object]) -> list[str]:
    hints: list[str] = []
    for line in institution_header_lines(page_lines_or_lines):
        # Restrict candidate evidence to a compact masthead window. Counterparty
        # transaction rows and support/footer text often name other banks but do
        # not identify the statement issuer.
        if (
            line_names_institution(line)
            and not STREET_ADDRESS_RE.search(line)
            and not INSTITUTION_TRANSACTION_RE.search(line)
            and not INSTITUTION_FOOTER_RE.search(line)
            and not INSTITUTION_AMOUNT_OR_DATE_RE.search(line)
        ):
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


def build_preflight(
    files: list[dict[str, object]],
    tax_year: int,
    scope: str,
    out_path: Path,
    csv_path: Path,
    *,
    require_institution: bool = False,
) -> dict[str, object]:
    if scope not in SUPPORTED_SCOPES:
        raise PreflightError(f"Unsupported scope {scope!r}. Use one-account or one-institution.")
    if require_institution and scope != "one-account":
        raise PreflightError("--require-institution is available only with one-account scope.")
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
        page_lines = item.get("page_lines", [])
        if not structural_stop:
            all_lines.extend(lines)
        parsed_page_lines = page_lines if isinstance(page_lines, list) else []
        document_metadata_dates = detect_document_metadata_dates(parsed_page_lines)
        metadata_refs = {
            (int(source_ref.get("page", 0)), int(source_ref.get("line", 0)))
            for evidence in document_metadata_dates
            if isinstance(evidence.get("source_ref"), dict)
            for source_ref in [evidence["source_ref"]]
        }
        year_evidence_lines = [
            str(raw_line)
            for raw_page in parsed_page_lines
            if isinstance(raw_page, dict) and isinstance(raw_page.get("lines"), list)
            for line_number, raw_line in enumerate(raw_page["lines"], start=1)
            if (int(raw_page.get("page", 0) or 0), line_number) not in metadata_refs
        ]
        years = detect_years(year_evidence_lines if parsed_page_lines else lines)
        period_intervals = detect_period_intervals(parsed_page_lines)
        contextual_date_evidence = detect_contextual_date_evidence(
            parsed_page_lines, period_intervals
        )
        contextual_date_evidence.extend(detect_tax_year_boundary_openings(period_intervals, tax_year))
        statement_period_years = statement_period_years_for_tax_year(period_intervals, tax_year)
        contextual_years = sorted(
            {
                int(evidence["year"])
                for evidence in contextual_date_evidence
                if isinstance(evidence.get("year"), int)
            }
        )
        unresolved_years = sorted(set(years) - set(statement_period_years) - set(contextual_years))
        # Prefer explicit statement-period ranges. When a source only supplies a
        # month/year label, retain the legacy non-contextual year detector as the
        # safe fallback rather than inventing an interval.
        coverage_years = statement_period_years or sorted(set(years) - set(contextual_years))
        account_hint_records = _collect_account_hint_records(lines)
        statement_files.append(
            {
                "file": item.get("file"),
                "resolved_file": item.get("resolved_file"),
                "is_pdf": item.get("is_pdf"),
                "page_count": item.get("page_count"),
                "character_count": item.get("character_count"),
                "content_sha256": item.get("content_sha256"),
                "content_bytes": item.get("content_bytes"),
                "detected_years": years,
                "detected_periods": detect_periods(lines),
                "statement_period_years": statement_period_years,
                "period_intervals": period_intervals,
                "document_metadata_dates": document_metadata_dates,
                "contextual_date_evidence": contextual_date_evidence,
                "contextual_years": contextual_years,
                "unresolved_years": unresolved_years,
                "coverage_years": coverage_years,
                "statement_titles": detect_statement_titles(lines),
                "currency": detect_currency(lines),
                "account_hints": [display for display, _compact, _partial in account_hint_records],
                "_account_hint_records": account_hint_records,
                "institution_hints": detect_institution_hints(
                    page_lines if isinstance(page_lines, list) else lines
                ),
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
    statement_period_years = sorted(
        {year for item in statement_files for year in item.get("statement_period_years", [])}
    )
    contextual_years = sorted({year for item in statement_files for year in item.get("contextual_years", [])})
    unresolved_years = sorted({year for item in statement_files for year in item.get("unresolved_years", [])})
    coverage_years = sorted({year for item in statement_files for year in item.get("coverage_years", [])})
    period_intervals: list[dict[str, object]] = []
    document_metadata_dates: list[dict[str, object]] = []
    contextual_date_evidence: list[dict[str, object]] = []
    for item in statement_files:
        source_file = str(item.get("file") or "")
        for interval in item.get("period_intervals", []):
            if not isinstance(interval, dict):
                continue
            copied = dict(interval)
            source_ref = interval.get("source_ref") if isinstance(interval.get("source_ref"), dict) else {}
            copied["source_ref"] = {"file": source_file, **source_ref}
            end_source_ref = interval.get("end_source_ref") if isinstance(interval.get("end_source_ref"), dict) else source_ref
            copied["end_source_ref"] = {"file": source_file, **end_source_ref}
            period_intervals.append(copied)
        for evidence in item.get("document_metadata_dates", []):
            if not isinstance(evidence, dict):
                continue
            copied = dict(evidence)
            source_ref = evidence.get("source_ref") if isinstance(evidence.get("source_ref"), dict) else {}
            copied["source_ref"] = {"file": source_file, **source_ref}
            document_metadata_dates.append(copied)
        for evidence in item.get("contextual_date_evidence", []):
            if not isinstance(evidence, dict):
                continue
            copied = dict(evidence)
            source_ref = evidence.get("source_ref") if isinstance(evidence.get("source_ref"), dict) else {}
            copied["source_ref"] = {"file": source_file, **source_ref}
            contextual_date_evidence.append(copied)
    out_of_period_generated_dates = [
        evidence
        for evidence in document_metadata_dates
        if str(evidence.get("date", ""))[:4] != str(tax_year)
    ]
    if out_of_period_generated_dates:
        generated_dates = stable_unique(
            str(evidence.get("date")) for evidence in out_of_period_generated_dates if evidence.get("date")
        )
        message = (
            f"Generated-on document metadata date(s) outside requested tax year {tax_year}: "
            f"{', '.join(generated_dates)}. They do not establish statement-period coverage."
        )
        warnings.append(message)
        add_gate(gates, OUT_OF_PERIOD_GENERATED_DATE_GATE, message)
    outside_years = [year for year in coverage_years if year != tax_year]
    if outside_years:
        message = f"Detected statement-period year(s) outside requested tax year {tax_year}: {', '.join(str(year) for year in outside_years)}."
        warnings.append(message)
        add_gate(gates, "mixed-years", message)
    unresolved_outside_years = [year for year in unresolved_years if year != tax_year]
    if unresolved_outside_years:
        message = (
            "Detected year evidence outside the requested statement periods that could not be classified as contextual: "
            f"{', '.join(str(year) for year in unresolved_outside_years)}."
        )
        warnings.append(message)
        add_gate(gates, "unresolved-year-evidence", message)
    if all_lines and not coverage_years:
        warnings.append("No statement years were detected; verify the PDFs belong to the requested tax year.")
        add_gate(gates, "unknown-year-coverage", "No statement years were detected; verify statement periods manually.")

    coverage_review = period_coverage_review(period_intervals, tax_year)
    possible_gaps = coverage_review.get("calendar_gaps")
    if isinstance(possible_gaps, list) and possible_gaps:
        ranges = ", ".join(
            f"{gap.get('start')} through {gap.get('end')}"
            for gap in possible_gaps
            if isinstance(gap, dict)
        )
        message = (
            "Detected statement-period labels leave possible calendar coverage gap(s): "
            f"{ranges}. Obtain the missing statements or review the source periods."
        )
        warnings.append(message)
        add_gate(gates, "possible-missing-statement-period", message)

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

    account_records = _collect_account_hint_records(all_lines)
    account_hints = [display for display, _compact, _partial in account_records]
    if scope == "one-account" and len(account_hints) > 1:
        message = f"Multiple account hints found; verify this is one account: {', '.join(account_hints[:8])}."
        warnings.append(message)
        add_gate(gates, "possible-mixed-accounts", message)
    if all_lines and scope == "one-account" and not account_hints:
        message = "No account number/designation hint was found; verify this is one account."
        warnings.append(message)
        add_gate(gates, "unknown-account", message)

    account_linkage = (
        account_linkage_review(statement_files, account_records)
        if scope == "one-account"
        else {
            "status": "not-applicable",
            "canonical_account_hint_count": len(account_records),
            "matched_file_count": 0,
            "unlinked_files": [],
        }
    )
    if (
        scope == "one-account"
        and account_linkage["status"] == "incomplete"
        and not any(gate.get("severity") == "stop" for gate in gates)
    ):
        unlinked_files = account_linkage["unlinked_files"]
        assert isinstance(unlinked_files, list)
        names = ", ".join(Path(str(path)).name for path in unlinked_files)
        message = (
            "A single account hint was found, but it could not be linked from every statement PDF: "
            f"{names}. Confirm the supplied PDFs represent one account."
        )
        warnings.append(message)
        add_gate(gates, "incomplete-account-linkage", message)

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
    institution_required = scope == "one-institution" or require_institution
    if institution_required and len(per_file_institution_signatures) > 1:
        message = f"Multiple institution hints found; verify this is one institution: {', '.join(institution_hints[:6])}."
        warnings.append(message)
        add_gate(gates, "possible-mixed-institutions", message)
    if all_lines and institution_required and not institution_hints:
        message = (
            "No institution hint was found in early statement text; a graphical logo or filename is not automatic "
            "institution evidence, so verify the institution manually."
        )
        warnings.append(message)
        add_gate(gates, "unknown-institution", message)

    title_values = stable_unique((title for item in statement_files for title in item.get("statement_titles", [])), limit=20)
    period_values = stable_unique((period for item in statement_files for period in item.get("detected_periods", [])), limit=30)
    primary_institution = most_common_hint(institution_hints)
    status = REVIEW_REQUIRED_STATUS if gates else READY_STATUS

    for item in statement_files:
        item.pop("_account_hint_records", None)

    return {
        "schema_version": SCHEMA_VERSION,
        "skill": PREFLIGHT_SKILL,
        "status": status,
        "tax_year": tax_year,
        "scope": scope,
        "requirements": {"institution_required": require_institution},
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
        "account_linkage": account_linkage,
        "institution_hints": institution_hints,
        "coverage_hints": {
            "requested_tax_year": tax_year,
            "detected_years": detected_years,
            "statement_period_years": statement_period_years,
            "contextual_years": contextual_years,
            "unresolved_years": unresolved_years,
            "outside_requested_years": outside_years,
            "detected_periods": period_values,
            "period_intervals": period_intervals,
            "document_metadata_dates": document_metadata_dates,
            "contextual_date_evidence": contextual_date_evidence,
            "period_coverage_review": coverage_review,
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
        "content_bytes",
        "content_sha256",
        "detected_years",
        "detected_periods",
        "statement_period_years",
        "period_intervals",
        "document_metadata_dates",
        "contextual_years",
        "contextual_date_evidence",
        "unresolved_years",
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
            intervals = item.get("period_intervals") if isinstance(item.get("period_intervals"), list) else []
            metadata_dates = item.get("document_metadata_dates") if isinstance(item.get("document_metadata_dates"), list) else []
            contextual = item.get("contextual_date_evidence") if isinstance(item.get("contextual_date_evidence"), list) else []

            def compact_source_ref(value: object, key: str = "source_ref") -> str:
                source_ref = value.get(key) if isinstance(value, dict) else None
                if not isinstance(source_ref, dict):
                    return ""
                page = source_ref.get("page")
                line = source_ref.get("line")
                return f"p{page}/l{line}" if page and line else ""

            def compact_period_endpoint_refs(value: object) -> str:
                start_ref = compact_source_ref(value)
                end_ref = compact_source_ref(value, "end_source_ref") or start_ref
                parts = []
                if start_ref:
                    parts.append(f"start={start_ref}")
                if end_ref:
                    parts.append(f"end={end_ref}")
                return "; ".join(parts)

            row = {
                "file": item.get("file"),
                "is_pdf": item.get("is_pdf"),
                "page_count": item.get("page_count"),
                "character_count": item.get("character_count"),
                "content_bytes": item.get("content_bytes"),
                "content_sha256": item.get("content_sha256"),
                "detected_years": "; ".join(str(year) for year in item.get("detected_years", [])),
                "detected_periods": "; ".join(str(period) for period in item.get("detected_periods", [])),
                "statement_period_years": "; ".join(str(year) for year in item.get("statement_period_years", [])),
                "period_intervals": "; ".join(
                    " ".join(
                        part
                        for part in (
                            f"{interval.get('start')}..{interval.get('end')}" if isinstance(interval, dict) else "",
                            str(interval.get("confidence")) if isinstance(interval, dict) else "",
                            compact_period_endpoint_refs(interval),
                        )
                        if part
                    )
                    for interval in intervals
                    if isinstance(interval, dict)
                ),
                "document_metadata_dates": "; ".join(
                    " ".join(
                        part
                        for part in (
                            str(evidence.get("date") or ""),
                            str(evidence.get("kind") or ""),
                            str(evidence.get("confidence") or ""),
                            compact_source_ref(evidence),
                        )
                        if part
                    )
                    for evidence in metadata_dates
                    if isinstance(evidence, dict)
                ),
                "contextual_years": "; ".join(str(year) for year in item.get("contextual_years", [])),
                "contextual_date_evidence": "; ".join(
                    " ".join(
                        part
                        for part in (
                            str(evidence.get("date") or evidence.get("year")),
                            str(evidence.get("classification") or ""),
                            compact_source_ref(evidence),
                        )
                        if part
                    )
                    for evidence in contextual
                    if isinstance(evidence, dict)
                ),
                "unresolved_years": "; ".join(str(year) for year in item.get("unresolved_years", [])),
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
    if exit_nonzero_on_review and status == REVIEW_REQUIRED_STATUS:
        return REVIEW_EXIT_CODE
    return 0


def load_json_artifact(path: Path, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PreflightError(f"Could not read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PreflightError(f"{label.capitalize()} {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PreflightError(f"{label.capitalize()} {path} must contain a JSON object.")
    return data


def review_gates_for_handoff(data: dict[str, object]) -> list[dict[str, str]]:
    if data.get("skill") != PREFLIGHT_SKILL:
        raise PreflightError(f"Preflight JSON must come from {PREFLIGHT_SKILL}.")
    if str(data.get("schema_version", "")) != SCHEMA_VERSION:
        raise PreflightError(
            f"Unsupported preflight schema_version {data.get('schema_version')!r}; rerun preflight with the current skill."
        )
    if data.get("status") != REVIEW_REQUIRED_STATUS:
        raise PreflightError(
            "review-handoff requires a preflight with status review-required; pass a ready preflight directly to the downstream skill."
        )
    raw_gates = data.get("review_gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise PreflightError("review-required preflight has no review_gates to acknowledge; rerun preflight.")

    gates: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for raw_gate in raw_gates:
        if not isinstance(raw_gate, dict):
            raise PreflightError("Preflight review_gates must contain objects; rerun preflight.")
        code = str(raw_gate.get("code") or "").strip()
        severity = str(raw_gate.get("severity") or "").strip()
        message = str(raw_gate.get("message") or "").strip()
        if not code or not severity or not message:
            raise PreflightError("Every preflight review gate needs code, severity, and message; rerun preflight.")
        if code in seen_codes:
            raise PreflightError(f"Preflight has duplicate review gate code {code!r}; rerun preflight.")
        seen_codes.add(code)
        gates.append({"code": code, "severity": severity, "message": message})

    stop_codes = sorted(gate["code"] for gate in gates if gate["severity"] == "stop")
    if stop_codes:
        raise PreflightError(
            "Cannot create a reviewed handoff while structural stop gate(s) remain: "
            f"{', '.join(stop_codes)}. Fix or remove those input files, then rerun preflight."
        )
    unexpected_severities = sorted({gate["severity"] for gate in gates if gate["severity"] != "review"})
    if unexpected_severities:
        raise PreflightError(
            "Preflight review gate severities must be review or stop; found "
            f"{', '.join(unexpected_severities)}. Rerun preflight with the current skill."
        )
    return gates


def _unique_year_arguments(args: argparse.Namespace, attribute: str) -> list[int]:
    values = [int(value) for value in (getattr(args, attribute, None) or [])]
    if len(set(values)) != len(values):
        raise PreflightError(f"Pass each {attribute.replace('_', '-')} value only once.")
    return sorted(values)


def _unique_iso_date_arguments(args: argparse.Namespace, attribute: str) -> list[str]:
    values: list[str] = []
    for raw_value in (getattr(args, attribute, None) or []):
        try:
            values.append(date.fromisoformat(str(raw_value)).isoformat())
        except (TypeError, ValueError) as exc:
            raise PreflightError(
                f"{attribute.replace('_', '-')} values must be ISO calendar dates (YYYY-MM-DD)."
            ) from exc
    if len(set(values)) != len(values):
        raise PreflightError(f"Pass each {attribute.replace('_', '-')} value only once.")
    return sorted(values)


def _coverage_years(coverage: dict[str, object], key: str) -> set[int]:
    raw_values = coverage.get(key, [])
    if not isinstance(raw_values, list):
        return set()
    years: set[int] = set()
    for value in raw_values:
        try:
            years.add(int(value))
        except (TypeError, ValueError):
            continue
    return years


def preflight_requires_institution(source: dict[str, object]) -> bool:
    """Whether this one-account preflight explicitly requested issuer review."""
    requirements = source.get("requirements")
    return isinstance(requirements, dict) and requirements.get("institution_required") is True


def source_out_of_period_generated_date_evidence(source: dict[str, object], tax_year: int) -> list[dict[str, object]]:
    """Return the exact generated-on evidence that the new review gate exposes.

    The reviewer confirms only these pre-extracted dates. File/page/line anchors
    and parse confidence are copied from the immutable preflight rather than
    accepted from a command-line argument.
    """
    coverage = source.get("coverage_hints")
    if not isinstance(coverage, dict):
        return []
    raw_evidence = coverage.get("document_metadata_dates")
    if not isinstance(raw_evidence, list):
        return []

    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, int, int]] = set()
    for raw_item in raw_evidence:
        if not isinstance(raw_item, dict) or raw_item.get("kind") != "generated-on":
            continue
        try:
            parsed_date = date.fromisoformat(str(raw_item.get("date") or ""))
        except ValueError:
            continue
        if parsed_date.year == tax_year:
            continue
        source_ref = raw_item.get("source_ref")
        if not isinstance(source_ref, dict):
            continue
        source_file = str(source_ref.get("file") or "").strip()
        try:
            page = int(source_ref.get("page"))
            line = int(source_ref.get("line"))
        except (TypeError, ValueError):
            continue
        confidence = str(raw_item.get("confidence") or "")
        if not source_file or page <= 0 or line <= 0 or confidence not in {"high", "medium"}:
            continue
        item = {
            "kind": "generated-on",
            "date": parsed_date.isoformat(),
            "confidence": confidence,
            "source_ref": {"file": source_file, "page": page, "line": line},
        }
        key = (item["date"], confidence, source_file, page, line)
        if key not in seen:
            seen.add(key)
            normalized.append(item)
    return sorted(
        normalized,
        key=lambda item: (
            str(item["date"]),
            str(item["source_ref"]["file"]),  # type: ignore[index]
            int(item["source_ref"]["page"]),  # type: ignore[index]
            int(item["source_ref"]["line"]),  # type: ignore[index]
        ),
    )


def build_user_resolutions(
    source: dict[str, object], gates: list[dict[str, str]], args: argparse.Namespace
) -> dict[str, object]:
    """Validate explicit reviewer input without changing source preflight evidence."""
    gate_codes = {gate["code"] for gate in gates}
    try:
        tax_year = int(source.get("tax_year"))
    except (TypeError, ValueError):
        raise PreflightError("Preflight tax_year is invalid; rerun preflight with the current skill.") from None
    if not (MIN_TAX_YEAR <= tax_year <= MAX_TAX_YEAR):
        raise PreflightError("Preflight tax_year is outside the supported range; rerun preflight.")

    coverage = source.get("coverage_hints") if isinstance(source.get("coverage_hints"), dict) else {}
    contextual_years = _coverage_years(coverage, "contextual_years")
    unresolved_years = _coverage_years(coverage, "unresolved_years")
    outside_period_years = _coverage_years(coverage, "outside_requested_years")

    confirmed_years = _unique_year_arguments(args, "confirm_statement_year")
    classified_contextual_years = _unique_year_arguments(args, "classify_contextual_year")
    year_gate_codes = {"mixed-years", "unresolved-year-evidence", "unknown-year-coverage"} & gate_codes
    if "mixed-years" in year_gate_codes:
        # A source-referenced period that actually crosses years cannot be made
        # into a one-year statement by accepting a gate. The user must select the
        # correct tax year or supply corrected statement files and preflight again.
        raise PreflightError(
            "mixed-years cannot be resolved by reviewed input; rerun preflight with the corrected tax year or statement set."
        )
    if confirmed_years and confirmed_years != [tax_year]:
        raise PreflightError(
            f"Confirmed statement years must be exactly the requested tax year {tax_year}; rerun preflight to use another year."
        )
    if year_gate_codes and not confirmed_years:
        raise PreflightError(
            f"Pass --confirm-statement-year {tax_year} after reviewing {', '.join(sorted(year_gate_codes))}."
        )
    if classified_contextual_years and not confirmed_years:
        raise PreflightError("--classify-contextual-year requires --confirm-statement-year for the requested tax year.")
    allowed_contextual_years = contextual_years | {year for year in unresolved_years if year != tax_year}
    unexpected_contextual_years = sorted(set(classified_contextual_years) - allowed_contextual_years)
    if unexpected_contextual_years:
        raise PreflightError(
            "Contextual-year classifications must match extracted contextual or unresolved year evidence: "
            + ", ".join(str(year) for year in unexpected_contextual_years)
        )
    required_contextual_years = {year for year in unresolved_years if year != tax_year}
    if "unresolved-year-evidence" in year_gate_codes and set(classified_contextual_years) != required_contextual_years:
        raise PreflightError(
            "Resolve every out-of-period unresolved year with --classify-contextual-year before creating a reviewed handoff."
        )
    if outside_period_years:
        # Defensive guard for legacy/hand-edited artifacts whose gate list was
        # altered: a period outside the requested year is never reviewer-overridable.
        raise PreflightError(
            "Source evidence contains statement-period year(s) outside the requested tax year; rerun preflight with corrected scope."
        )

    generated_date_gate_codes = {OUT_OF_PERIOD_GENERATED_DATE_GATE} & gate_codes
    confirmed_generated_dates = _unique_iso_date_arguments(args, "confirm_generated_on_date")
    generated_date_evidence = source_out_of_period_generated_date_evidence(source, tax_year)
    expected_generated_dates = sorted({str(item["date"]) for item in generated_date_evidence})
    if generated_date_gate_codes:
        if not generated_date_evidence:
            raise PreflightError(
                "out-of-period-generated-date requires source-bound generated-on date evidence; rerun preflight."
            )
        if confirmed_generated_dates != expected_generated_dates:
            raise PreflightError(
                "Pass --confirm-generated-on-date once for every extracted out-of-period generated-on date: "
                + ", ".join(expected_generated_dates)
                + "."
            )
    elif confirmed_generated_dates:
        raise PreflightError(
            "--confirm-generated-on-date is only allowed when preflight reports out-of-period-generated-date."
        )

    currency = source.get("currency") if isinstance(source.get("currency"), dict) else {}
    source_currency = str(currency.get("code") or "")
    currency_gate_codes = {"ambiguous-dollar", "unknown-currency"} & gate_codes
    confirmed_currency = str(getattr(args, "confirm_currency", "") or "").strip().upper()
    if currency_gate_codes:
        if source_currency != "UNKNOWN":
            raise PreflightError("Currency review input is allowed only when preflight could not corroborate a currency.")
        if not confirmed_currency:
            raise PreflightError(
                "Pass --confirm-currency with an ISO 4217 code after reviewing ambiguous or unknown currency evidence."
            )
        if confirmed_currency not in CURRENCY_CODES:
            raise PreflightError(f"{confirmed_currency!r} is not a supported ISO 4217 currency code.")
    elif confirmed_currency:
        raise PreflightError("--confirm-currency is only allowed when preflight reports ambiguous-dollar or unknown-currency.")

    account_gate_codes = {"unknown-account", "possible-mixed-accounts", "incomplete-account-linkage"} & gate_codes
    confirmed_one_account = bool(getattr(args, "confirm_one_account", False))
    if account_gate_codes:
        if source.get("scope") != "one-account":
            raise PreflightError("Account-scope confirmation is valid only for a one-account preflight.")
        if not confirmed_one_account:
            raise PreflightError("Pass --confirm-one-account after reviewing the supplied statement set.")
    elif confirmed_one_account:
        raise PreflightError(
            "--confirm-one-account is only allowed when preflight reports unknown-account, possible-mixed-accounts, or incomplete-account-linkage."
        )

    institution_gate_codes = {"unknown-institution", "possible-mixed-institutions"} & gate_codes
    confirmed_institution = clean_line(str(getattr(args, "confirm_institution", "") or ""))
    if institution_gate_codes:
        scope = source.get("scope")
        institution_confirmation_allowed = (
            scope == "one-institution" and institution_gate_codes == {"unknown-institution"}
        ) or (scope == "one-account" and preflight_requires_institution(source))
        if not institution_confirmation_allowed:
            raise PreflightError(
                "Institution confirmation is valid only for an unknown one-institution preflight or a one-account preflight run with --require-institution."
            )
        if not confirmed_institution:
            raise PreflightError("Pass --confirm-institution after reviewing the supplied statement set.")
        if len(confirmed_institution) > 160:
            raise PreflightError("--confirm-institution must be at most 160 characters.")
    elif confirmed_institution:
        raise PreflightError("--confirm-institution is only allowed when preflight reports unknown-institution.")

    return {
        "statement_years": (
            {
                "status": "user-confirmed",
                "confirmed_years": confirmed_years,
                "contextual_year_classifications": [
                    {
                        "year": year,
                        "classification": "user-confirmed-contextual-prior-year",
                        "source": "user-review",
                    }
                    for year in classified_contextual_years
                ],
            }
            if confirmed_years or classified_contextual_years
            else {"status": "not-required"}
        ),
        "generated_on_dates": (
            {
                "status": "user-confirmed",
                "confirmed_dates": confirmed_generated_dates,
                "source_date_evidence": generated_date_evidence,
                "resolved_gate_codes": sorted(generated_date_gate_codes),
            }
            if generated_date_gate_codes
            else {"status": "not-required"}
        ),
        "currency": (
            {
                "status": "user-confirmed",
                "code": confirmed_currency,
                "source_currency_code": source_currency,
                "resolved_gate_codes": sorted(currency_gate_codes),
            }
            if confirmed_currency
            else {"status": "not-required"}
        ),
        "one_account": (
            {
                "status": "user-confirmed",
                "confirmed": True,
                "account_identifier_provided": False,
                "resolved_gate_codes": sorted(account_gate_codes),
            }
            if confirmed_one_account
            else {"status": "not-required"}
        ),
        "institution": (
            {
                "status": "user-confirmed",
                "name": confirmed_institution,
                "resolved_gate_codes": sorted(institution_gate_codes),
            }
            if confirmed_institution
            else {"status": "not-required"}
        ),
    }


def command_review_handoff(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    out_path = Path(args.out)
    if normalize_path(input_path) == normalize_path(out_path):
        raise PreflightError("--input and --out resolve to the same path; preserve the original preflight JSON.")
    if not args.user_review_confirmed:
        raise PreflightError("Pass --user-review-confirmed only after the user has reviewed every listed preflight gate.")

    source = load_json_artifact(input_path, "preflight JSON")
    gates = review_gates_for_handoff(source)
    expected_codes = {gate["code"] for gate in gates}
    accepted_codes = [str(code).strip() for code in (args.accept_gate or []) if str(code).strip()]
    accepted_set = set(accepted_codes)
    if len(accepted_set) != len(accepted_codes):
        raise PreflightError("Pass each --accept-gate code exactly once.")
    if accepted_set != expected_codes:
        missing = sorted(expected_codes - accepted_set)
        unexpected = sorted(accepted_set - expected_codes)
        details: list[str] = []
        if missing:
            details.append(f"missing acceptance for {', '.join(missing)}")
        if unexpected:
            details.append(f"unknown gate code(s) {', '.join(unexpected)}")
        raise PreflightError("Accepted gate codes must exactly match the review gates: " + "; ".join(details))

    user_resolutions = build_user_resolutions(source, gates, args)

    source_sha256 = file_sha256(input_path)
    if not source_sha256:
        raise PreflightError(f"Could not hash source preflight JSON {input_path}.")
    statement_files = source.get("statement_files")
    if not isinstance(statement_files, list):
        raise PreflightError("Preflight JSON has no statement_files list; rerun preflight.")
    protected_paths = {normalize_path(input_path)}
    for item in statement_files:
        if isinstance(item, dict):
            source_file = item.get("resolved_file") or item.get("file")
            if source_file:
                protected_paths.add(normalize_path(str(source_file)))
    source_artifacts = source.get("artifacts")
    if isinstance(source_artifacts, dict) and source_artifacts.get("review_csv"):
        protected_paths.add(normalize_path(str(source_artifacts["review_csv"])))
    if normalize_path(out_path) in protected_paths:
        raise PreflightError("--out would overwrite the source preflight, its review CSV, or an input statement; choose another path.")

    handoff: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "skill": PREFLIGHT_SKILL,
        "artifact_type": REVIEWED_HANDOFF_TYPE,
        "status": REVIEWED_HANDOFF_STATUS,
        "tax_year": source.get("tax_year"),
        "scope": source.get("scope"),
        "requirements": source.get("requirements"),
        "statement_files": statement_files,
        "currency": source.get("currency"),
        "profile": source.get("profile"),
        "coverage_hints": source.get("coverage_hints"),
        "account_hints": source.get("account_hints", []),
        "account_linkage": source.get("account_linkage"),
        "institution_hints": source.get("institution_hints", []),
        "warnings": source.get("warnings", []),
        "review_gates": gates,
        "source_preflight": {
            "path": str(input_path),
            "resolved_path": normalize_path(input_path),
            "sha256": source_sha256,
            "schema_version": source.get("schema_version"),
            "status": source.get("status"),
            "tax_year": source.get("tax_year"),
            "scope": source.get("scope"),
            "requirements": source.get("requirements"),
        },
        "review": {
            "user_review_confirmed": True,
            "accepted_gate_codes": sorted(accepted_set),
            "confirmed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "user_resolutions": {
            "source_preflight_sha256": source_sha256,
            **user_resolutions,
        },
        "artifacts": {"source_preflight_json": str(input_path)},
    }
    write_json(out_path, handoff)
    print(f"Wrote reviewed handoff JSON: {out_path}")
    print(f"Accepted review gates: {', '.join(sorted(accepted_set))}")
    return 0


def command_preflight(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    csv_path = Path(args.csv) if args.csv else review_csv_path(out_path)
    check_output_paths(out_path, csv_path, args.pdf)
    files = load_pdf_files(args.pdf)
    data = build_preflight(
        files,
        int(args.tax_year),
        args.scope,
        out_path,
        csv_path,
        require_institution=bool(getattr(args, "require_institution", False)),
    )
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
        clean_coverage = clean.get("coverage_hints") if isinstance(clean, dict) else None
        clean_intervals = clean_coverage.get("period_intervals") if isinstance(clean_coverage, dict) else []
        expected_clean_endpoint = {"file": "clean.pdf", "page": 1, "line": 3}
        if (
            not isinstance(clean_intervals, list)
            or len(clean_intervals) != 1
            or not isinstance(clean_intervals[0], dict)
            or clean_intervals[0].get("source_ref") != expected_clean_endpoint
            or clean_intervals[0].get("end_source_ref") != expected_clean_endpoint
        ):
            failures.append(
                f"period-endpoints: expected independent, file-bound start/end references, got {clean_intervals}"
            )
        clean_csv = root / "clean-review.csv"
        write_review_csv(clean_csv, clean)
        try:
            clean_csv_rows = list(csv.DictReader(clean_csv.read_text(encoding="utf-8").splitlines()))
        except (OSError, csv.Error):
            clean_csv_rows = []
        clean_csv_intervals = clean_csv_rows[0].get("period_intervals", "") if clean_csv_rows else ""
        if "start=p1/l3" not in clean_csv_intervals or "end=p1/l3" not in clean_csv_intervals:
            failures.append(
                f"period-endpoints: CSV must show both endpoints independently, got {clean_csv_intervals!r}"
            )

        mixed_year = build_preflight(
            [synthetic_file("mixed-year.pdf", "Example Bank\nAccount 12345678\nStatement period December 2024 to January 2025\nCurrency USD")],
            2025,
            "one-account",
            root / "mixed-year.json",
            root / "mixed-year-review.csv",
        )
        if not any(gate.get("code") == "mixed-years" for gate in mixed_year["review_gates"]):  # type: ignore[index]
            failures.append("mixed-year: expected mixed-years gate")

        boundary_opening = build_preflight(
            [
                synthetic_file(
                    "boundary-opening.pdf",
                    "Banco Ejemplo S.A.\nCuenta 12345678\nDESDE: 2024/12/31 HASTA: 2025/03/31\nMoneda COP",
                )
            ],
            2025,
            "one-account",
            root / "boundary-opening.json",
            root / "boundary-opening-review.csv",
        )
        boundary_context = boundary_opening["coverage_hints"].get("contextual_date_evidence", [])  # type: ignore[index]
        if any(gate.get("code") == "mixed-years" for gate in boundary_opening["review_gates"]):  # type: ignore[index]
            failures.append("boundary-opening: prior Dec 31 opening boundary must not trip mixed-years")
        if boundary_opening["coverage_hints"].get("statement_period_years") != [2025]:  # type: ignore[index]
            failures.append("boundary-opening: statement period years must retain only the requested year")
        if not any(
            isinstance(item, dict)
            and item.get("classification") == "tax-year-boundary-opening"
            and item.get("date") == "2024-12-31"
            for item in boundary_context
        ):
            failures.append("boundary-opening: expected source-referenced contextual boundary evidence")

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
        if detect_currency(["Movimientos de cuenta en COP", "$12,000"])["code"] != "COP":  # type: ignore[index]
            failures.append("currency-account-movements: a COP account-movements header must confirm COP")
        if detect_currency(["Movimientos de cuenta en", "COP"])["code"] != "UNKNOWN":  # type: ignore[index]
            failures.append("currency-account-movements: a split header must not confirm COP")
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
            # Round-9 Z4: a comma-separated copyright list is suppressed all the
            # way down, transitively (2021 is well past the marker window).
            (2021, "© 2019, 2020, 2021 Example Corporation. All rights reserved."),
        ]:
            if leak in detect_years([line]):
                failures.append(f"year-hard-copyright: {leak} must stay suppressed in {line!r}")
        # Round-9 guards: the transitive run must not over-reach. A hard-copyright
        # year and a real numeric date can share a line ("© 2019 ... since
        # 2020-01-01" keeps 2020), and a period year survives a distant footer
        # marker ("January 2025 ... © 2010" keeps 2025).
        if detect_years(["© 2019 Example Bank -- serving you since 2020-01-01"]) != [2020]:
            failures.append("year-run-guard: a numeric 'since 2020-01-01' must survive a '© 2019' on the same line")
        if 2025 not in detect_years(["Statement period January 2025 to January 2025    © 2010 Example"]):
            failures.append("year-run-guard: a period year must survive a distant '© 2010' footer on the same line")
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

        # A full identifier in one PDF and an equivalent masked suffix in the
        # next are source-linked. This should not add friction to normal
        # recurring statements that redact their account number differently.
        linked_account = build_preflight(
            [
                synthetic_file(
                    "linked-full.pdf",
                    "Example Bank\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD",
                ),
                synthetic_file(
                    "linked-masked.pdf",
                    "Example Bank\nAccount ending in 5678\nStatement period February 1 2025 to February 28 2025\nCurrency USD",
                ),
            ],
            2025,
            "one-account",
            root / "linked-account.json",
            root / "linked-account-review.csv",
        )
        linked_summary = linked_account.get("account_linkage")
        if not isinstance(linked_summary, dict) or linked_summary.get("status") != "linked-by-source-hint":
            failures.append(f"account-linkage: expected linked source hints, got {linked_summary}")
        if any(gate.get("code") == "incomplete-account-linkage" for gate in linked_account["review_gates"]):  # type: ignore[index]
            failures.append("account-linkage: equivalent masked account hint must not trip incomplete linkage")

        # A single hint somewhere in the set is not proof that every PDF is the
        # same account. The reviewer can resolve this without providing an
        # account number, but it must be visible before downstream extraction.
        incomplete_account_linkage = build_preflight(
            [
                synthetic_file(
                    "linked-account.pdf",
                    "Example Bank\nAccount 12345678\nStatement period January 1 2025 to June 30 2025\nCurrency USD",
                ),
                synthetic_file(
                    "unlinked-account.pdf",
                    "Example Bank\nStatement period July 1 2025 to December 31 2025\nCurrency USD",
                ),
            ],
            2025,
            "one-account",
            root / "incomplete-account-linkage.json",
            root / "incomplete-account-linkage-review.csv",
        )
        incomplete_summary = incomplete_account_linkage.get("account_linkage")
        if not isinstance(incomplete_summary, dict) or incomplete_summary.get("status") != "incomplete":
            failures.append(f"account-linkage: expected incomplete summary, got {incomplete_summary}")
        if incomplete_summary.get("matched_file_count") != 1 or incomplete_summary.get("unlinked_files") != ["unlinked-account.pdf"]:
            failures.append(f"account-linkage: expected one unlinked source PDF, got {incomplete_summary}")
        incomplete_codes = {gate.get("code") for gate in incomplete_account_linkage["review_gates"]}  # type: ignore[index]
        if "incomplete-account-linkage" not in incomplete_codes:
            failures.append("account-linkage: expected incomplete-account-linkage review gate")
        linkage_resolution_args = argparse.Namespace(
            confirm_statement_year=[],
            classify_contextual_year=[],
            confirm_currency=None,
            confirm_one_account=True,
            confirm_institution=None,
        )
        linkage_resolutions = build_user_resolutions(
            incomplete_account_linkage, incomplete_account_linkage["review_gates"], linkage_resolution_args  # type: ignore[arg-type]
        )
        one_account_resolution = linkage_resolutions.get("one_account")
        if not isinstance(one_account_resolution, dict) or one_account_resolution.get("resolved_gate_codes") != [
            "incomplete-account-linkage"
        ]:
            failures.append(f"account-linkage: expected identifier-free reviewed resolution, got {one_account_resolution}")
        linkage_source = root / "incomplete-account-linkage-source.json"
        linkage_handoff = root / "incomplete-account-linkage-handoff.json"
        write_json(linkage_source, incomplete_account_linkage)
        linkage_handoff_args = argparse.Namespace(
            input=str(linkage_source),
            out=str(linkage_handoff),
            accept_gate=["incomplete-account-linkage"],
            user_review_confirmed=True,
            confirm_statement_year=[],
            classify_contextual_year=[],
            confirm_currency=None,
            confirm_one_account=True,
            confirm_institution=None,
        )
        if command_review_handoff(linkage_handoff_args) != 0:
            failures.append("account-linkage: expected reviewed handoff to accept an explicit one-account confirmation")
        else:
            linkage_handoff_data = load_json_artifact(linkage_handoff, "account-linkage handoff")
            handoff_resolution = linkage_handoff_data.get("user_resolutions")
            if not isinstance(handoff_resolution, dict) or handoff_resolution.get("one_account") != one_account_resolution:
                failures.append(f"account-linkage: handoff lost the reviewed resolution, got {handoff_resolution}")

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

        # FBAR keeps its normal one-account behavior unless it explicitly asks
        # preflight to prove the issuing institution. This opt-in mode must gate
        # absent or conflicting evidence and retain a typed reviewer selection.
        legacy_one_account_institution = build_preflight(
            [
                synthetic_file(
                    "legacy-one-account.pdf",
                    "Account 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD",
                )
            ],
            2025,
            "one-account",
            root / "legacy-one-account-institution.json",
            root / "legacy-one-account-institution-review.csv",
        )
        if legacy_one_account_institution.get("requirements") != {"institution_required": False}:
            failures.append("institution-required: legacy one-account preflight must record institution requirement as false")
        if any(gate.get("code") == "unknown-institution" for gate in legacy_one_account_institution["review_gates"]):  # type: ignore[index]
            failures.append("institution-required: legacy one-account preflight must not introduce an institution gate")

        required_unknown_institution = build_preflight(
            [
                synthetic_file(
                    "required-unknown-institution.pdf",
                    "Account 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD",
                )
            ],
            2025,
            "one-account",
            root / "required-unknown-institution.json",
            root / "required-unknown-institution-review.csv",
            require_institution=True,
        )
        unknown_institution_codes = [str(gate.get("code")) for gate in required_unknown_institution["review_gates"]]  # type: ignore[index]
        if required_unknown_institution.get("requirements") != {"institution_required": True} or unknown_institution_codes != ["unknown-institution"]:
            failures.append("institution-required: expected unknown-institution gate only for an opt-in one-account preflight")
        required_unknown_source = root / "required-unknown-institution-source.json"
        required_unknown_handoff = root / "required-unknown-institution-handoff.json"
        write_json(required_unknown_source, required_unknown_institution)
        try:
            command_review_handoff(
                argparse.Namespace(
                    input=str(required_unknown_source),
                    out=str(required_unknown_handoff),
                    accept_gate=unknown_institution_codes,
                    user_review_confirmed=True,
                    confirm_institution="Reviewed Test Bank",
                )
            )
            required_unknown_data = load_json_artifact(required_unknown_handoff, "required institution handoff")
            required_unknown_resolutions = required_unknown_data.get("user_resolutions")
            institution_resolution = (
                required_unknown_resolutions.get("institution") if isinstance(required_unknown_resolutions, dict) else None
            )
            if not isinstance(institution_resolution, dict) or institution_resolution.get("name") != "Reviewed Test Bank":
                failures.append("institution-required: expected source-bound typed institution resolution")
        except PreflightError as exc:
            failures.append(f"institution-required: expected unknown issuer confirmation to succeed ({exc})")

        required_mixed_institutions = build_preflight(
            [
                synthetic_file(
                    "required-alpha.pdf",
                    "Alpha Bank N.A.\nAccount 12345678\nStatement period January 1 2025 to January 31 2025\nCurrency USD",
                ),
                synthetic_file(
                    "required-beta.pdf",
                    "Beta Banco S.A.\nAccount 12345678\nStatement period February 1 2025 to February 28 2025\nCurrency USD",
                ),
            ],
            2025,
            "one-account",
            root / "required-mixed-institutions.json",
            root / "required-mixed-institutions-review.csv",
            require_institution=True,
        )
        required_mixed_codes = [str(gate.get("code")) for gate in required_mixed_institutions["review_gates"]]  # type: ignore[index]
        if required_mixed_codes != ["possible-missing-statement-period", "possible-mixed-institutions"]:
            failures.append(f"institution-required: expected mixed issuer evidence gate, got {required_mixed_codes}")
        required_mixed_source = root / "required-mixed-institutions-source.json"
        write_json(required_mixed_source, required_mixed_institutions)
        try:
            command_review_handoff(
                argparse.Namespace(
                    input=str(required_mixed_source),
                    out=str(root / "required-mixed-institutions-handoff.json"),
                    accept_gate=required_mixed_codes,
                    user_review_confirmed=True,
                    confirm_institution="Reviewed Test Bank",
                )
            )
        except PreflightError as exc:
            failures.append(f"institution-required: expected mixed issuer confirmation to succeed for opt-in FBAR mode ({exc})")

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

        # A one-token alphanumeric brand ("Marca66") heads a name the
        # word-boundary match misses; the stem catches it, but a marketing line
        # sharing the stem ("Personal banking made easy") must not become an
        # institution hint.
        if not detect_institution_hints(["Marca66 S.A."]):
            failures.append("institution-stem: 'Marca66 S.A.' must be recognized as an institution")
        for header in ("Page1", "Report2025", "Jan2025", "Q1FY2025"):
            if line_names_institution(header):
                failures.append(f"institution-alnum: {header!r} must not be automatic issuer evidence")
            generic_text = (
                f"{header}\nMonthly Statement\nAccount 12345678\n"
                "Statement period January 1 2025 to January 31 2025\n"
                "Currency USD\nClosing balance 100.00 USD"
            )
            unknown_issuer = build_preflight(
                [synthetic_file(f"{header}.pdf", generic_text)],
                2025,
                "one-institution",
                root / f"{header}-issuer.json",
                root / f"{header}-issuer-review.csv",
            )
            if unknown_issuer["institution_hints"] or not any(
                gate.get("code") == "unknown-institution" for gate in unknown_issuer["review_gates"]
            ):
                failures.append(f"institution-alnum: {header!r} must require an institution review")
        required_unknown_issuer = build_preflight(
            [
                synthetic_file(
                    "page1-required.pdf",
                    "Page1\nMonthly Statement\nAccount 12345678\n"
                    "Statement period January 1 2025 to January 31 2025\n"
                    "Currency USD\nClosing balance 100.00 USD",
                )
            ],
            2025,
            "one-account",
            root / "page1-required.json",
            root / "page1-required-review.csv",
            require_institution=True,
        )
        if required_unknown_issuer["institution_hints"] or not any(
            gate.get("code") == "unknown-institution" for gate in required_unknown_issuer["review_gates"]
        ):
            failures.append("institution-alnum: Page1 must require issuer confirmation for FBAR intake")
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
        if detect_account_hints(["2025-02-03 14:17 Administración de Cuenta 12345678 12,00"]):
            failures.append("account-label: an account-administration transaction reference must not become an account hint")
        if detect_account_hints(["Administración de Cuenta Account ID 12345678"]) != ["12345678"]:
            failures.append("account-label: an explicit account ID must remain usable beside an account-administration description")

        standalone_spanish_header = [
            "ESTADO DE CUENTA",
            "CUENTA DE AHORROS",
            "NÚMERO 76543210",
            "SUCURSAL PRINCIPAL",
        ]
        if detect_account_hints(standalone_spanish_header) != ["76543210"]:
            failures.append("account-header: standalone Spanish NÚMERO after an account product must yield the account")
        if detect_account_hints(["NÚMERO 76543210"]):
            failures.append("account-header: standalone NÚMERO without account-header context must not yield an account")
        if detect_account_hints(["CUENTA DE AHORROS", "NÚMERO DE PÁGINA 76543210"]):
            failures.append("account-header: page-number wording must not yield an account")
        if detect_account_hints(["CUENTA DE AHORROS", "NÚMERO 123"]):
            failures.append("account-header: too-short standalone numbers must not yield an account")

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
        # A source-labelled generated-on date is document metadata, not a
        # statement period. It must retain a source anchor while being excluded
        # from generic year fallback coverage.
        generated_date_line = "Extracto de cuenta generado el 31 de Diciembre de 2026"
        if detect_periods([generated_date_line]):
            failures.append("generated-date-metadata: generated-on date must not be a statement-period label")
        generated_date_metadata = build_preflight(
            [
                synthetic_file(
                    "generated-date-metadata.pdf",
                    "Example Bank Account Extract\nAccount 12345678\nCurrency USD\n"
                    "Transaction date 31/12\n"
                    + generated_date_line,
                )
            ],
            2025,
            "one-account",
            root / "generated-date-metadata.json",
            root / "generated-date-metadata-review.csv",
        )
        generated_date_gates = {
            str(gate.get("code"))
            for gate in generated_date_metadata["review_gates"]
            if isinstance(gate, dict)
        }
        generated_date_coverage = generated_date_metadata.get("coverage_hints", {})
        generated_date_records = (
            generated_date_coverage.get("document_metadata_dates", [])
            if isinstance(generated_date_coverage, dict)
            else []
        )
        if not (
            {"out-of-period-generated-date", "unknown-year-coverage"} <= generated_date_gates
            and not ({"mixed-years", "unresolved-year-evidence"} & generated_date_gates)
            and isinstance(generated_date_coverage, dict)
            and generated_date_coverage.get("detected_years") == []
            and generated_date_coverage.get("period_intervals") == []
            and generated_date_records == [
                {
                    "kind": "generated-on",
                    "date": "2026-12-31",
                    "confidence": "high",
                    "source_ref": {"file": "generated-date-metadata.pdf", "page": 1, "line": 5},
                }
            ]
        ):
            failures.append(
                f"generated-date-metadata: expected source-bound metadata without year coverage, got {generated_date_metadata}"
            )
        generated_date_source = root / "generated-date-metadata-source.json"
        write_json(generated_date_source, generated_date_metadata)
        generated_date_csv = root / "generated-date-metadata-review.csv"
        write_review_csv(generated_date_csv, generated_date_metadata)
        generated_date_handoff = root / "generated-date-metadata-handoff.json"
        try:
            command_review_handoff(
                argparse.Namespace(
                    input=str(generated_date_source),
                    out=str(generated_date_handoff),
                    accept_gate=sorted(generated_date_gates),
                    user_review_confirmed=True,
                    confirm_statement_year=[2025],
                    classify_contextual_year=[],
                    confirm_generated_on_date=["2026-12-31"],
                    confirm_currency=None,
                    confirm_one_account=False,
                    confirm_institution=None,
                )
            )
        except PreflightError:
            failures.append("generated-date-metadata: expected exact source-bound reviewed resolution to be accepted")
        else:
            generated_date_handoff_data = load_json_artifact(generated_date_handoff, "generated date handoff")
            generated_date_resolutions = generated_date_handoff_data.get("user_resolutions")
            generated_date_resolution = (
                generated_date_resolutions.get("generated_on_dates")
                if isinstance(generated_date_resolutions, dict)
                else None
            )
            if not isinstance(generated_date_resolution, dict) or generated_date_resolution != {
                "status": "user-confirmed",
                "confirmed_dates": ["2026-12-31"],
                "source_date_evidence": generated_date_records,
                "resolved_gate_codes": [OUT_OF_PERIOD_GENERATED_DATE_GATE],
            }:
                failures.append(
                    f"generated-date-metadata: expected immutable generated-on resolution, got {generated_date_resolution}"
                )
        for label, confirmed_dates in (
            ("missing generated date", []),
            ("unexpected generated date", ["2026-12-30"]),
        ):
            try:
                command_review_handoff(
                    argparse.Namespace(
                        input=str(generated_date_source),
                        out=str(root / f"generated-date-metadata-{label}.json"),
                        accept_gate=sorted(generated_date_gates),
                        user_review_confirmed=True,
                        confirm_statement_year=[2025],
                        classify_contextual_year=[],
                        confirm_generated_on_date=confirmed_dates,
                        confirm_currency=None,
                        confirm_one_account=False,
                        confirm_institution=None,
                    )
                )
                failures.append(f"generated-date-metadata: expected {label} to be rejected")
            except PreflightError:
                pass
        with generated_date_csv.open(newline="", encoding="utf-8") as handle:
            generated_date_rows = list(csv.DictReader(handle))
        if (
            len(generated_date_rows) != 1
            or generated_date_rows[0].get("document_metadata_dates") != "2026-12-31 generated-on high p1/l5"
        ):
            failures.append(f"generated-date-metadata: expected compact CSV source reference, got {generated_date_rows}")

        # A real 2025 period remains coverage evidence even when a document
        # carries an out-of-period generated-on date.
        generated_date_with_period = build_preflight(
            [
                synthetic_file(
                    "generated-date-with-period.pdf",
                    "Example Bank Monthly Statement\nAccount 12345678\n"
                    "Statement period January 1 2025 to January 31 2025\nCurrency USD\n"
                    + generated_date_line,
                )
            ],
            2025,
            "one-account",
            root / "generated-date-with-period.json",
            root / "generated-date-with-period-review.csv",
        )
        generated_period_gates = {
            str(gate.get("code"))
            for gate in generated_date_with_period["review_gates"]
            if isinstance(gate, dict)
        }
        generated_period_coverage = generated_date_with_period.get("coverage_hints", {})
        if not (
            "out-of-period-generated-date" in generated_period_gates
            and not ({"mixed-years", "unresolved-year-evidence", "unknown-year-coverage"} & generated_period_gates)
            and isinstance(generated_period_coverage, dict)
            and generated_period_coverage.get("detected_years") == [2025]
            and generated_period_coverage.get("statement_period_years") == [2025]
        ):
            failures.append(
                f"generated-date-metadata: expected 2025 period coverage to remain intact, got {generated_date_with_period}"
            )
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
        if file_byte_size(probe) != len(b"example statement bytes"):
            failures.append("file-byte-size: byte count must match the source file")
        if file_sha256(root / "does-not-exist.bin") is not None:
            failures.append("file-sha256: an unreadable path must return None")
        if file_byte_size(root / "does-not-exist.bin") is not None:
            failures.append("file-byte-size: an unreadable path must return None")
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
        if not detect_periods(["2025/05/01 a 2025/05/31"]):
            failures.append("numeric-period: a yyyy/mm/dd Spanish 'a' range must be detected as a period")
        if detect_periods(["Ref 12/34 amount 56.00"]):
            failures.append("numeric-period: a lone fraction-like token must not read as a period range")

        # ``detected_periods`` is a human review aid. It retains compact
        # statement headings and source-labelled period dates, but must not
        # retain transaction narratives merely because they mention a month and
        # year. These fixtures are synthetic and cover both English and Spanish
        # shapes seen in statement text layers.
        display_periods = detect_periods(
            [
                "January 2025",
                "Extracto Bancario de Octubre de 2025",
                "Statement period January 1 2025 to March 31 2025",
                "DESDE: 01-Ene-2025",
            ]
        )
        if display_periods != [
            "January 2025",
            "Extracto Bancario de Octubre de 2025",
            "Statement period January 1 2025 to March 31 2025",
            "DESDE: 01-Ene-2025",
        ]:
            failures.append(f"display-periods: expected compact headings and labelled periods, got {display_periods}")
        transaction_narratives = [
            "Movimiento 654321 publicado en Enero 2025 importe COP 10,000",
            "Payment reference 765432 issued in March 2025 amount USD 125.00",
            "Customer since April 2025",
        ]
        if detect_periods(transaction_narratives):
            failures.append("display-periods: transaction/month narratives must not become statement-period labels")

        # A transaction line can contain a real date and the same Spanish
        # ``desde`` word used by statement-period endpoints. It must not leak
        # its narrative or balance values into the JSON/CSV review artifacts.
        display_period_header = "Estado de cuenta para el periodo de: 2025/05/01 a 2025/05/31"
        transaction_period_sentinel = "TXN-LEAK-CHECK-57"
        transaction_with_endpoint_word = (
            f"25/05/2025 {transaction_period_sentinel} desde transferencia $500,000.00 $500,001.00"
        )
        if detect_periods([display_period_header, transaction_with_endpoint_word]) != [display_period_header]:
            failures.append("display-periods: dated transaction with 'desde' must not become a period label")
        display_artifact = build_preflight(
            [
                synthetic_file(
                    "display-periods.pdf",
                    "\n".join(
                        [
                            "Example Financial Statement",
                            "Account 12345678",
                            display_period_header,
                            "Currency USD",
                            transaction_with_endpoint_word,
                        ]
                    ),
                )
            ],
            2025,
            "one-account",
            root / "display-periods.json",
            root / "display-periods-review.csv",
        )
        display_artifact_periods = display_artifact["statement_files"][0].get("detected_periods", [])  # type: ignore[index]
        if display_artifact_periods != [display_period_header]:
            failures.append(
                f"display-periods: artifacts must retain only the statement heading, got {display_artifact_periods}"
            )
        write_json(root / "display-periods.json", display_artifact)
        write_review_csv(root / "display-periods-review.csv", display_artifact)
        display_json = (root / "display-periods.json").read_text(encoding="utf-8")
        display_csv = (root / "display-periods-review.csv").read_text(encoding="utf-8")
        if transaction_period_sentinel in display_json or transaction_period_sentinel in display_csv:
            failures.append("display-periods: transaction narrative must not leak into JSON/CSV review artifacts")

        # Spanish abbreviated months with hyphenated day-month-year labels are
        # common in LATAM statements. They must become source-bound intervals,
        # not merely display-only period hints, so missing quarters can gate.
        spanish_period_lines = [
            "DESDE: 01-Ene-2025",
            "HASTA: 31-Mar-2025",
            "DESDE: 01-Abr-2025",
            "HASTA: 30-Jun-2025",
            "DESDE: 01-Jul-2025",
            "HASTA: 30-Sep-2025",
            "DESDE: 01-Oct-2025",
            "HASTA: 31-Dic-2025",
        ]
        if not detect_periods(spanish_period_lines):
            failures.append("spanish-abbrev-period: abbreviated Spanish dates must be detected as periods")
        spanish_intervals = detect_period_intervals([{"page": 1, "lines": spanish_period_lines}])
        expected_spanish_intervals = [
            ("2025-01-01", "2025-03-31"),
            ("2025-04-01", "2025-06-30"),
            ("2025-07-01", "2025-09-30"),
            ("2025-10-01", "2025-12-31"),
        ]
        if [(item["start"], item["end"]) for item in spanish_intervals] != expected_spanish_intervals:
            failures.append(f"spanish-abbrev-period: unexpected source-bound intervals {spanish_intervals}")
        if not all(
            isinstance(item.get("end_source_ref"), dict) and item["end_source_ref"].get("page") == 1
            for item in spanish_intervals
        ):
            failures.append(f"spanish-abbrev-period: split ranges must retain end source references {spanish_intervals}")
        if len(spanish_intervals) == 4:
            spanish_missing_q2 = period_coverage_review(
                [spanish_intervals[0], spanish_intervals[2], spanish_intervals[3]], 2025
            )
            spanish_missing_q4 = period_coverage_review(spanish_intervals[:3], 2025)
            if not spanish_missing_q2.get("calendar_gaps"):
                failures.append("spanish-abbrev-period: omitted Q2 must produce a coverage gap")
            if not spanish_missing_q4.get("calendar_gaps"):
                failures.append("spanish-abbrev-period: omitted Q4 must produce a coverage gap")

        # A common Spanish monthly label uses two year-first slash dates joined
        # by ``a``. It must retain a one-line source reference and make a
        # May-through-December set visibly incomplete for the requested year.
        spanish_slash_period_lines = [
            "Estado de cuenta para el período de: 2025/05/01 a 2025/05/31",
            "Estado de cuenta para el período de: 2025/06/01 a 2025/06/30",
            "Estado de cuenta para el período de: 2025/07/01 a 2025/07/31",
            "Estado de cuenta para el período de: 2025/08/01 a 2025/08/31",
            "Estado de cuenta para el período de: 2025/09/01 a 2025/09/30",
            "Estado de cuenta para el período de: 2025/10/01 a 2025/10/31",
            "Estado de cuenta para el período de: 2025/11/01 a 2025/11/30",
            "Estado de cuenta para el período de: 2025/12/01 a 2025/12/31",
        ]
        spanish_slash_intervals = detect_period_intervals([{"page": 1, "lines": spanish_slash_period_lines}])
        if len(spanish_slash_intervals) != len(spanish_slash_period_lines):
            failures.append(
                "spanish-slash-period: every labelled monthly 'a' range must become a source-bound interval"
            )
        elif not all(
            item.get("source_ref") == {"page": 1, "line": index}
            and item.get("end_source_ref") == {"page": 1, "line": index}
            for index, item in enumerate(spanish_slash_intervals, start=1)
        ):
            failures.append("spanish-slash-period: one-line ranges must retain both exact source endpoints")
        else:
            spanish_slash_coverage = period_coverage_review(spanish_slash_intervals, 2025)
            if spanish_slash_coverage.get("calendar_gaps") != [
                {"start": "2025-01-01", "end": "2025-04-30"}
            ]:
                failures.append(
                    "spanish-slash-period: May-through-December ranges must flag the leading January-April gap"
                )

        # Source-aware period evidence keeps an opening balance's prior-year
        # date visible, but does not mistake it for a second statement period.
        def quarterly_statement(name: str, start: str, end: str, opening: str = "") -> dict[str, object]:
            lines = [
                "Example Bank Quarterly Statement",
                "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
                f"Statement period {start} to {end}",
                "Currency USD",
            ]
            if opening:
                lines.append(opening)
            return synthetic_file(name, "\n".join(lines))

        q1 = quarterly_statement(
            "q1.pdf", "January 1 2025", "March 31 2025", "Opening balance as of 31/12/2024"
        )
        q2 = quarterly_statement("q2.pdf", "April 1 2025", "June 30 2025")
        q3 = quarterly_statement("q3.pdf", "July 1 2025", "September 30 2025")
        q4 = quarterly_statement("q4.pdf", "October 1 2025", "December 31 2025")
        contextual_year = build_preflight(
            [q1, q2, q3, q4], 2025, "one-account", root / "contextual-year.json", root / "contextual-year-review.csv"
        )
        contextual_coverage = contextual_year["coverage_hints"]  # type: ignore[index]
        contextual_evidence = contextual_coverage.get("contextual_date_evidence", []) if isinstance(contextual_coverage, dict) else []
        if any(gate.get("code") == "mixed-years" for gate in contextual_year["review_gates"]):  # type: ignore[index]
            failures.append("contextual-year: a labelled 2024 opening balance must not trip mixed-years for 2025 periods")
        if not isinstance(contextual_coverage, dict) or contextual_coverage.get("statement_period_years") != [2025]:
            failures.append(f"contextual-year: expected only 2025 statement-period coverage, got {contextual_coverage}")
        if not any(
            isinstance(item, dict)
            and item.get("date") == "2024-12-31"
            and item.get("classification") == "opening-or-prior-balance"
            and isinstance(item.get("source_ref"), dict)
            and item["source_ref"].get("page") == 1
            and item["source_ref"].get("line") == 5
            for item in contextual_evidence
        ):
            failures.append(f"contextual-year: expected source-referenced 2024 opening-balance evidence, got {contextual_evidence}")

        genuine_mixed_period = build_preflight(
            [quarterly_statement("cross-year.pdf", "December 1 2024", "January 31 2025")],
            2025,
            "one-account",
            root / "cross-year.json",
            root / "cross-year-review.csv",
        )
        if not any(gate.get("code") == "mixed-years" for gate in genuine_mixed_period["review_gates"]):  # type: ignore[index]
            failures.append("contextual-year: a genuine 2024-2025 statement period must still trip mixed-years")

        reordered_complete_periods = build_preflight(
            [q4, q2, q3, q1],
            2025,
            "one-account",
            root / "reordered-periods.json",
            root / "reordered-periods-review.csv",
        )
        reordered_coverage = reordered_complete_periods.get("coverage_hints", {})  # type: ignore[union-attr]
        reordered_gaps = (
            reordered_coverage.get("period_coverage_review", {}).get("calendar_gaps", [])
            if isinstance(reordered_coverage, dict)
            else []
        )
        if reordered_gaps or any(
            gate.get("code") == "possible-missing-statement-period"
            for gate in reordered_complete_periods["review_gates"]  # type: ignore[index]
        ):
            failures.append(
                f"reordered-periods: complete source periods must not create a coverage gap, got {reordered_gaps}"
            )

        expected_missing_period_gaps = {
            "missing-q2": [{"start": "2025-04-01", "end": "2025-06-30"}],
            "missing-q4": [{"start": "2025-10-01", "end": "2025-12-31"}],
        }
        for label, files in (("missing-q2", [q1, q3, q4]), ("missing-q4", [q1, q2, q3])):
            missing_period = build_preflight(
                files, 2025, "one-account", root / f"{label}.json", root / f"{label}-review.csv"
            )
            missing_coverage = missing_period.get("coverage_hints", {})  # type: ignore[union-attr]
            missing_review = (
                missing_coverage.get("period_coverage_review", {})
                if isinstance(missing_coverage, dict)
                else {}
            )
            missing_gaps = missing_review.get("calendar_gaps", []) if isinstance(missing_review, dict) else []
            gate_messages = [
                str(gate.get("message", ""))
                for gate in missing_period["review_gates"]  # type: ignore[index]
                if gate.get("code") == "possible-missing-statement-period"
            ]
            expected_gaps = expected_missing_period_gaps[label]
            expected_range = f"{expected_gaps[0]['start']} through {expected_gaps[0]['end']}"
            if (
                missing_gaps != expected_gaps
                or missing_review.get("intervals_detected") != 3
                or not gate_messages
                or expected_range not in gate_messages[0]
            ):
                failures.append(
                    f"{label}: expected exact coverage gap {expected_gaps}, got {missing_review} gates={gate_messages}"
                )

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

        # A review-required preflight becomes usable downstream only through a
        # separate, user-confirmed handoff that acknowledges every review gate.
        reviewed_source = root / "review-required.json"
        write_json(reviewed_source, mixed_currency)
        reviewed_handoff = root / "reviewed-handoff.json"
        handoff_args = argparse.Namespace(
            input=str(reviewed_source),
            out=str(reviewed_handoff),
            accept_gate=["mixed-currencies"],
            user_review_confirmed=True,
        )
        if command_review_handoff(handoff_args) != 0:
            failures.append("review-handoff: expected reviewed preflight to produce a handoff")
        handoff_data = load_json_artifact(reviewed_handoff, "reviewed handoff")
        if handoff_data.get("status") != REVIEWED_HANDOFF_STATUS:
            failures.append("review-handoff: expected reviewed-for-domain-extraction status")
        review = handoff_data.get("review")
        if not isinstance(review, dict) or review.get("accepted_gate_codes") != ["mixed-currencies"]:
            failures.append("review-handoff: expected accepted gate record")
        source_meta = handoff_data.get("source_preflight")
        if not isinstance(source_meta, dict) or source_meta.get("sha256") != file_sha256(reviewed_source):
            failures.append("review-handoff: expected source preflight digest")
        if handoff_data.get("coverage_hints") != mixed_currency.get("coverage_hints"):
            failures.append("review-handoff: expected coverage hints to remain visible downstream")

        # Structured reviewer resolutions are separate from the immutable source
        # preflight. This covers a 2025 period carrying unclassified 2024 date
        # evidence, a bare dollar sign, and no account hint.
        resolution_preflight = build_preflight(
            [
                synthetic_file(
                    "resolution.pdf",
                    "Example Bank Statement\nStatement period January 1 2025 to March 31 2025\n"
                    "Historic reference 31/12/2024\nClosing balance $100.00",
                )
            ],
            2025,
            "one-account",
            root / "resolution.json",
            root / "resolution-review.csv",
        )
        resolution_source = root / "resolution-source.json"
        write_json(resolution_source, resolution_preflight)
        resolution_before = load_json_artifact(resolution_source, "resolution source")
        resolution_codes = [str(gate.get("code")) for gate in resolution_preflight["review_gates"]]  # type: ignore[index]
        resolution_out = root / "resolution-handoff.json"
        resolution_args = argparse.Namespace(
            input=str(resolution_source),
            out=str(resolution_out),
            accept_gate=resolution_codes,
            user_review_confirmed=True,
            confirm_statement_year=[2025],
            classify_contextual_year=[2024],
            confirm_currency="cop",
            confirm_one_account=True,
        )
        if command_review_handoff(resolution_args) != 0:
            failures.append("review-handoff-resolutions: expected valid structured reviewer resolutions")
        if load_json_artifact(resolution_source, "resolution source") != resolution_before:
            failures.append("review-handoff-resolutions: source preflight must remain unchanged")
        resolved_data = load_json_artifact(resolution_out, "resolved handoff")
        resolutions = resolved_data.get("user_resolutions")
        if not isinstance(resolutions, dict):
            failures.append("review-handoff-resolutions: expected user_resolutions object")
        else:
            statement_resolution = resolutions.get("statement_years")
            currency_resolution = resolutions.get("currency")
            account_resolution = resolutions.get("one_account")
            if not isinstance(statement_resolution, dict) or statement_resolution.get("confirmed_years") != [2025]:
                failures.append(f"review-handoff-resolutions: expected confirmed 2025 statement year, got {statement_resolution}")
            if not isinstance(currency_resolution, dict) or currency_resolution.get("code") != "COP":
                failures.append(f"review-handoff-resolutions: expected COP confirmation, got {currency_resolution}")
            if not isinstance(account_resolution, dict) or account_resolution.get("account_identifier_provided") is not False:
                failures.append(f"review-handoff-resolutions: expected identifier-free account confirmation, got {account_resolution}")
            if resolutions.get("source_preflight_sha256") != file_sha256(resolution_source):
                failures.append("review-handoff-resolutions: expected source fingerprint alongside user resolutions")

        for label, overrides in (
            ("missing currency", {"confirm_currency": None}),
            ("invalid currency", {"confirm_currency": "USDX"}),
            ("wrong statement year", {"confirm_statement_year": [2024]}),
            ("unknown contextual year", {"classify_contextual_year": [2023]}),
        ):
            values = {
                "input": str(resolution_source),
                "out": str(root / f"resolution-{label}.json"),
                "accept_gate": resolution_codes,
                "user_review_confirmed": True,
                "confirm_statement_year": [2025],
                "classify_contextual_year": [2024],
                "confirm_currency": "COP",
                "confirm_one_account": True,
            }
            values.update(overrides)
            try:
                command_review_handoff(argparse.Namespace(**values))
                failures.append(f"review-handoff-resolutions: expected {label} to be rejected")
            except PreflightError:
                pass

        mixed_year_source = root / "mixed-year-resolution.json"
        write_json(mixed_year_source, mixed_year)
        mixed_year_codes = [str(gate.get("code")) for gate in mixed_year["review_gates"]]  # type: ignore[index]
        try:
            command_review_handoff(
                argparse.Namespace(
                    input=str(mixed_year_source),
                    out=str(root / "mixed-year-resolution-handoff.json"),
                    accept_gate=mixed_year_codes,
                    user_review_confirmed=True,
                    confirm_statement_year=[2025],
                    classify_contextual_year=[],
                    confirm_currency=None,
                    confirm_one_account=False,
                )
            )
            failures.append("review-handoff-resolutions: expected genuine mixed-years to require a fresh preflight")
        except PreflightError:
            pass

        for label, handoff_arg in (
            (
                "no user confirmation",
                argparse.Namespace(
                    input=str(reviewed_source),
                    out=str(root / "unconfirmed-handoff.json"),
                    accept_gate=["mixed-currencies"],
                    user_review_confirmed=False,
                ),
            ),
            (
                "incomplete acceptance",
                argparse.Namespace(
                    input=str(reviewed_source),
                    out=str(root / "incomplete-handoff.json"),
                    accept_gate=[],
                    user_review_confirmed=True,
                ),
            ),
        ):
            try:
                command_review_handoff(handoff_arg)
                failures.append(f"review-handoff: expected {label} to be rejected")
            except PreflightError:
                pass

        protected_statement = mixed_currency["statement_files"][0]["resolved_file"]  # type: ignore[index]
        try:
            command_review_handoff(
                argparse.Namespace(
                    input=str(reviewed_source),
                    out=str(protected_statement),
                    accept_gate=["mixed-currencies"],
                    user_review_confirmed=True,
                )
            )
            failures.append("review-handoff: expected input-statement overwrite to be rejected")
        except PreflightError:
            pass

        stop_source = dict(mixed_currency)
        stop_source["review_gates"] = [{"code": "low-text-pdf", "severity": "stop", "message": "too little text"}]
        stop_source["status"] = REVIEW_REQUIRED_STATUS
        stop_path = root / "stop-preflight.json"
        write_json(stop_path, stop_source)
        try:
            command_review_handoff(
                argparse.Namespace(
                    input=str(stop_path),
                    out=str(root / "stop-handoff.json"),
                    accept_gate=["low-text-pdf"],
                    user_review_confirmed=True,
                )
            )
            failures.append("review-handoff: expected structural stop gate to be rejected")
        except PreflightError:
            pass

        write_json(root / "clean.json", clean)
        write_review_csv(root / "clean-review.csv", clean)
        if not (root / "clean-review.csv").exists():
            failures.append("review-csv: expected review CSV to be written")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Self-test passed: deterministic preflight coverage")
    return 0


def _year_arg(raw: str) -> int:
    try:
        year = int(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"year {raw!r} is not an integer") from None
    if not (MIN_TAX_YEAR <= year <= MAX_TAX_YEAR):
        raise argparse.ArgumentTypeError(f"year {year} is outside the supported range {MIN_TAX_YEAR}-{MAX_TAX_YEAR}")
    return year


def _iso_date_arg(raw: str) -> str:
    try:
        return date.fromisoformat(raw).isoformat()
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"date {raw!r} must use YYYY-MM-DD.") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Preflight one statement PDF set.")
    preflight.add_argument("--pdf", nargs="+", required=True, help="Statement PDFs for one tax year and one scope.")
    preflight.add_argument("--tax-year", type=_year_arg, required=True, help=f"Calendar/tax year to verify ({MIN_TAX_YEAR}-{MAX_TAX_YEAR}).")
    preflight.add_argument("--scope", choices=sorted(SUPPORTED_SCOPES), required=True, help="Expected downstream scope.")
    preflight.add_argument(
        "--require-institution",
        action="store_true",
        help="With one-account scope, require source-bound institution evidence or a reviewed typed institution confirmation.",
    )
    preflight.add_argument("--out", required=True, help="Output preflight JSON path.")
    preflight.add_argument("--csv", help="Optional review CSV path.")
    preflight.add_argument(
        "--exit-nonzero-on-review",
        action="store_true",
        help=f"Exit {REVIEW_EXIT_CODE} (instead of 0) when the result is review-required, for scripted callers.",
    )
    preflight.set_defaults(func=command_preflight)

    handoff = subparsers.add_parser(
        "review-handoff",
        help="Create a reviewed handoff for a preflight with non-structural review gates.",
    )
    handoff.add_argument("--input", required=True, help="review-required preflight JSON to preserve and acknowledge.")
    handoff.add_argument("--out", required=True, help="Output reviewed-handoff JSON path.")
    handoff.add_argument(
        "--accept-gate",
        action="append",
        required=True,
        help="One review gate code explicitly confirmed by the user; repeat for every gate.",
    )
    handoff.add_argument(
        "--user-review-confirmed",
        action="store_true",
        help="Required after the user reviewed every listed non-structural preflight gate.",
    )
    handoff.add_argument(
        "--confirm-statement-year",
        action="append",
        type=_year_arg,
        help="Confirm the requested statement year after review; repeat only if the handoff requests it.",
    )
    handoff.add_argument(
        "--classify-contextual-year",
        action="append",
        type=_year_arg,
        help="Classify one extracted prior year as contextual after confirming the requested statement year.",
    )
    handoff.add_argument(
        "--confirm-generated-on-date",
        action="append",
        metavar="YYYY-MM-DD",
        type=_iso_date_arg,
        help="Confirm one extracted out-of-period generated-on date; repeat only for dates shown in the source preflight.",
    )
    handoff.add_argument(
        "--confirm-currency",
        metavar="ISO",
        help="Confirm one ISO 4217 currency only when preflight reports ambiguous-dollar or unknown-currency.",
    )
    handoff.add_argument(
        "--confirm-one-account",
        action="store_true",
        help="Confirm the supplied PDFs represent one account without recording an account number.",
    )
    handoff.add_argument(
        "--confirm-institution",
        metavar="NAME",
        help="Confirm one institution name only for an allowed institution review gate.",
    )
    handoff.set_defaults(func=command_review_handoff)

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
