#!/usr/bin/env python3
"""Prove normal CLI execution does not mutate a read-only installed tree."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run(command: list[str], cwd: Path) -> None:
    process = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if process.returncode != 0:
        raise AssertionError(
            f"command failed ({process.returncode}): {' '.join(command)}\n{process.stdout}\n{process.stderr}"
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fx-read-only-") as temporary:
        root = Path(temporary)
        installed = root / "get-year-end-fx-rate"
        shutil.copytree(PACKAGE_ROOT, installed)
        script = installed / "scripts" / "get_year_end_fx_rate.py"
        api_file = installed / "tests" / "fixtures" / "treasury-2025-12-31.json"
        before = tree_digest(installed)
        for path in sorted(installed.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(installed, 0o555)

        run([sys.executable, "-B", str(script), "self-test"], root)
        run([sys.executable, "-B", str(script), "map-check", "--year", "2025", "--api-file", str(api_file), "--strict"], root)
        run([
            sys.executable, "-B", str(script), "lookup", "--currency", "COP", "--year", "2025",
            "--api-file", str(api_file), "--output-root", str(root / "lookup-output"),
        ], root)
        run([
            sys.executable, "-B", str(script), "manual", "--currency", "COP", "--year", "2025",
            "--rate", "3900", "--rate-direction", "foreign-per-usd", "--source-title", "Synthetic year-end source",
            "--source-url", "https://example.gov/synthetic-year-end", "--source-note",
            "Synthetic source explicitly labels the rate as December 31 year-end support.",
            "--year-end-confirmed", "--proof-file", str(api_file), "--output-root", str(root / "manual-output"),
        ], root)
        after = tree_digest(installed)
        if before != after:
            raise AssertionError("read-only installed tree digest changed after normal CLI execution")
    print("runtime immutability passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
