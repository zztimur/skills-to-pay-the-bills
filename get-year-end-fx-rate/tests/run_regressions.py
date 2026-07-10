#!/usr/bin/env python3
"""Run get-year-end-fx-rate script-style regression checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = PACKAGE_ROOT / "tests"

REGRESSION_SCRIPTS = (
    "test_manual_input_hardening.py",
    "test_manual_policy_hardening.py",
    "test_treasury_map.py",
    "test_treasury_offline_provenance.py",
    "test_year_end_availability.py",
)


def main() -> int:
    for script_name in REGRESSION_SCRIPTS:
        script_path = TEST_DIR / script_name
        if not script_path.is_file():
            print(f"missing regression script: {script_path}", file=sys.stderr)
            return 1
        print(f"running {script_name}")
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=PACKAGE_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            print(f"{script_name} failed with exit {result.returncode}", file=sys.stderr)
            return result.returncode
    print("all get-year-end-fx-rate regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
