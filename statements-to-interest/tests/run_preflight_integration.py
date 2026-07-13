#!/usr/bin/env python3
"""Run real-PDF statement-intake-preflight handoff regressions for interest extraction.

This suite generates synthetic PDFs, then invokes the sibling preflight CLI and
this package's extraction CLI as independent subprocesses. It proves the
ordered file-fingerprint handoff is enforced across the package boundary.

Run with the bundled Codex Python when available:

    "$PYTHON" statements-to-interest/tests/run_preflight_integration.py

Exit 0 when every scenario passes or the required PDF dependencies are absent;
exit 1 on a regression. All PDFs and JSON artifacts are temporary.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

try:  # Test-only dependencies; skip cleanly when a compatible PDF runtime is unavailable.
    import pdfplumber  # noqa: F401
    from pypdf import PdfReader  # noqa: F401
    from reportlab.pdfgen import canvas
except BaseException as exc:  # noqa: BLE001 - a broken native dependency can raise more than ImportError.
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise
    print(f"SKIP: preflight integration needs reportlab, pdfplumber, and pypdf ({type(exc).__name__}); not run.")
    raise SystemExit(0)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parent
PREFLIGHT_SCRIPT = REPOSITORY_ROOT / "statement-intake-preflight" / "scripts" / "statement_intake_preflight.py"
INTEREST_SCRIPT = PACKAGE_ROOT / "scripts" / "statements_to_interest.py"


def command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)


def command_output(process: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (process.stdout.strip(), process.stderr.strip()) if part)


def make_pdf(root: Path, name: str, lines: list[str]) -> Path:
    path = root / name
    document = canvas.Canvas(str(path))
    y = 760
    for line in lines:
        document.drawString(48, y, line)
        y -= 18
    document.save()
    return path


def preflight(root: Path, tag: str, pdfs: list[Path]) -> tuple[subprocess.CompletedProcess[str], Path, dict | None]:
    output = root / f"{tag}-preflight.json"
    process = command(
        [
            sys.executable,
            str(PREFLIGHT_SCRIPT),
            "preflight",
            "--pdf",
            *(str(pdf) for pdf in pdfs),
            "--tax-year",
            "2025",
            "--scope",
            "one-institution",
            "--out",
            str(output),
        ]
    )
    if not output.is_file():
        return process, output, None
    try:
        return process, output, json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return process, output, None


def extract(
    pdf_paths: list[Path], preflight_json: Path, output: Path, institution: str = "Example Bank"
) -> subprocess.CompletedProcess[str]:
    return command(
        [
            sys.executable,
            str(INTEREST_SCRIPT),
            "extract",
            "--pdf",
            *(str(pdf) for pdf in pdf_paths),
            "--tax-year",
            "2025",
            "--institution",
            institution,
            "--preflight-json",
            str(preflight_json),
            "--out",
            str(output),
        ]
    )


def ready_statement_lines(interest: str = "1.25") -> list[str]:
    return [
        "Example Bank Monthly Statement",
        "Account 12345678",  # privacy-gate: allow (synthetic account fixture)
        "Statement period January 1 2025 to January 31 2025",
        "Currency USD",
        "Closing balance 100.00 USD",
        f"2025-01-03 Interest credited USD {interest}",
    ]


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"PASS: {name}")
        else:
            failures.append(f"{name}: {detail}".rstrip())
            print(f"FAIL: {name}: {detail}", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="statements-to-interest-preflight-") as temporary:
        root = Path(temporary)

        ready_pdf = make_pdf(root, "ready.pdf", ready_statement_lines())
        ready_process, ready_preflight_path, ready_preflight = preflight(root, "ready", [ready_pdf])
        ready_valid = (
            ready_process.returncode == 0
            and isinstance(ready_preflight, dict)
            and ready_preflight.get("status") == "ready-for-domain-extraction"
            and ready_preflight.get("review_gates") == []
        )
        check("ready preflight accepts one-institution USD PDF", ready_valid, command_output(ready_process))

        if ready_valid:
            ready_analysis_path = root / "ready-analysis.json"
            ready_extract = extract([ready_pdf], ready_preflight_path, ready_analysis_path)
            analysis = json.loads(ready_analysis_path.read_text(encoding="utf-8")) if ready_analysis_path.is_file() else {}
            rows = analysis.get("rows") if isinstance(analysis, dict) else None
            expected_fingerprints = [
                {
                    "resolved_file": ready_preflight["statement_files"][0]["resolved_file"],
                    "content_sha256": ready_preflight["statement_files"][0]["content_sha256"],
                    "content_bytes": ready_preflight["statement_files"][0]["content_bytes"],
                }
            ]
            verified_fingerprints = analysis.get("preflight", {}).get("verified_statement_files") if isinstance(analysis, dict) else None
            check(
                "ready preflight extracts one USD interest row and retains verified fingerprints",
                (
                    ready_extract.returncode == 0
                    and analysis.get("status") == "ready-for-reporting"
                    and isinstance(rows, list)
                    and len(rows) == 1
                    and rows[0].get("amount_foreign") == "1.25"
                    and rows[0].get("currency") == "USD"
                    and verified_fingerprints == expected_fingerprints
                ),
                command_output(ready_extract),
            )

            forged_fx_analysis = dict(analysis)
            forged_fx_analysis["skill"] = "forged-statements-to-interest"
            forged_fx_path = root / "forged-fx-analysis.json"
            forged_fx_path.write_text(json.dumps(forged_fx_analysis, indent=2), encoding="utf-8")
            forged_fx_prompt = command(
                [
                    sys.executable,
                    str(INTEREST_SCRIPT),
                    "fx-prompt",
                    "--input",
                    str(forged_fx_path),
                    "--fx-method",
                    "user-rate",
                    "--fx-rate",
                    "4000",
                    "--fx-source",
                    "synthetic test rate",
                ]
            )
            check(
                "fx prompt rejects a forged analysis before requesting confirmation",
                forged_fx_prompt.returncode != 0 and "must be generated by" in command_output(forged_fx_prompt),
                command_output(forged_fx_prompt),
            )

            legacy_preflight = dict(ready_preflight)
            legacy_statement_files = [dict(item) for item in ready_preflight["statement_files"]]
            for item in legacy_statement_files:
                item.pop("content_bytes", None)
                item.pop("content_sha256", None)
            legacy_preflight["statement_files"] = legacy_statement_files
            legacy_preflight_path = root / "legacy-preflight.json"
            legacy_preflight_path.write_text(json.dumps(legacy_preflight, indent=2), encoding="utf-8")
            legacy_extract = extract([ready_pdf], legacy_preflight_path, root / "legacy-analysis.json")
            check(
                "legacy preflight without file fingerprints is rejected",
                legacy_extract.returncode != 0 and "SHA-256 fingerprint" in command_output(legacy_extract),
                command_output(legacy_extract),
            )

        first_lines = ready_statement_lines("1.00")
        first_lines[2] = "Statement period January 1 2025 to September 30 2025"
        first_pdf = make_pdf(root, "first.pdf", first_lines)
        second_lines = ready_statement_lines("2.00")
        second_lines[2] = "Statement period October 1 2025 to December 31 2025"
        second_lines[5] = "2025-10-03 Interest credited USD 2.00"
        second_pdf = make_pdf(root, "second.pdf", second_lines)
        ordered_process, ordered_preflight_path, ordered_preflight = preflight(root, "ordered", [first_pdf, second_pdf])
        ordered_valid = (
            ordered_process.returncode == 0
            and isinstance(ordered_preflight, dict)
            and ordered_preflight.get("status") == "ready-for-domain-extraction"
            and ordered_preflight.get("review_gates") == []
        )
        check("two-PDF ordered preflight is ready", ordered_valid, command_output(ordered_process))
        if ordered_valid:
            reordered_extract = extract([second_pdf, first_pdf], ordered_preflight_path, root / "reordered-analysis.json")
            check(
                "reordered PDFs are rejected before extraction",
                reordered_extract.returncode != 0 and "PDF sequence" in command_output(reordered_extract),
                command_output(reordered_extract),
            )

        changed_pdf = make_pdf(root, "changed.pdf", ready_statement_lines("3.00"))
        changed_process, changed_preflight_path, changed_preflight = preflight(root, "changed", [changed_pdf])
        changed_valid = (
            changed_process.returncode == 0
            and isinstance(changed_preflight, dict)
            and changed_preflight.get("status") == "ready-for-domain-extraction"
            and changed_preflight.get("review_gates") == []
        )
        check("changed-PDF preflight is ready before replacement", changed_valid, command_output(changed_process))
        if changed_valid:
            make_pdf(root, "changed.pdf", ready_statement_lines("4.00"))
            changed_extract = extract([changed_pdf], changed_preflight_path, root / "changed-analysis.json")
            check(
                "PDF changed after preflight is rejected before extraction",
                changed_extract.returncode != 0 and "changed after preflight" in command_output(changed_extract),
                command_output(changed_extract),
            )

        review_lines = ready_statement_lines("5.00") + ["Currency EUR", "Closing balance 100.00 EUR"]
        review_pdf = make_pdf(root, "review-required.pdf", review_lines)
        review_process, review_preflight_path, review_preflight = preflight(root, "review", [review_pdf])
        review_ready = (
            review_process.returncode == 0
            and isinstance(review_preflight, dict)
            and review_preflight.get("status") == "review-required"
            and [gate.get("code") for gate in review_preflight.get("review_gates", [])] == ["mixed-currencies"]
        )
        check("mixed-currency preflight can produce a review handoff", review_ready, command_output(review_process))
        if review_ready:
            reviewed_handoff_path = root / "reviewed-handoff.json"
            handoff = command(
                [
                    sys.executable,
                    str(PREFLIGHT_SCRIPT),
                    "review-handoff",
                    "--input",
                    str(review_preflight_path),
                    "--out",
                    str(reviewed_handoff_path),
                    "--accept-gate",
                    "mixed-currencies",
                    "--user-review-confirmed",
                ]
            )
            handoff_data = json.loads(reviewed_handoff_path.read_text(encoding="utf-8")) if reviewed_handoff_path.is_file() else {}
            handoff_valid = handoff.returncode == 0 and handoff_data.get("status") == "reviewed-for-domain-extraction"
            check("preflight CLI creates an FBAR-style reviewed handoff", handoff_valid, command_output(handoff))
            if handoff_valid:
                reviewed_extract = extract([review_pdf], reviewed_handoff_path, root / "reviewed-analysis.json")
                check(
                    "reviewed handoff with unresolved mixed currencies is rejected for interest extraction",
                    reviewed_extract.returncode != 0 and "mixed-currencies" in command_output(reviewed_extract),
                    command_output(reviewed_extract),
                )

        generated_metadata_pdf = make_pdf(
            root,
            "generated-metadata.pdf",
            ready_statement_lines("5.50") + ["Extracto de cuenta generado el 31 de Diciembre de 2026"],
        )
        generated_process, generated_preflight_path, generated_preflight = preflight(
            root, "generated-metadata", [generated_metadata_pdf]
        )
        generated_valid = (
            generated_process.returncode == 0
            and isinstance(generated_preflight, dict)
            and generated_preflight.get("status") == "review-required"
            and [gate.get("code") for gate in generated_preflight.get("review_gates", [])]
            == ["out-of-period-generated-date"]
        )
        check(
            "out-of-period generated-on metadata requires source-bound review",
            generated_valid,
            command_output(generated_process),
        )
        if generated_valid:
            reviewed_generated_path = root / "generated-metadata-reviewed.json"
            reviewed_generated = command(
                [
                    sys.executable,
                    str(PREFLIGHT_SCRIPT),
                    "review-handoff",
                    "--input",
                    str(generated_preflight_path),
                    "--out",
                    str(reviewed_generated_path),
                    "--accept-gate",
                    "out-of-period-generated-date",
                    "--confirm-generated-on-date",
                    "2026-12-31",
                    "--user-review-confirmed",
                ]
            )
            reviewed_generated_data = (
                json.loads(reviewed_generated_path.read_text(encoding="utf-8")) if reviewed_generated_path.is_file() else {}
            )
            generated_resolution = reviewed_generated_data.get("user_resolutions", {}).get("generated_on_dates", {})
            generated_handoff_valid = (
                reviewed_generated.returncode == 0
                and generated_resolution.get("status") == "user-confirmed"
                and generated_resolution.get("confirmed_dates") == ["2026-12-31"]
            )
            check(
                "reviewed handoff retains the generated-on metadata resolution",
                generated_handoff_valid,
                command_output(reviewed_generated),
            )
            if generated_handoff_valid:
                generated_extract = extract(
                    [generated_metadata_pdf], reviewed_generated_path, root / "generated-metadata-analysis.json"
                )
                check(
                    "source-bound generated-on handoff is accepted for interest extraction",
                    generated_extract.returncode == 0,
                    command_output(generated_extract),
                )

                missing_generated_data = json.loads(reviewed_generated_path.read_text(encoding="utf-8"))
                missing_generated_data["user_resolutions"]["generated_on_dates"]["confirmed_dates"] = []
                missing_generated_path = root / "generated-metadata-missing-date.json"
                missing_generated_path.write_text(json.dumps(missing_generated_data, indent=2), encoding="utf-8")
                missing_generated_extract = extract(
                    [generated_metadata_pdf], missing_generated_path, root / "generated-metadata-missing-date-analysis.json"
                )
                check(
                    "reviewed handoff rejects a missing generated-on date",
                    missing_generated_extract.returncode != 0
                    and "generated-on dates" in command_output(missing_generated_extract),
                    command_output(missing_generated_extract),
                )

                tampered_anchor_data = json.loads(reviewed_generated_path.read_text(encoding="utf-8"))
                tampered_anchor_data["user_resolutions"]["generated_on_dates"]["source_date_evidence"][0]["source_ref"]["line"] = 99
                tampered_anchor_path = root / "generated-metadata-tampered-anchor.json"
                tampered_anchor_path.write_text(json.dumps(tampered_anchor_data, indent=2), encoding="utf-8")
                tampered_anchor_extract = extract(
                    [generated_metadata_pdf], tampered_anchor_path, root / "generated-metadata-tampered-anchor-analysis.json"
                )
                check(
                    "reviewed handoff rejects a tampered generated-on source anchor",
                    tampered_anchor_extract.returncode != 0
                    and "generated-on date evidence" in command_output(tampered_anchor_extract),
                    command_output(tampered_anchor_extract),
                )

        unknown_institution_pdf = make_pdf(
            root,
            "unknown-institution.pdf",
            [
                "Monthly Savings Statement",
                "Statement period January 1 2025 to January 31 2025",
                "Currency USD",
                "2025-01-03 Interest credited USD 6.00",
            ],
        )
        unknown_process, unknown_preflight_path, unknown_preflight = preflight(
            root, "unknown-institution", [unknown_institution_pdf]
        )
        unknown_valid = (
            unknown_process.returncode == 0
            and isinstance(unknown_preflight, dict)
            and unknown_preflight.get("status") == "review-required"
            and [gate.get("code") for gate in unknown_preflight.get("review_gates", [])] == ["unknown-institution"]
        )
        check("unknown institution preflight requires a typed confirmation", unknown_valid, command_output(unknown_process))
        if unknown_valid:
            missing_confirmation = command(
                [
                    sys.executable,
                    str(PREFLIGHT_SCRIPT),
                    "review-handoff",
                    "--input",
                    str(unknown_preflight_path),
                    "--out",
                    str(root / "missing-institution-confirmation.json"),
                    "--accept-gate",
                    "unknown-institution",
                    "--user-review-confirmed",
                ]
            )
            check(
                "unknown institution cannot create a reviewed handoff without its confirmation",
                missing_confirmation.returncode != 0 and "--confirm-institution" in command_output(missing_confirmation),
                command_output(missing_confirmation),
            )

            reviewed_unknown_path = root / "unknown-institution-reviewed.json"
            reviewed_unknown = command(
                [
                    sys.executable,
                    str(PREFLIGHT_SCRIPT),
                    "review-handoff",
                    "--input",
                    str(unknown_preflight_path),
                    "--out",
                    str(reviewed_unknown_path),
                    "--accept-gate",
                    "unknown-institution",
                    "--confirm-institution",
                    "Example Bank",
                    "--user-review-confirmed",
                ]
            )
            reviewed_unknown_data = (
                json.loads(reviewed_unknown_path.read_text(encoding="utf-8")) if reviewed_unknown_path.is_file() else {}
            )
            institution_resolution = reviewed_unknown_data.get("user_resolutions", {}).get("institution", {})
            handoff_valid = (
                reviewed_unknown.returncode == 0
                and institution_resolution.get("status") == "user-confirmed"
                and institution_resolution.get("name") == "Example Bank"
            )
            check("reviewed handoff retains the typed institution resolution", handoff_valid, command_output(reviewed_unknown))
            if handoff_valid:
                reviewed_interest = extract(
                    [unknown_institution_pdf], reviewed_unknown_path, root / "unknown-institution-analysis.json"
                )
                check(
                    "reviewed institution handoff is accepted and bound for interest extraction",
                    reviewed_interest.returncode == 0,
                    command_output(reviewed_interest),
                )
                mismatched_interest = extract(
                    [unknown_institution_pdf],
                    reviewed_unknown_path,
                    root / "unknown-institution-mismatch-analysis.json",
                    institution="Other Bank",
                )
                check(
                    "reviewed institution handoff rejects a mismatched extraction institution",
                    mismatched_interest.returncode != 0 and "does not match extraction institution" in command_output(mismatched_interest),
                    command_output(mismatched_interest),
                )

        unresolved_year_pdf = make_pdf(
            root,
            "unresolved-year.pdf",
            [
                "Example Bank Statement",
                "Statement period January 1 2025 to March 31 2025",
                "Historic reference 31/12/2024",
                "Currency USD",
                "2025-02-03 Interest credited USD 7.00",
            ],
        )
        unresolved_process, unresolved_preflight_path, unresolved_preflight = preflight(
            root, "unresolved-year", [unresolved_year_pdf]
        )
        unresolved_valid = (
            unresolved_process.returncode == 0
            and isinstance(unresolved_preflight, dict)
            and unresolved_preflight.get("status") == "review-required"
            and [gate.get("code") for gate in unresolved_preflight.get("review_gates", [])] == ["unresolved-year-evidence"]
        )
        check("unresolved year preflight requires a typed contextual-year confirmation", unresolved_valid, command_output(unresolved_process))
        if unresolved_valid:
            reviewed_unresolved_path = root / "unresolved-year-reviewed.json"
            reviewed_unresolved = command(
                [
                    sys.executable,
                    str(PREFLIGHT_SCRIPT),
                    "review-handoff",
                    "--input",
                    str(unresolved_preflight_path),
                    "--out",
                    str(reviewed_unresolved_path),
                    "--accept-gate",
                    "unresolved-year-evidence",
                    "--confirm-statement-year",
                    "2025",
                    "--classify-contextual-year",
                    "2024",
                    "--user-review-confirmed",
                ]
            )
            reviewed_unresolved_data = (
                json.loads(reviewed_unresolved_path.read_text(encoding="utf-8")) if reviewed_unresolved_path.is_file() else {}
            )
            contextual_classifications = (
                reviewed_unresolved_data.get("user_resolutions", {})
                .get("statement_years", {})
                .get("contextual_year_classifications", [])
            )
            valid_contextual_handoff = (
                reviewed_unresolved.returncode == 0
                and contextual_classifications
                == [
                    {
                        "year": 2024,
                        "classification": "user-confirmed-contextual-prior-year",
                        "source": "user-review",
                    }
                ]
            )
            check(
                "reviewed year handoff retains the required contextual-year classification",
                valid_contextual_handoff,
                command_output(reviewed_unresolved),
            )
            if valid_contextual_handoff:
                valid_year_extract = extract(
                    [unresolved_year_pdf], reviewed_unresolved_path, root / "unresolved-year-analysis.json"
                )
                check(
                    "reviewed contextual-year handoff is accepted for interest extraction",
                    valid_year_extract.returncode == 0,
                    command_output(valid_year_extract),
                )

                missing_contextual_data = json.loads(reviewed_unresolved_path.read_text(encoding="utf-8"))
                missing_contextual_data["user_resolutions"]["statement_years"]["contextual_year_classifications"] = []
                missing_contextual_path = root / "unresolved-year-missing-contextual.json"
                missing_contextual_path.write_text(json.dumps(missing_contextual_data, indent=2), encoding="utf-8")
                missing_contextual_extract = extract(
                    [unresolved_year_pdf], missing_contextual_path, root / "unresolved-year-missing-contextual-analysis.json"
                )
                check(
                    "reviewed handoff rejects a missing contextual-year classification",
                    missing_contextual_extract.returncode != 0
                    and "contextual-year classifications" in command_output(missing_contextual_extract),
                    command_output(missing_contextual_extract),
                )

                substituted_contextual_data = json.loads(reviewed_unresolved_path.read_text(encoding="utf-8"))
                substituted_contextual_data["user_resolutions"]["statement_years"]["contextual_year_classifications"] = [
                    {
                        "year": 2023,
                        "classification": "user-confirmed-contextual-prior-year",
                        "source": "user-review",
                    }
                ]
                substituted_contextual_path = root / "unresolved-year-substituted-contextual.json"
                substituted_contextual_path.write_text(json.dumps(substituted_contextual_data, indent=2), encoding="utf-8")
                substituted_contextual_extract = extract(
                    [unresolved_year_pdf],
                    substituted_contextual_path,
                    root / "unresolved-year-substituted-contextual-analysis.json",
                )
                check(
                    "reviewed handoff rejects a substituted contextual-year classification",
                    substituted_contextual_extract.returncode != 0
                    and "contextual-year classifications" in command_output(substituted_contextual_extract),
                    command_output(substituted_contextual_extract),
                )

    if failures:
        print(f"Integration suite failed: {len(failures)} scenario(s).", file=sys.stderr)
        return 1
    print("Preflight integration passed: ready workflow, fingerprints, reordered and changed PDFs, legacy handoff, and reviewed-handoff guardrails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
