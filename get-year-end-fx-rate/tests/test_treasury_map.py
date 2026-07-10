#!/usr/bin/env python3
"""Regression checks for Chunk 3 Treasury map completeness."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import get_year_end_fx_rate as fx  # noqa: E402


def main() -> None:
    fixture = PACKAGE_ROOT / "tests" / "fixtures" / "treasury-2025-12-31.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert len(payload["data"]) == 174

    map_args = argparse.Namespace(
        year=2025,
        currency=None,
        api_url=fx.TREASURY_API_URL,
        api_file=str(fixture),
        strict=True,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        assert fx.command_map_check(map_args) == 0

    thb = fx.find_treasury_rate(
        "THB", 2025, fixture.read_text(encoding="utf-8"), fx.treasury_query_url("THB", 2025, fx.TREASURY_API_URL)
    )
    assert thb.rate == Decimal("31.66")
    assert thb.country == "Thailand"

    assert fx.labels_match("Rep. of N. Macedonia", "Republic of North Macedonia")
    assert fx.labels_match("New Lira", "Lira")
    assert not fx.labels_match("Guinea", "Equatorial Guinea")
    assert not fx.labels_match("Franc", "CFA Franc")
    assert not fx.labels_match("Rial", "Riyal")

    equatorial = next(
        row for row in payload["data"] if row["country"] == "Equatorial Guinea"
    )
    assert fx.mapped_treasury_code(equatorial) == "XAF"
    guinea = next(row for row in payload["data"] if row["country"] == "Guinea")
    assert fx.mapped_treasury_code(guinea) == "GNF"

    with tempfile.TemporaryDirectory() as tmp:
        lookup_args = argparse.Namespace(
            currency="THB",
            year=2025,
            output_root=str(Path(tmp) / "proof"),
            api_url=fx.TREASURY_API_URL,
            api_file=str(fixture),
            retrieved="2026-07-09",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert fx.command_lookup(lookup_args) == 0
        assert "Rate: 1 USD = 31.66 THB year-end (2025-12-31)" in output.getvalue()

    try:
        fx.treasury_query_url("XQZ", 2025, fx.TREASURY_API_URL)
    except fx.RateError as exc:
        assert exc.code == 4
        assert "map-maintenance issue" in str(exc)
    else:
        raise AssertionError("unknown Treasury code should require map maintenance")

    print("Treasury map completeness passed")


if __name__ == "__main__":
    main()
