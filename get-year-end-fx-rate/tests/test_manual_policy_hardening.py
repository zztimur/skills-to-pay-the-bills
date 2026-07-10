#!/usr/bin/env python3
"""Regression checks for Chunk 2 year-end manual-policy hardening."""

from __future__ import annotations

import contextlib
import io
import json
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
    for value in (
        "Central Bank 2025 Average",
        "Central Bank average for 2025",
        "annual rate",
        "annual-average rate",
        "yearly rate",
    ):
        try:
            fx.reject_average_language(value)
        except fx.RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"average source should reject: {value}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proof_file = root / "source.html"
        proof_file.write_text("<p>2025-12-31 rate</p>", encoding="utf-8")
        base = [
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
            "--output-root",
            str(root / "proof"),
        ]

        code, _stdout, stderr = run_main(base)
        assert code == 2
        assert "--proof-file or explain its absence" in stderr

        code, _stdout, stderr = run_main([*base, "--proof-file", str(proof_file), "--no-proof-file-reason", "Not needed"])
        assert code == 2
        assert "either --proof-file or --no-proof-file-reason" in stderr

        code, _stdout, stderr = run_main([*base, "--source-note", ""])
        assert code == 2
        assert "nonempty --source-note" in stderr

        no_proof_root = root / "no-proof"
        code, stdout, stderr = run_main(
            [
                *base[:-2],
                "--output-root",
                str(no_proof_root),
                "--no-proof-file-reason",
                "The source page could not be saved during retrieval.",
            ]
        )
        assert code == 0, (code, stdout, stderr)
        assert "Caveat: No saved source proof file was supplied. Reason: The source page could not be saved during retrieval." in stdout
        workpaper_path = next(no_proof_root.glob("cop-2025-*/workpaper.json"))
        workpaper = json.loads(workpaper_path.read_text(encoding="utf-8"))
        assert workpaper["proof"]["saved_files"] == []
        assert workpaper["proof"]["limitations"] == [
            "No saved source proof file was supplied. Reason: The source page could not be saved during retrieval."
        ]

        with_proof_root = root / "with-proof"
        code, stdout, stderr = run_main(
            [
                *base[:-2],
                "--output-root",
                str(with_proof_root),
                "--proof-file",
                str(proof_file),
            ]
        )
        assert code == 0, (code, stdout, stderr)
        assert "Caveat:" not in stdout
        workpaper_path = next(with_proof_root.glob("cop-2025-*/workpaper.json"))
        workpaper = json.loads(workpaper_path.read_text(encoding="utf-8"))
        assert workpaper["proof"]["saved_files"][0]["filename"] == "source-proof-1.html"

    print("manual-policy hardening passed")


if __name__ == "__main__":
    main()
