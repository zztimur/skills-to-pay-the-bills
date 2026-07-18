#!/usr/bin/env python3
"""Reproduce the synthetic FBAR proof demo and its public sample artifacts.

The script drives the repository's real statement-preflight, FBAR, and
year-end-FX CLIs. It does not import or reimplement their parser or calculation
logic. All statement content, identifiers, balances, and institution names are
invented for this demo.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence


DEMO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ.get("FBAR_DEMO_REPO_ROOT", DEMO_ROOT.parents[1])).resolve()
FIXTURE_ROOT = DEMO_ROOT / "fixtures" / "statements"
DEFAULT_ARTIFACT_ROOT = DEMO_ROOT / "artifacts"
DEFAULT_MEDIA_ROOT = REPO_ROOT / "docs" / "assets" / "demo"

PREFLIGHT_SCRIPT = REPO_ROOT / "statement-intake-preflight" / "scripts" / "statement_intake_preflight.py"
FBAR_SCRIPT = REPO_ROOT / "fbar-threshold-check" / "scripts" / "fbar_threshold_check.py"
FX_SCRIPT = REPO_ROOT / "get-year-end-fx-rate" / "scripts" / "get_year_end_fx_rate.py"
TREASURY_FIXTURE = REPO_ROOT / "get-year-end-fx-rate" / "tests" / "fixtures" / "treasury-2025-12-31.json"

TAX_YEAR = 2025
SYNTHETIC_INSTITUTION = "Fictional Orbit Bank - Synthetic Demo Only"
SYNTHETIC_ACCOUNT_ID = "synthetic-demo-account-0042"
EXPECTED_MAXIMUM_NATIVE = Decimal("10500000")
EXPECTED_MAXIMUM_DATE = "2025-10-10"
FIXED_RETRIEVAL_DATE = "2026-07-18"
FX_FOLDER_NAME = "cop-2025-treasury-reporting-rates-of-exchange-fiscal-data"

SUMMARY_PDF_ASSET = "fbar-proof-summary.pdf"
SUMMARY_PNG_ASSET = "fbar-proof-summary-page-1.png"
FX_PNG_ASSET = "fbar-fx-proof-page-1.png"
TERMINAL_GIF_ASSET = "fbar-proof-demo-terminal.gif"
TERMINAL_POSTER_ASSET = "fbar-proof-demo-terminal-poster.png"
CHECKSUM_ASSET = "checksums.sha256"
PUBLISHED_REPO_PREFIX = "${REPO_ROOT}"


class DemoError(RuntimeError):
    """A demo assertion or required command failed."""


def _dependency_ready() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("reportlab", "pdfplumber", "PIL"))


def _bootstrap_runtime() -> None:
    """Relaunch with Codex's bundled artifact runtime when plain Python is lean."""
    if _dependency_ready():
        return
    if os.environ.get("FBAR_DEMO_RUNTIME_BOOTSTRAPPED") == "1":
        raise DemoError("The selected Python runtime is missing reportlab, pdfplumber, or Pillow.")

    candidates = [
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "python"
        / "bin"
        / "python3",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        environment = dict(os.environ)
        environment["FBAR_DEMO_RUNTIME_BOOTSTRAPPED"] = "1"
        os.execve(str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]], environment)

    raise DemoError(
        "This demo needs reportlab, pdfplumber, and Pillow. Install those packages or run it with the "
        "Codex bundled artifact Python runtime."
    )


_bootstrap_runtime()

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402


