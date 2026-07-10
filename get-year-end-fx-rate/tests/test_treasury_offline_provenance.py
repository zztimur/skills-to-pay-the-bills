#!/usr/bin/env python3
"""Regression checks for Chunk 4 offline Treasury reproducibility."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import io
import json
import sys
import tempfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import get_year_end_fx_rate as fx  # noqa: E402


def main() -> None:
    fixture = PACKAGE_ROOT / "tests" / "fixtures" / "treasury-2025-12-31.json"
    query_url = fx.treasury_query_url("THB", 2025, fx.TREASURY_API_URL)

    original_urlopen = fx.urlopen
    try:
        def closed_connection(*_args: object, **_kwargs: object) -> object:
            raise http.client.RemoteDisconnected("Treasury closed the connection")

        fx.urlopen = closed_connection
        fx.load_json_text(query_url, None)
    except fx.RateError as exc:
        assert exc.code == 5
        assert "--api-file" in str(exc)
        assert query_url in str(exc)
    else:
        raise AssertionError("RemoteDisconnected should become a retryable RateError")
    finally:
        fx.urlopen = original_urlopen

    with tempfile.TemporaryDirectory() as tmp:
        output_root = Path(tmp) / "proof"
        args = argparse.Namespace(
            currency="THB",
            year=2025,
            output_root=str(output_root),
            api_url=fx.TREASURY_API_URL,
            api_file=str(fixture),
            retrieved="2026-07-09",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert fx.command_lookup(args) == 0
        packet = next(output_root.glob("thb-2025-*/workpaper.json"))
        data = json.loads(packet.read_text(encoding="utf-8"))
        fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
        assert "Snapshot origin: supplied local JSON file" in data["source"]["note"]
        assert fixture_hash in data["source"]["note"]
        assert data["treasury_record"]["snapshot_origin"] == "supplied local JSON file"
        assert data["treasury_record"]["source_response_reference"] == str(fixture.resolve())
        assert data["proof"]["saved_files"][0]["sha256"] == fixture_hash

    print("Treasury offline provenance passed")


if __name__ == "__main__":
    main()
