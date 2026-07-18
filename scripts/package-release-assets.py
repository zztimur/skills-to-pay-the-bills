#!/usr/bin/env python3
"""Build and verify deterministic repository release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path


PUBLIC_SKILLS = (
    "privacy-gate",
    "get-yearly-fx-rate",
    "get-year-end-fx-rate",
    "statement-intake-preflight",
    "fbar-threshold-check",
    "statements-to-interest",
)
PLUGIN = "fbar-proof-kit"
ARCHIVE_PREFIX = "skills-to-pay-the-bills"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
EXCLUDED_NAMES = {
    ".DS_Store",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def included_files(package_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in package_root.rglob("*"):
        relative = path.relative_to(package_root)
        if any(part in EXCLUDED_NAMES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"release packages may not contain symlinks: {path}")
        if path.is_file() and path.suffix not in EXCLUDED_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(package_root).as_posix())


def archive_entry(path: str, data: bytes, *, executable: bool = False) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(path, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o755 if executable else 0o644
    info.external_attr = (0o100000 | mode) << 16
    return info, data


def build_archive(repo_root: Path, package_root: Path, archive_path: Path, package_name: str) -> None:
    source_files = included_files(package_root)
    if not source_files:
        raise ValueError(f"package is empty: {package_root}")

    entries: list[tuple[str, bytes, bool]] = []
    hashes: dict[str, str] = {}
    for source in source_files:
        relative = source.relative_to(package_root).as_posix()
        if relative == "RELEASE-MANIFEST.json":
            raise ValueError(f"source package contains reserved release manifest: {source}")
        archived = f"{package_name}/{relative}"
        data = source.read_bytes()
        executable = bool(source.stat().st_mode & 0o111)
        entries.append((archived, data, executable))
        hashes[relative] = sha256_bytes(data)

    if "LICENSE" not in hashes:
        license_data = (repo_root / "LICENSE").read_bytes()
        entries.append((f"{package_name}/LICENSE", license_data, False))
        hashes["LICENSE"] = sha256_bytes(license_data)

    manifest = {
        "archive_format": 1,
        "package": package_name,
        "source_repository": "https://github.com/zztimur/skills-to-pay-the-bills",
        "files": dict(sorted(hashes.items())),
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    entries.append((f"{package_name}/RELEASE-MANIFEST.json", manifest_data, False))

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for archived, data, executable in sorted(entries, key=lambda item: item[0]):
            info, payload = archive_entry(archived, data, executable=executable)
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def expected_archives(version: str) -> list[str]:
    packages = [*PUBLIC_SKILLS, PLUGIN]
    return [f"{ARCHIVE_PREFIX}-v{version}-{package}.zip" for package in packages]


def build(repo_root: Path, output_dir: Path, version: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob(f"{ARCHIVE_PREFIX}-v*.zip"):
        old.unlink()
    sums_path = output_dir / "SHA256SUMS"
    if sums_path.exists():
        sums_path.unlink()

    roots = [(name, repo_root / name) for name in PUBLIC_SKILLS]
    roots.append((PLUGIN, repo_root / "plugins" / PLUGIN))
    for package_name, package_root in roots:
        if not package_root.is_dir():
            raise FileNotFoundError(f"missing release package: {package_root}")
        filename = f"{ARCHIVE_PREFIX}-v{version}-{package_name}.zip"
        build_archive(repo_root, package_root, output_dir / filename, package_name)

    checksum_lines = [
        f"{sha256_file(output_dir / filename)}  {filename}"
        for filename in expected_archives(version)
    ]
    sums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def verify_archive(path: Path, package_name: str) -> None:
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"corrupt ZIP member in {path.name}: {corrupt}")
        names = archive.namelist()
        prefix = f"{package_name}/"
        if not names or any(not name.startswith(prefix) for name in names):
            raise ValueError(f"{path.name} must contain one {package_name}/ package root")
        if f"{package_name}/SKILL.md" not in names and package_name != PLUGIN:
            raise ValueError(f"{path.name} is missing {package_name}/SKILL.md")
        if package_name == PLUGIN and f"{package_name}/.codex-plugin/plugin.json" not in names:
            raise ValueError(f"{path.name} is missing the Codex plugin manifest")

        manifest_name = f"{package_name}/RELEASE-MANIFEST.json"
        manifest = json.loads(archive.read(manifest_name))
        if manifest.get("package") != package_name:
            raise ValueError(f"{path.name} manifest package mismatch")
        expected_hashes = manifest.get("files")
        if not isinstance(expected_hashes, dict):
            raise ValueError(f"{path.name} manifest files must be an object")
        for relative, expected_hash in expected_hashes.items():
            actual = sha256_bytes(archive.read(f"{package_name}/{relative}"))
            if actual != expected_hash:
                raise ValueError(f"{path.name} content hash mismatch: {relative}")


def verify(output_dir: Path, version: str) -> None:
    sums_path = output_dir / "SHA256SUMS"
    if not sums_path.is_file():
        raise FileNotFoundError(f"missing checksum file: {sums_path}")

    checksum_rows: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        checksum, separator, filename = line.partition("  ")
        if not separator or not checksum or not filename:
            raise ValueError(f"invalid SHA256SUMS row: {line!r}")
        checksum_rows[filename] = checksum

    names = expected_archives(version)
    if sorted(checksum_rows) != sorted(names):
        raise ValueError("SHA256SUMS does not list exactly the expected release archives")
    for filename in names:
        path = output_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"missing release archive: {path}")
        actual = sha256_file(path)
        if actual != checksum_rows[filename]:
            raise ValueError(f"checksum mismatch: {filename}")
        package_name = filename.removeprefix(f"{ARCHIVE_PREFIX}-v{version}-").removesuffix(".zip")
        verify_archive(path, package_name)


def reproducibility_check(repo_root: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="release-assets-") as first_tmp, tempfile.TemporaryDirectory(
        prefix="release-assets-"
    ) as second_tmp:
        first = Path(first_tmp)
        second = Path(second_tmp)
        build(repo_root, first, version)
        build(repo_root, second, version)
        for filename in [*expected_archives(version), "SHA256SUMS"]:
            if (first / filename).read_bytes() != (second / filename).read_bytes():
                raise ValueError(f"release asset is not reproducible: {filename}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="release version; defaults to root VERSION")
    parser.add_argument("--output-dir", default="dist/release", type=Path)
    parser.add_argument("--verify", action="store_true", help="verify existing assets instead of building")
    parser.add_argument("--check-reproducible", action="store_true", help="build twice and compare bytes")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    version = args.version or (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    if not version or any(not part.isdigit() for part in version.split(".")) or len(version.split(".")) != 3:
        raise ValueError(f"version must be numeric X.Y.Z, got: {version!r}")
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir

    if args.check_reproducible:
        reproducibility_check(repo_root, version)
        print(f"release assets reproducible for v{version}")
        return 0
    if args.verify:
        verify(output_dir, version)
        print(f"release assets verified for v{version}: {output_dir}")
        return 0

    build(repo_root, output_dir, version)
    verify(output_dir, version)
    print(f"release assets built for v{version}: {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"package-release-assets: {exc}", file=sys.stderr)
        raise SystemExit(1)
