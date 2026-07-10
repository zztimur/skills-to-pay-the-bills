#!/usr/bin/env python3
"""Freeze the Chunk 0 audit observations before hardening.

This diagnostic is intentionally expected to stop passing as later chunks fix
the documented defects. It is not part of the skill's normal self-test.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import get_year_end_fx_rate as fx  # noqa: E402


def run_main(arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = fx.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def main() -> None:
    fixture = PACKAGE_ROOT / "tests" / "fixtures" / "treasury-2025-12-31-thb.json"

    code, _stdout, stderr = run_main(
        ["lookup", "--currency", "THB", "--year", "2025", "--api-file", str(fixture)]
    )
    assert code == 4, (code, stderr)
    assert "No Treasury/Fiscal Data row mapping is known for THB" in stderr

    code, _stdout, _stderr = run_main(
        ["map-check", "--year", "2025", "--currency", "THB", "--api-file", str(fixture), "--strict"]
    )
    assert code == 4

    assert fx.parse_decimal("1.234,56") == Decimal("1.23456")
    fx.reject_average_language("Central Bank 2025 Average")

    with tempfile.TemporaryDirectory() as tmp:
        output_root = Path(tmp) / "proof"
        code, stdout, stderr = run_main(
            [
                "manual",
                "--currency",
                "COP",
                "--year",
                "2025",
                "--rate",
                "3900",
                "--source-title",
                "Example Central Bank Year-End Rate",
                "--source-url",
                "https://example.test/year-end",
                "--source-note",
                "Source labels this as the 2025-12-31 rate.",
                "--year-end-confirmed",
                "--retrieved",
                "not-a-date",
                "--output-root",
                str(output_root),
            ]
        )
        assert code == 0, (code, stdout, stderr)
        assert "Caveat:" not in stdout
        workpaper_path = next(output_root.glob("cop-2025-*/workpaper.json"))
        workpaper = json.loads(workpaper_path.read_text(encoding="utf-8"))
        assert workpaper["source"]["retrieved"] == "not-a-date"
        assert workpaper["proof"]["limitations"] == [
            "No saved source proof file was supplied for this manual workpaper."
        ]

    print("pre-hardening baseline reproduced")


if __name__ == "__main__":
    main()