QUARTERS: tuple[tuple[str, str, str, list[str]], ...] = (
    (
        "q1",
        "2025/01/01",
        "2025/03/31",
        [
            "2025-01-01 09:00:00 SALDO INICIAL 2649001 $0 $9,800,000 $9,800,000",
            "2025-01-04 09:00:00 COMPRA 2649002 $100,000 $0 $9,876,543",
            "2025-01-31 09:00:00 CIERRE 2649003 $25,000 $0 $9,850,000",
            "2025-02-28 09:00:00 CIERRE 2649004 $25,000 $0 $9,875,000",
            "2025-03-31 09:00:00 CIERRE 2649005 $50,000 $0 $9,900,000",
        ],
    ),
    (
        "q2",
        "2025/04/01",
        "2025/06/30",
        [
            "2025-04-01 09:00:00 SALDO INICIAL 2649011 $0 $9,950,000 $9,950,000",
            "2025-04-30 09:00:00 CIERRE 2649012 $20,000 $0 $9,930,000",
            "2025-05-31 09:00:00 CIERRE 2649013 $10,000 $0 $9,940,000",
            "2025-06-15 09:00:00 ABONO 2649014 $0 $25,000 $9,925,000",
            "2025-06-30 09:00:00 CIERRE 2649015 $35,000 $0 $9,960,000",
        ],
    ),
    (
        "q3",
        "2025/07/01",
        "2025/09/30",
        [
            "2025-07-01 09:00:00 SALDO INICIAL 2649021 $0 $9,975,000 $9,975,000",
            "2025-07-31 09:00:00 CIERRE 2649022 $25,000 $0 $10,000,000",
            "2025-08-31 09:00:00 CIERRE 2649023 $100,000 $0 $10,100,000",
            "2025-09-15 09:00:00 ABONO 2649024 $0 $275,000 $10,250,000",
            "2025-09-30 09:00:00 CIERRE 2649025 $50,000 $0 $10,200,000",
        ],
    ),
    (
        "q4",
        "2025/10/01",
        "2025/12/31",
        [
            "2025-10-01 09:00:00 SALDO INICIAL 2649031 $0 $10,300,000 $10,300,000",
            "2025-10-10 09:00:00 AJUSTE 2649032 $50,000 $0 $10,500,000",
            "2025-10-31 09:00:00 CIERRE 2649033 $100,000 $0 $10,400,000",
            "2025-11-30 09:00:00 CIERRE 2649034 $50,000 $0 $10,350,000",
            "2025-12-31 09:00:00 CIERRE 2649035 $250,000 $0 $10,250,000",
        ],
    ),
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"Could not read JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DemoError(f"Expected a JSON object in {path}.")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DemoError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def clean_known_outputs(artifact_root: Path, media_root: Path, *, clean_media: bool) -> None:
    for path in (artifact_root / "happy-path", artifact_root / "refusal-path"):
        if path.exists():
            shutil.rmtree(path)
    paths = [
        DEMO_ROOT / "demo-run.txt",
        DEMO_ROOT / "demo-manifest.json",
        DEMO_ROOT / CHECKSUM_ASSET,
    ]
    if clean_media:
        paths.extend(media_root / name for name in (
            SUMMARY_PDF_ASSET,
            SUMMARY_PNG_ASSET,
            FX_PNG_ASSET,
            TERMINAL_GIF_ASSET,
            TERMINAL_POSTER_ASSET,
            CHECKSUM_ASSET,
        ))
    for path in paths:
        if path.is_file():
            path.unlink()


def make_statement_pdf(path: Path, quarter: str, start: str, end: str, rows: Sequence[str]) -> None:
    """Generate one deterministic, machine-readable, conspicuously synthetic PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = canvas.Canvas(str(path), pagesize=letter, pageCompression=1, invariant=1)
    width, height = letter
    document.setTitle(f"Synthetic FBAR Demo {quarter.upper()}")
    document.setAuthor("Synthetic demo generator")
    document.setCreator("examples/fbar-proof-demo/run_demo.py")

    document.setFillColor(colors.HexColor("#7F1D1D"))
    document.roundRect(36, height - 92, width - 72, 44, 8, stroke=0, fill=1)
    document.setFillColor(colors.white)
    document.setFont("Helvetica-Bold", 14)
    document.drawString(52, height - 68, "SYNTHETIC DEMO ONLY - NOT A REAL STATEMENT")

    y = height - 122
    document.setFillColor(colors.HexColor("#111827"))
    document.setFont("Helvetica-Bold", 11)
    document.drawString(52, y, "Invented data for reproducible software testing")
    y -= 22
    document.setFont("Helvetica", 10)
    lines = [
        "Movimientos de cuenta en COP",
        "CUENTA DE AHORROS",
        "NÚMERO 00000042",  # privacy-gate: allow (conspicuously synthetic demo account ID)
        f"DESDE {start} HASTA {end}",
    ]
    for line in lines:
        document.drawString(52, y, line)
        y -= 18

    y -= 4
    document.setFillColor(colors.HexColor("#1E3A5F"))
    document.setFont("Helvetica-Bold", 8.5)
    document.drawString(52, y, "Fecha Descripción Movimiento Tarjeta Débito Abono Saldo")
    y -= 20
    document.setFillColor(colors.HexColor("#111827"))
    document.setFont("Helvetica", 8.25)
    for row in rows:
        document.drawString(52, y, row)
        y -= 18

    document.setStrokeColor(colors.HexColor("#D1D5DB"))
    document.line(52, 64, width - 52, 64)
    document.setFillColor(colors.HexColor("#6B7280"))
    document.setFont("Helvetica", 8)
    document.drawString(52, 48, f"Fixture {quarter.upper()} - account 00000042 - all names and values are synthetic")
    document.save()


def make_statement_fixtures() -> list[Path]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    expected_names = {f"fictional-orbit-{quarter}.pdf" for quarter, _start, _end, _rows in QUARTERS}
    for existing in FIXTURE_ROOT.glob("*.pdf"):
        if existing.name not in expected_names:
            existing.unlink()

    paths: list[Path] = []
    for quarter, start, end, rows in QUARTERS:
        path = FIXTURE_ROOT / f"fictional-orbit-{quarter}.pdf"
        make_statement_pdf(path, quarter, start, end, rows)
        paths.append(path)
    return paths


def display_command(command: Sequence[str]) -> str:
    display: list[str] = []
    for item in command:
        candidate = Path(item)
        if candidate.is_absolute() and candidate.resolve() == Path(sys.executable).resolve():
            display.append("python3")
            continue
        if candidate.is_absolute() and candidate.name in {"pdftoppm", "pdfinfo"}:
            display.append(candidate.name)
            continue
        if candidate.is_absolute():
            display.append(repo_relative(candidate))
        else:
            display.append(item)
    return shlex.join(display)


def run_command(
    command: Sequence[str],
    raw_transcript: list[str],
    *,
    expected_codes: Iterable[int] = (0,),
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    raw_transcript.append(f"$ {display_command(command)}")
    process = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, env=environment)
    if process.stdout.strip():
        raw_transcript.extend(process.stdout.rstrip().splitlines())
    if process.stderr.strip():
        raw_transcript.extend(f"stderr: {line}" for line in process.stderr.rstrip().splitlines())
    raw_transcript.append(f"[exit {process.returncode}]")
    if process.returncode not in set(expected_codes):
        raise DemoError(
            f"Command exited {process.returncode}, expected {sorted(set(expected_codes))}: "
            f"{display_command(command)}\n{process.stderr.strip()}"
        )
    return process


def gate_codes(data: dict[str, Any]) -> list[str]:
    gates = data.get("review_gates")
    if not isinstance(gates, list):
        return []
    return [str(gate.get("code")) for gate in gates if isinstance(gate, dict) and gate.get("code")]


def create_reviewed_handoff(
    source: Path,
    output: Path,
    source_data: dict[str, Any],
    raw_transcript: list[str],
) -> dict[str, Any]:
    codes = gate_codes(source_data)
    command = [sys.executable, str(PREFLIGHT_SCRIPT), "review-handoff", "--input", str(source)]
    for code in codes:
        command.extend(["--accept-gate", code])
    command.extend(["--user-review-confirmed"])
    if "unknown-institution" in codes or "possible-mixed-institutions" in codes:
        command.extend(["--confirm-institution", SYNTHETIC_INSTITUTION])
    if "unknown-account" in codes or "incomplete-account-linkage" in codes or "possible-mixed-accounts" in codes:
        command.append("--confirm-one-account")
    command.extend(["--out", str(output)])
    run_command(command, raw_transcript)
    data = read_json(output)
    require(data.get("status") == "reviewed-for-domain-extraction", "Reviewed handoff was not accepted.")
    resolutions = data.get("user_resolutions")
    require(isinstance(resolutions, dict), "Reviewed handoff did not retain user_resolutions.")
    review = data.get("review")
    require(
        isinstance(review, dict) and review.get("accepted_gate_codes") == sorted(codes),
        "Reviewed handoff did not preserve every accepted gate code.",
    )
    return data


def observed_rows(account_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = account_data.get("daily_ledger")
    require(isinstance(rows, list), "Extracted account has no daily ledger.")
    return {
        str(row.get("date")): row
        for row in rows
        if isinstance(row, dict) and row.get("balance_source") == "observed"
    }


def find_maximum_native(account_data: dict[str, Any]) -> tuple[str, Decimal]:
    rows = observed_rows(account_data)
    require(rows, "No observed balance rows were extracted.")
    date, row = max(rows.items(), key=lambda item: Decimal(str(item[1].get("native_balance"))))
    return date, Decimal(str(row.get("native_balance")))


def render_first_page(pdf_path: Path, output_path: Path, raw_transcript: list[str]) -> None:
    executable = shutil.which("pdftoppm")
    if not executable:
        raise DemoError("pdftoppm is required to render the committed PDF screenshots.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = output_path.with_suffix("")
    environment = dict(os.environ)
    font_cache = Path(tempfile.gettempdir()) / "synthetic-fbar-demo-fontconfig"
    font_cache.mkdir(parents=True, exist_ok=True)
    environment["XDG_CACHE_HOME"] = str(font_cache)
    system_font_config = Path("/opt/homebrew/etc/fonts/fonts.conf")
    if system_font_config.is_file():
        environment["FONTCONFIG_FILE"] = str(system_font_config)
    run_command(
        [executable, "-f", "1", "-singlefile", "-r", "144", "-png", str(pdf_path), str(prefix)],
        raw_transcript,
        environment=environment,
    )
    require(output_path.is_file() and output_path.stat().st_size > 0, f"Could not render {pdf_path}.")


def terminal_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("/System/Library/Fonts/SFNSMono.ttf"),
        Path("/System/Library/Fonts/Menlo.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def line_color(line: str) -> str:
    lowered = line.casefold()
    if "refused" in lowered or "insufficient" in lowered:
        return "#FCA5A5"
    if "pass" in lowered or "retained" in lowered or "complete" in lowered:
        return "#86EFAC"
    if line.startswith("$"):
        return "#67E8F9"
    if line.startswith("["):
        return "#FCD34D"
    return "#E5E7EB"


def create_terminal_gif(
    lines: Sequence[str],
    output: Path,
    *,
    poster_output: Path | None = None,
    total_seconds: int = 36,
) -> None:
    """Create a 36-second terminal animation from the just-completed run."""
    require(30 <= total_seconds <= 60, "Terminal recording must be between 30 and 60 seconds.")
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1120, 680
    frame_count = 15
    duration_ms = int(total_seconds * 1000 / frame_count)
    font = terminal_font(19)
    small_font = terminal_font(15)
    frames: list[Image.Image] = []
    visible_line_limit = 24

    for frame_index in range(frame_count):
        revealed = max(1, int((frame_index + 1) * len(lines) / frame_count))
        visible = list(lines[:revealed])[-visible_line_limit:]
        frame = Image.new("RGB", (width, height), "#070B14")
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((16, 16, width - 16, height - 16), radius=18, fill="#0B1220", outline="#27364D", width=2)
        draw.rectangle((17, 17, width - 17, 63), fill="#111827")
        for x, color in ((42, "#FB7185"), (68, "#FBBF24"), (94, "#4ADE80")):
            draw.ellipse((x - 7, 33 - 7, x + 7, 33 + 7), fill=color)
        draw.text((128, 23), "synthetic-fbar-demo - actual run", fill="#CBD5E1", font=small_font)
        elapsed = min(total_seconds, round((frame_index + 1) * total_seconds / frame_count))
        draw.text((width - 155, 23), f"00:{elapsed:02d} / 00:{total_seconds:02d}", fill="#64748B", font=small_font)

        y = 82
        for line in visible:
            clipped = line if len(line) <= 102 else line[:99] + "..."
            draw.text((40, y), clipped, fill=line_color(line), font=font)
            y += 24
        if frame_index < frame_count - 1:
            draw.rectangle((40, min(y + 2, height - 40), 52, min(y + 20, height - 22)), fill="#67E8F9")
        frames.append(frame)

    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=[duration_ms] * frame_count,
        loop=0,
        disposal=2,
        optimize=False,
    )
    if poster_output is not None:
        poster_output.parent.mkdir(parents=True, exist_ok=True)
        frames[-1].save(poster_output, format="PNG", optimize=True)


def write_checksums(paths: Iterable[Path], destinations: Sequence[Path]) -> None:
    unique = sorted({path.resolve() for path in paths if path.is_file()}, key=lambda path: repo_relative(path))
    lines = [f"{sha256_file(path)}  {repo_relative(path)}" for path in unique]
    content = "\n".join(lines) + "\n"
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def replace_text_values(paths: Iterable[Path], replacements: dict[str, str]) -> None:
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def sanitize_published_text_artifacts(
    artifact_root: Path,
    preflight_pairs: Sequence[tuple[Path, Path]],
) -> None:
    """Remove workstation paths after the real pipeline and preserve handoff hashes.

    The raw CLIs intentionally retain absolute paths. Public sample text uses a
    clear checkout token instead, after all production contract assertions have
    run. Rebinding the reviewed handoff's source-preflight SHA keeps the nested
    provenance internally consistent with the sanitized source JSON.
    """
    text_paths = [
        path
        for path in artifact_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".csv", ".md", ".txt"}
    ]
    old_preflight_hashes = {preflight: sha256_file(preflight) for preflight, _handoff in preflight_pairs}
    replace_text_values(text_paths, {str(REPO_ROOT.resolve()): PUBLISHED_REPO_PREFIX})

    hash_replacements = {
        old_hash: sha256_file(preflight)
        for preflight, old_hash in old_preflight_hashes.items()
    }
    replace_text_values(text_paths, hash_replacements)

    for preflight, handoff in preflight_pairs:
        expected_hash = sha256_file(preflight)
        data = read_json(handoff)
        source = data.get("source_preflight")
        resolutions = data.get("user_resolutions")
        require(
            isinstance(source, dict) and source.get("sha256") == expected_hash,
            f"Published handoff source hash does not match {preflight}.",
        )
        require(
            isinstance(resolutions, dict) and resolutions.get("source_preflight_sha256") == expected_hash,
            f"Published handoff resolution hash does not match {preflight}.",
        )


def run_demo(artifact_root: Path, media_root: Path, *, skip_media: bool) -> None:
    raw_transcript: list[str] = []
    console_lines: list[str] = []

    def emit(line: str) -> None:
        print(line, flush=True)
        console_lines.append(line)

    clean_known_outputs(artifact_root, media_root, clean_media=not skip_media)
    happy = artifact_root / "happy-path"
    refusal = artifact_root / "refusal-path"
    happy.mkdir(parents=True, exist_ok=True)
    refusal.mkdir(parents=True, exist_ok=True)

    emit("$ examples/fbar-proof-demo/run_demo.py")
    emit("SYNTHETIC FBAR PROOF DEMO - no real people, accounts, or institutions")

    statements = make_statement_fixtures()
    require(len(statements) == 4 and all(path.stat().st_size > 0 for path in statements), "Statement generation failed.")
    emit("[1/9] Generated 4 deterministic quarterly statement PDFs")

    happy_preflight = happy / "statement-preflight.json"
    happy_preflight_csv = happy / "statement-preflight.csv"
    run_command(
        [
            sys.executable,
            str(PREFLIGHT_SCRIPT),
            "preflight",
            "--pdf",
            *(str(path) for path in statements),
            "--tax-year",
            str(TAX_YEAR),
            "--scope",
            "one-account",
            "--require-institution",
            "--out",
            str(happy_preflight),
            "--csv",
            str(happy_preflight_csv),
        ],
        raw_transcript,
    )
    happy_preflight_data = read_json(happy_preflight)
    require(happy_preflight_data.get("status") == "review-required", "Happy preflight should stop for issuer review.")
    require(gate_codes(happy_preflight_data) == ["unknown-institution"], "Unexpected happy-path preflight gates.")
    require(happy_preflight_data.get("currency", {}).get("code") == "COP", "Preflight did not corroborate COP.")
    require(happy_preflight_data.get("account_hints") == ["00000042"], "Preflight did not retain the synthetic account hint.")
    emit("[2/9] Preflight: review-required (unknown-institution only)")

    happy_handoff = happy / "statement-preflight-reviewed.json"
    create_reviewed_handoff(happy_preflight, happy_handoff, happy_preflight_data, raw_transcript)
    emit(f"[3/9] Reviewed handoff: {SYNTHETIC_INSTITUTION}")

    ledger = happy / "account-ledger.json"
    ledger_csv = happy / "account-ledger.csv"
    run_command(
        [
            sys.executable,
            str(FBAR_SCRIPT),
            "extract-account",
            "--pdf",
            *(str(path) for path in statements),
            "--tax-year",
            str(TAX_YEAR),
            "--preflight-json",
            str(happy_handoff),
            "--account-id",
            SYNTHETIC_ACCOUNT_ID,
            "--out",
            str(ledger),
            "--csv",
            str(ledger_csv),
        ],
        raw_transcript,
    )
    ledger_data = read_json(ledger)
    coverage = ledger_data.get("coverage")
    require(isinstance(coverage, dict), "Happy ledger has no coverage card.")
    require(coverage.get("complete_year") is True, "Happy ledger does not cover the complete year.")
    require(coverage.get("missing_days") == 0 and coverage.get("carry_gaps") == [], "Happy ledger has a coverage gap.")
    require(coverage.get("observed_days") == 20, "Happy ledger did not retain all 20 observed fixture dates.")
    maximum_date, maximum_native = find_maximum_native(ledger_data)
    require(
        (maximum_date, maximum_native) == (EXPECTED_MAXIMUM_DATE, EXPECTED_MAXIMUM_NATIVE),
        "Happy ledger maximum did not match the frozen fixture.",
    )
    review_summary = ledger_data.get("review_summary")
    require(isinstance(review_summary, dict), "Happy ledger did not create a review summary.")
    same_day = review_summary.get("same_day_balance_candidates")
    require(isinstance(same_day, dict) and same_day.get("count") == 0, "Happy ledger has conflicting same-day candidates.")
    emit("[4/9] Ledger review: 365 days, 20 observed dates, 0 missing days")
    emit("      Maximum native balance: 10,500,000 COP on 2025-10-10")

    ledger_review = happy / "ledger-review.json"
    write_json(
        ledger_review,
        {
            "demo_only": True,
            "review_basis": "Deterministic assertions against the committed synthetic fixture",
            "reviewed_artifacts": [repo_relative(ledger), repo_relative(ledger_csv)],
            "assertions": {
                "account_id": SYNTHETIC_ACCOUNT_ID,
                "currency": "COP",
                "institution": SYNTHETIC_INSTITUTION,
                "complete_year": True,
                "missing_days": 0,
                "same_day_candidate_count": 0,
                "maximum_native_balance": str(maximum_native),
                "maximum_native_date": maximum_date,
            },
            "production_note": "A real workflow must stop here for the user to review and confirm the ledger.",
        },
    )

    fx_root = happy / "fx-proof"
    run_command(
        [
            sys.executable,
            str(FX_SCRIPT),
            "lookup",
            "--currency",
            "COP",
            "--year",
            str(TAX_YEAR),
            "--retrieved",
            FIXED_RETRIEVAL_DATE,
            "--api-file",
            str(TREASURY_FIXTURE),
            "--output-root",
            str(fx_root),
        ],
        raw_transcript,
    )
    fx_folder = fx_root / FX_FOLDER_NAME
    fx_workpaper = fx_folder / "workpaper.json"
    fx_data = read_json(fx_workpaper)
    require(fx_data.get("skill") == "get-year-end-fx-rate", "FX packet has the wrong skill contract.")
    require(fx_data.get("currency") == "COP" and fx_data.get("year") == TAX_YEAR, "FX packet scope mismatch.")
    require(fx_data.get("year_end_date") == "2025-12-31", "FX packet is not the 2025 year-end record.")
    require(Decimal(str(fx_data.get("foreign_per_usd"))) > 0, "FX packet has no positive foreign-per-USD rate.")
    treasury_record = fx_data.get("treasury_record")
    require(
        isinstance(treasury_record, dict) and treasury_record.get("snapshot_origin") == "supplied local JSON file",
        "FX packet did not retain frozen-source provenance.",
    )
    emit(f"[5/9] Frozen Treasury proof retained: 1 USD = {fx_data['foreign_per_usd']} COP")

    confirmed = happy / "account-confirmed.json"
    confirmed_csv = happy / "account-confirmed.csv"
    run_command(
        [
            sys.executable,
            str(FBAR_SCRIPT),
            "confirm-account",
            "--input",
            str(ledger),
            "--balances-confirmed",
            "--fx-workpaper-json",
            str(fx_workpaper),
            "--out",
            str(confirmed),
            "--csv",
            str(confirmed_csv),
        ],
        raw_transcript,
    )
    confirmed_data = read_json(confirmed)
    require(confirmed_data.get("status") == "confirmed", "Happy ledger was not confirmed.")
    emit("[6/9] Reviewed synthetic ledger confirmed with retained FX dependency")

    summary_json = happy / "fbar-2025-summary.json"
    summary_csv = happy / "fbar-2025-summary.csv"
    summary_pdf = happy / "fbar-2025-summary.pdf"
    run_command(
        [
            sys.executable,
            str(FBAR_SCRIPT),
            "aggregate",
            "--account-ledger",
            str(confirmed),
            "--out",
            str(summary_json),
            "--csv",
            str(summary_csv),
            "--pdf",
            str(summary_pdf),
        ],
        raw_transcript,
    )
    summary_data = read_json(summary_json)
    daily = summary_data.get("daily_threshold")
    maximum_view = summary_data.get("fincen_max_value_view")
    require(isinstance(daily, dict) and daily.get("answer") == "no", "Unexpected happy daily-threshold answer.")
    require(
        isinstance(maximum_view, dict) and maximum_view.get("exceeded") is False,
        "Unexpected happy maximum-value answer.",
    )
    require(summary_csv.is_file() and summary_pdf.is_file(), "Aggregate did not write JSON/CSV/PDF outputs.")
    emit("[7/9] Summary: daily threshold NO; FinCEN maximum-value view NO")

    omitted = statements[1]
    refusal_statements = [statements[0], statements[2], statements[3]]
    refusal_preflight = refusal / "statement-preflight.json"
    refusal_preflight_csv = refusal / "statement-preflight.csv"
    refusal_preflight_process = run_command(
        [
            sys.executable,
            str(PREFLIGHT_SCRIPT),
            "preflight",
            "--pdf",
            *(str(path) for path in refusal_statements),
            "--tax-year",
            str(TAX_YEAR),
            "--scope",
            "one-account",
            "--require-institution",
            "--out",
            str(refusal_preflight),
            "--csv",
            str(refusal_preflight_csv),
            "--exit-nonzero-on-review",
        ],
        raw_transcript,
        expected_codes=(3,),
    )
    refusal_preflight_data = read_json(refusal_preflight)
    refusal_gates = gate_codes(refusal_preflight_data)
    require("possible-missing-statement-period" in refusal_gates, "Omitted Q2 did not trigger the missing-period gate.")
    require("unknown-institution" in refusal_gates, "Refusal preflight did not retain the issuer review gate.")

    refusal_handoff = refusal / "statement-preflight-reviewed.json"
    create_reviewed_handoff(refusal_preflight, refusal_handoff, refusal_preflight_data, raw_transcript)
    refusal_ledger = refusal / "account-ledger.json"
    refusal_ledger_csv = refusal / "account-ledger.csv"
    run_command(
        [
            sys.executable,
            str(FBAR_SCRIPT),
            "extract-account",
            "--pdf",
            *(str(path) for path in refusal_statements),
            "--tax-year",
            str(TAX_YEAR),
            "--preflight-json",
            str(refusal_handoff),
            "--account-id",
            SYNTHETIC_ACCOUNT_ID,
            "--out",
            str(refusal_ledger),
            "--csv",
            str(refusal_ledger_csv),
        ],
        raw_transcript,
    )
    refusal_ledger_data = read_json(refusal_ledger)
    sufficiency = refusal_ledger_data.get("data_sufficiency")
    require(isinstance(sufficiency, dict), "Refusal ledger has no sufficiency card.")
    refusal_daily = sufficiency.get("daily_threshold")
    refusal_maximum = sufficiency.get("maximum_account_value")
    require(
        isinstance(refusal_daily, dict) and refusal_daily.get("answer") == "insufficient-records",
        "Omitted Q2 did not produce insufficient-records.",
    )
    require(
        isinstance(refusal_maximum, dict) and refusal_maximum.get("answer") == "not-determinable",
        "Omitted Q2 incorrectly produced an annual maximum.",
    )
    require("daily_threshold" not in refusal_ledger_data, "Refusal ledger emitted a threshold decision.")
    refusal_coverage = refusal_ledger_data.get("coverage")
    require(isinstance(refusal_coverage, dict), "Refusal ledger has no coverage card.")
    carry_gaps = refusal_coverage.get("carry_gaps")
    require(
        isinstance(carry_gaps, list) and any(int(gap.get("days", 0)) > 40 for gap in carry_gaps if isinstance(gap, dict)),
        "Omitted Q2 did not preserve its long carry-forward gap.",
    )

    refused_output = refusal / "account-confirmed.json"
    refused_process = run_command(
        [
            sys.executable,
            str(FBAR_SCRIPT),
            "confirm-account",
            "--input",
            str(refusal_ledger),
            "--balances-confirmed",
            "--fx-workpaper-json",
            str(fx_workpaper),
            "--out",
            str(refused_output),
        ],
        raw_transcript,
        expected_codes=(2,),
    )
    require(not refused_output.exists(), "Refused confirmation unexpectedly wrote an output ledger.")
    require("carry-forward gap" in refused_process.stderr, "Confirmation failed for an unexpected reason.")
    refusal_record = refusal / "refusal.json"
    write_json(
        refusal_record,
        {
            "demo_only": True,
            "omitted_statement": repo_relative(omitted),
            "preflight_exit_code": refusal_preflight_process.returncode,
            "preflight_gates": refusal_gates,
            "data_sufficiency": sufficiency,
            "confirmation_exit_code": refused_process.returncode,
            "confirmation_written": False,
            "refusal_message": refused_process.stderr.strip(),
            "assertions": {
                "daily_threshold": "insufficient-records",
                "maximum_account_value": "not-determinable",
                "decision_field_absent": True,
                "confirmation_refused_without_accept_carry_forward": True,
            },
        },
    )
    emit("[8/9] Omitted Q2: INSUFFICIENT RECORDS; confirm-account REFUSED")

    manifest = {
        "schema_version": "1.0",
        "demo": "Synthetic FBAR proof workflow",
        "demo_only": True,
        "tax_year": TAX_YEAR,
        "institution": SYNTHETIC_INSTITUTION,
        "account_id": SYNTHETIC_ACCOUNT_ID,
        "statement_files": [repo_relative(path) for path in statements],
        "frozen_treasury_fixture": {
            "path": repo_relative(TREASURY_FIXTURE),
            "sha256": sha256_file(TREASURY_FIXTURE),
        },
        "happy_path": {
            "daily_threshold": daily.get("answer"),
            "fincen_maximum_value_view": "yes" if maximum_view.get("exceeded") else "no",
            "maximum_native_balance": str(maximum_native),
            "maximum_native_date": maximum_date,
            "summary_json": repo_relative(summary_json),
            "summary_csv": repo_relative(summary_csv),
            "summary_pdf": repo_relative(summary_pdf),
            "fx_workpaper": repo_relative(fx_workpaper),
        },
        "refusal_path": {
            "omitted_statement": repo_relative(omitted),
            "daily_threshold": refusal_daily.get("answer"),
            "maximum_account_value": refusal_maximum.get("answer"),
            "confirmation_exit_code": refused_process.returncode,
            "confirmation_written": False,
            "refusal_json": repo_relative(refusal_record),
        },
        "publication_sanitization": {
            "applied_after_full_pipeline_validation": True,
            "absolute_checkout_prefix_replaced_with": PUBLISHED_REPO_PREFIX,
            "reviewed_handoff_source_hashes_rebound": True,
        },
    }
    manifest_path = DEMO_ROOT / "demo-manifest.json"
    write_json(manifest_path, manifest)
    transcript_path = DEMO_ROOT / "demo-run.txt"

    media_paths: list[Path] = []
    if not skip_media:
        media_root.mkdir(parents=True, exist_ok=True)
        summary_pdf_asset = media_root / SUMMARY_PDF_ASSET
        shutil.copy2(summary_pdf, summary_pdf_asset)
        summary_png = media_root / SUMMARY_PNG_ASSET
        fx_png = media_root / FX_PNG_ASSET
        render_first_page(summary_pdf, summary_png, raw_transcript)
        render_first_page(fx_folder / "workpaper.pdf", fx_png, raw_transcript)
        terminal_gif = media_root / TERMINAL_GIF_ASSET
        recording_lines = [
            *console_lines,
            "[9/9] PASS - JSON, CSV, PDF, screenshots, recording, and checksums retained",
            f"      {repo_relative(summary_pdf)}",
            f"      {repo_relative(terminal_gif)} (36 seconds)",
        ]
        terminal_poster = media_root / TERMINAL_POSTER_ASSET
        create_terminal_gif(recording_lines, terminal_gif, poster_output=terminal_poster)
        media_paths.extend([summary_pdf_asset, summary_png, fx_png, terminal_gif, terminal_poster])

    sanitize_published_text_artifacts(
        artifact_root,
        ((happy_preflight, happy_handoff), (refusal_preflight, refusal_handoff)),
    )

    final_console_lines = [
        *console_lines,
        "[9/9] PASS - JSON, CSV, PDF, screenshots, recording, and checksums retained",
        f"      {repo_relative(summary_pdf)}",
    ]
    if not skip_media:
        final_console_lines.append(f"      {repo_relative(media_root / TERMINAL_GIF_ASSET)} (36 seconds)")
    published_transcript = [
        line.replace(str(REPO_ROOT.resolve()), PUBLISHED_REPO_PREFIX).replace(str(Path(sys.executable).resolve()), "python3")
        for line in raw_transcript
    ]
    transcript_path.write_text(
        "\n".join([*final_console_lines, "", "DETAILED COMMAND TRANSCRIPT", *published_transcript]) + "\n",
        encoding="utf-8",
    )

    checksum_inputs = [
        *statements,
        *(path for path in artifact_root.rglob("*") if path.is_file()),
        manifest_path,
        transcript_path,
        *media_paths,
    ]
    checksum_destinations = [DEMO_ROOT / CHECKSUM_ASSET]
    if not skip_media:
        checksum_destinations.append(media_root / CHECKSUM_ASSET)
    write_checksums(checksum_inputs, checksum_destinations)

    emit("[9/9] PASS - JSON, CSV, PDF, screenshots, recording, and checksums retained")
    emit(f"      {repo_relative(summary_pdf)}")
    if not skip_media:
        emit(f"      {repo_relative(media_root / TERMINAL_GIF_ASSET)} (36 seconds)")


def run_ephemeral_check() -> None:
    """Execute both demo paths in scratch space without rewriting samples."""
    with tempfile.TemporaryDirectory(prefix="fbar-proof-demo-check-") as scratch_text:
        scratch = Path(scratch_text)
        runner = scratch / "run_demo.py"
        shutil.copy2(Path(__file__).resolve(), runner)
        environment = dict(os.environ)
        environment["FBAR_DEMO_REPO_ROOT"] = str(REPO_ROOT)
        process = subprocess.run(
            [sys.executable, str(runner), "--skip-media"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=environment,
            timeout=180,
        )
        if process.returncode != 0:
            raise DemoError(
                "Ephemeral demo check failed.\n"
                f"stdout:\n{process.stdout.rstrip()}\n"
                f"stderr:\n{process.stderr.rstrip()}"
            )
        manifest = read_json(scratch / "demo-manifest.json")
        happy = manifest.get("happy_path")
        refusal = manifest.get("refusal_path")
        require(
            isinstance(happy, dict)
            and happy.get("daily_threshold") == "no"
            and happy.get("fincen_maximum_value_view") == "no",
            "Ephemeral happy path did not preserve both expected no results.",
        )
        require(
            isinstance(refusal, dict)
            and refusal.get("daily_threshold") == "insufficient-records"
            and refusal.get("maximum_account_value") == "not-determinable"
            and refusal.get("confirmation_written") is False,
            "Ephemeral refusal path did not fail closed.",
        )
        print("PASS: ephemeral happy path and missing-quarter refusal path")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
        help="Artifact output root (default: examples/fbar-proof-demo/artifacts).",
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=DEFAULT_MEDIA_ROOT,
        help="Rendered public media root (default: docs/assets/demo).",
    )
    parser.add_argument(
        "--skip-media",
        action="store_true",
        help="Skip PDF screenshots and the 36-second terminal GIF; core proof artifacts still run.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run both paths in scratch space without rewriting committed samples.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.check:
            run_ephemeral_check()
        else:
            run_demo(args.artifact_root.resolve(), args.media_root.resolve(), skip_media=args.skip_media)
    except subprocess.TimeoutExpired:
        print("FAIL: ephemeral demo check exceeded 180 seconds", file=sys.stderr)
        return 1
    except DemoError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
