#!/usr/bin/env python3
"""Regression checks for refusing unopened year-end dates."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import sys
import tempfile
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
    assert fx.ensure_year_end_has_occurred(2025, dt.date(2026, 1, 1)) == "2025-12-31"
    assert fx.ensure_year_end_has_occurred(2026, dt.date(2027, 1, 1)) == "2026-12-31"
    for year in (2026, 2027):
        try:
            fx.ensure_year_end_has_occurred(year, dt.date(2026, 7, 10))
        except fx.RateError as exc:
            assert exc.code == 2
            assert "has not occurred yet" in str(exc)
            assert f"{year}-12-31" in str(exc)
        else:
            raise AssertionError(f"{year} should be unavailable on 2026-07-10")

    original_today_date = fx.today_date
    fx.today_date = lambda: dt.date(2026, 7, 10)
    try:
        assert fx.resolve_retrieval_date("2025-12-31") == "2025-12-31"
        assert fx.resolve_retrieval_date("2026-07-10") == "2026-07-10"
        assert fx.resolve_retrieval_date(None) == "2026-07-10"
        try:
            fx.resolve_retrieval_date("2026-07-11")
        except fx.RateError as exc:
            assert exc.code == 2
            assert "cannot be later than today" in str(exc)
        else:
            raise AssertionError("future retrieval date should fail")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proof_file = root / "source.html"
            proof_file.write_text("<p>Future year-end source claim</p>", encoding="utf-8")

            manual_base = [
                "manual",
                "--currency",
                "COP",
                "--rate",
                "3900",
                "--source-title",
                "Example Central Bank Year-End Rate",
                "--source-url",
                "https://example.test/year-end",
                "--source-note",
                "Source labels this as the requested 12-31 rate.",
                "--year-end-confirmed",
                "--output-root",
                str(root / "manual-proof"),
            ]

            for future_year in ("2026", "2027"):
                code, stdout, stderr = run_main(
                    [*manual_base, "--year", future_year, "--proof-file", str(proof_file)]
                )
                assert code == 2, (code, stdout, stderr)
                assert f"{future_year}-12-31" in stderr
                assert "has not occurred yet" in stderr
                assert not (root / "manual-proof").exists()

            code, stdout, stderr = run_main(
                [
                    *manual_base,
                    "--year",
                    "2027",
                    "--no-proof-file-reason",
                    "The source page could not be saved during retrieval.",
                ]
            )
            assert code == 2, (code, stdout, stderr)
            assert "2027-12-31" in stderr
            assert "has not occurred yet" in stderr
            assert not (root / "manual-proof").exists()

            future_retrieved_root = root / "future-retrieved-manual-proof"
            code, stdout, stderr = run_main(
                [
                    *manual_base,
                    "--year",
                    "2025",
                    "--proof-file",
                    str(proof_file),
                    "--retrieved",
                    "2099-01-01",
                    "--output-root",
                    str(future_retrieved_root),
                ]
            )
            assert code == 2, (code, stdout, stderr)
            assert "Retrieval date 2099-01-01 cannot be later than today" in stderr
            assert not future_retrieved_root.exists()

            valid_retrieved_root = root / "valid-retrieved-manual-proof"
            code, stdout, stderr = run_main(
                [
                    *manual_base,
                    "--year",
                    "2025",
                    "--proof-file",
                    str(proof_file),
                    "--retrieved",
                    "2026-07-10",
                    "--output-root",
                    str(valid_retrieved_root),
                ]
            )
            assert code == 0, (code, stdout, stderr)
            assert valid_retrieved_root.exists()

            fixture = PACKAGE_ROOT / "tests" / "fixtures" / "treasury-2025-12-31.json"
            lookup_root = root / "lookup-proof"
            code, stdout, stderr = run_main(
                [
                    "lookup",
                    "--currency",
                    "THB",
                    "--year",
                    "2026",
                    "--api-file",
                    str(fixture),
                    "--output-root",
                    str(lookup_root),
                ]
            )
            assert code == 2, (code, stdout, stderr)
            assert "2026-12-31" in stderr
            assert "has not occurred yet" in stderr
            assert not lookup_root.exists()

            future_retrieved_lookup_root = root / "future-retrieved-lookup-proof"
            code, stdout, stderr = run_main(
                [
                    "lookup",
                    "--currency",
                    "THB",
                    "--year",
                    "2025",
                    "--api-file",
                    str(fixture),
                    "--retrieved",
                    "2099-01-01",
                    "--output-root",
                    str(future_retrieved_lookup_root),
                ]
            )
            assert code == 2, (code, stdout, stderr)
            assert "Retrieval date 2099-01-01 cannot be later than today" in stderr
            assert not future_retrieved_lookup_root.exists()
    finally:
        fx.today_date = original_today_date

    print("year-end availability guard passed")


if __name__ == "__main__":
    main()
