#!/usr/bin/env python3
"""Synchronize generated native-plugin copies from canonical repo sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


SKILLS = (
    "statement-intake-preflight",
    "get-year-end-fx-rate",
    "fbar-threshold-check",
)
ASSETS = {
    "docs/assets/social-preview.png": "social-preview.png",
    "docs/assets/demo/fbar-proof-summary-page-1.png": "summary.png",
    "docs/assets/demo/fbar-fx-proof-page-1.png": "fx-proof.png",
}
EXCLUDED_PARTS = {".DS_Store", ".git", "__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
MANIFEST_NAME = "BUNDLE-MANIFEST.json"


def file_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    return "sha256:" + ":".join(digest[index : index + 8] for index in range(0, len(digest), 8))


def source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"bundle sources may not be symlinks: {path}")
        if path.is_file() and path.suffix not in EXCLUDED_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def desired_bundle(repo_root: Path) -> dict[str, bytes]:
    desired: dict[str, bytes] = {}
    for skill in SKILLS:
        canonical = repo_root / skill
        if not (canonical / "SKILL.md").is_file():
            raise FileNotFoundError(f"canonical skill is missing SKILL.md: {canonical}")
        for source in source_files(canonical):
            relative = source.relative_to(canonical).as_posix()
            desired[f"skills/{skill}/{relative}"] = source.read_bytes()
    for source_name, destination_name in ASSETS.items():
        source = repo_root / source_name
        if not source.is_file():
            raise FileNotFoundError(f"canonical plugin asset is missing: {source}")
        desired[f"assets/{destination_name}"] = source.read_bytes()
    return desired


def manifest_bytes(desired: dict[str, bytes]) -> bytes:
    manifest = {
        "schema_version": 1,
        "generated": True,
        "canonical_skill_roots": list(SKILLS),
        "files": {path: file_hash(data) for path, data in sorted(desired.items())},
    }
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def bundle_files(plugin_root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for child_name in ("skills", "assets"):
        child = plugin_root / child_name
        if not child.exists():
            continue
        for path in source_files(child):
            found[path.relative_to(plugin_root).as_posix()] = path.read_bytes()
    manifest = plugin_root / MANIFEST_NAME
    if manifest.is_file():
        found[MANIFEST_NAME] = manifest.read_bytes()
    return found


def expected_bundle(repo_root: Path) -> dict[str, bytes]:
    desired = desired_bundle(repo_root)
    return {**desired, MANIFEST_NAME: manifest_bytes(desired)}


def check(repo_root: Path, plugin_root: Path) -> None:
    expected = expected_bundle(repo_root)
    actual = bundle_files(plugin_root)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(path for path in set(expected) & set(actual) if expected[path] != actual[path])
    if missing or extra or changed:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"extra: {', '.join(extra)}")
        if changed:
            details.append(f"changed: {', '.join(changed)}")
        raise ValueError("plugin bundle drift detected; " + "; ".join(details))


def sync(repo_root: Path, plugin_root: Path) -> None:
    expected = expected_bundle(repo_root)
    for child_name in ("skills", "assets"):
        target = plugin_root / child_name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    manifest = plugin_root / MANIFEST_NAME
    if manifest.exists():
        manifest.unlink()
    for relative, data in sorted(expected.items()):
        destination = plugin_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated copies drift")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    plugin_root = repo_root / "plugins" / "fbar-proof-kit"
    if not plugin_root.is_dir():
        raise FileNotFoundError(f"plugin root is missing: {plugin_root}")
    if args.check:
        check(repo_root, plugin_root)
        print("fbar-proof-kit bundle is synchronized")
    else:
        sync(repo_root, plugin_root)
        check(repo_root, plugin_root)
        print("fbar-proof-kit bundle synchronized from 3 canonical skills")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"sync-plugin-bundles: {exc}", file=sys.stderr)
        raise SystemExit(1)
