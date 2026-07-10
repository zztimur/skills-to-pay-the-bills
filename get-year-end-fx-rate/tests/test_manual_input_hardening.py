#!/usr/bin/env python3
"""Regression checks for Chunk 1 manual-input hardening."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import get_year_end_fx_rate as fx  # noqa: E402


def run_main(arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = fx.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def main() -> None:
    for raw, expected in (("3900", "3900"), ("3900.00", "3900.00"), ("0.00025", "0.00025")):
        assert fx.parse_user_rate(raw) == Decimal(expected)
    for raw in ("1.234,56", "4,200", "1.2.3", "1e3", "abc"):
        try:
            fx.parse_user_rate(raw)
        except fx.RateError as exc:
            assert exc.code == 3
        else:
            raise AssertionError(f"parse_user_rate should reject {raw!r}")

    assert fx._year_arg("1900") == 1900
    assert fx._year_arg("2025") == 2025
    for raw in ("1899", "2101", "abc"):
        try:
            fx._year_arg(raw)
        except fx.argparse.ArgumentTypeError:
            pass
        else:
            raise AssertionError(f"_year_arg should reject {raw!r}")

    assert fx._iso_date_arg("2026-07-09") == "2026-07-09"
    for raw in ("not-a-date", "2026-13-01", "07/09/2026", "20260709", "2026-7-9"):
        try:
            fx._iso_date_arg(raw)
        except fx.argparse.ArgumentTypeError:
            pass
        else:
            raise AssertionError(f"_iso_date_arg should reject {raw!r}")

    with tempfile.TemporaryDirectory() as tmp:
        proof_file = Path(tmp) / "source.html"
        proof_file.write_text("<p>2025-12-31 rate</p>", encoding="utf-8")
        args = [
            "manual",
            "--currency",
            "XQZ",
            "--year",
            "2025",
            "--rate",
            "3900.00",
            "--source-title",
            "Example Central Bank Year-End Rate",
            "--source-url",
            "https://example.test/year-end",
            "--source-note",
            "Source labels this as the 2025-12-31 rate.",
            "--year-end-confirmed",
            "--proof-file",
            str(proof_file),
            "--output-root",
            str(Path(tmp) / "proof"),
        ]
        code, _stdout, stderr = run_main(args)
        assert code == 2
        assert "--allow-unknown-code" in stderr

        code, stdout, stderr = run_main([*args, "--allow-unknown-code"])
        assert code == 0, (code, stdout, stderr)
        assert "Rate: 1 USD = 3900 XQZ year-end (2025-12-31)" in stdout
        packet = next((Path(tmp) / "proof").glob("xqz-2025-*/"))
        for filename in ("workpaper.md", "workpaper.json", "workpaper.pdf", "source-proof-1.html"):
            assert (packet / filename).is_file(), f"missing artifact: {filename}"
        copied_proof = packet / "source-proof-1.html"
        assert copied_proof.read_text(encoding="utf-8") == proof_file.read_text(encoding="utf-8")
        data = json.loads((packet / "workpaper.json").read_text(encoding="utf-8"))
        assert data["proof"]["saved_files"][0]["filename"] == "source-proof-1.html"
        assert data["proof"]["saved_files"][0]["sha256"] == hashlib.sha256(proof_file.read_bytes()).hexdigest()

        proof_dir = Path(tmp) / "proof-dir"
        proof_dir.mkdir()
        missing_proof = Path(tmp) / "missing-source.html"
        broken_link = Path(tmp) / "broken-source.html"
        broken_link.symlink_to(Path(tmp) / "not-there.html")
        for label, invalid_proof in (
            ("directory", proof_dir),
            ("missing", missing_proof),
            ("broken-link", broken_link),
        ):
            output_root = Path(tmp) / f"{label}-proof"
            code, stdout, stderr = run_main(
                [
                    "manual",
                    "--currency",
                    "COP",
                    "--year",
                    "2025",
                    "--rate",
                    "3900.00",
                    "--source-title",
                    "Example Central Bank Year-End Rate",
                    "--source-url",
                    "https://example.test/year-end",
                    "--source-note",
                    "Source labels this as the 2025-12-31 rate.",
                    "--year-end-confirmed",
                    "--proof-file",
                    str(invalid_proof),
                    "--output-root",
                    str(output_root),
                ]
            )
            assert code == 2, (label, code, stdout, stderr)
            assert f"Proof file does not exist or is not a regular file: {invalid_proof}" in stderr
            assert not output_root.exists(), f"{label} proof should not create a packet folder"

    print("manual-input hardening passed")


if __name__ == "__main__":
    main()
