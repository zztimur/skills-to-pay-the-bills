#!/usr/bin/env python3
"""Golden / self-test for workpaper-kit.

Dependency-free and hermetic: builds fixed specs into a temp dir and asserts
stable ``workpaper.md`` / ``workpaper.json`` bytes (packet path normalized),
proof copy + SHA-256 hashing, the deep-merge / extra-row / proof-policy
divergence knobs, link escaping, PDF structural terms, and the overwrite
warning. Run: ``python3 workpaper-kit/test_workpaper.py``.

Byte-identity to the pre-extraction yearly/year-end output is a separate,
skill-level acceptance check (the migration phases); this file pins the kit's
own contract.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import workpaper as wp  # noqa: E402


YEARLY_CAVEATS = [
    "Golden caveat one: retained support workpaper, not tax advice.",
    "Golden caveat two: do not describe this rate as approved.",
]


def _norm(text: str, folder: Path) -> str:
    return text.replace(str(folder.resolve()), "<F>")


def _yearly_spec(root: Path, proof: Path) -> wp.WorkpaperSpec:
    return wp.WorkpaperSpec(
        output_root=str(root),
        skill_name="get-yearly-fx-rate",
        currency_code="CAD",
        year=2024,
        rate=Decimal("1.25"),
        rate_direction="foreign-per-usd",
        source_title="Golden FX Source",
        source_url="https://example.test/golden",
        source_category="golden published average",
        retrieval_date="2024-01-15",
        source_note="Golden note for the kit self-test.",
        document_title="Yearly FX Rate Workpaper",
        document_subtitle="Published annual average exchange-rate support",
        rate_phrase="yearly average",
        caveats=YEARLY_CAVEATS,
        saved_proofs=[proof],
        proof_required=False,
        proof_limitations=["Golden limitation line."],
    )


def test_yearly_golden_md_and_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "work"
        proof = tmp / "golden-proof.txt"
        proof.write_bytes(b"workpaper-kit golden proof source\n")
        proof_sha = wp.sha256_file(proof)

        workpaper = wp.build_workpaper(_yearly_spec(root, proof))
        folder = next(root.glob("cad-2024-*"))
        assert folder.name == "cad-2024-golden-fx-source", folder.name

        # returned dict carries reciprocal + presentation stash
        assert workpaper["foreign_per_usd"] == "1.25"
        assert workpaper["usd_per_foreign"] == "0.8"
        assert wp._PRESENTATION_KEY in workpaper
        # ...but the persisted JSON never leaks the presentation stash
        raw_json = (folder / "workpaper.json").read_text(encoding="utf-8")
        assert wp._PRESENTATION_KEY not in raw_json
        assert "_presentation" not in raw_json

        # --- golden MD (packet path normalized) ---
        actual_md = _norm((folder / "workpaper.md").read_text(encoding="utf-8"), folder)
        expected_md = "\n".join(
            [
                "# Yearly FX Rate Workpaper",
                "",
                "Currency: CAD",
                "Year: 2024",
                "Rate: 1 USD = 1.25 CAD yearly average",
                "Reciprocal: 1 CAD = 0.8 USD",
                "Source: Golden FX Source",
                "Source URL: https://example.test/golden",
                "Source category: golden published average",
                "Retrieved: 2024-01-15",
                "Source note: Golden note for the kit self-test.",
                "",
                "## Proof Files",
                "- Workpaper PDF: <F>/workpaper.pdf",
                f"- <F>/source-proof-1.txt (sha256: {proof_sha})",
                "",
                "## Proof Limitations",
                "- Golden limitation line.",
                "",
                "## Caveats",
                f"- {YEARLY_CAVEATS[0]}",
                f"- {YEARLY_CAVEATS[1]}",
                "",
            ]
        )
        assert actual_md == expected_md, f"MD golden mismatch:\n{actual_md!r}"

        # --- golden JSON (packet path normalized, pdf sha checked separately) ---
        data = json.loads(raw_json)
        pdf_path = Path(data["proof"]["workpaper_pdf"])
        assert data["proof"]["workpaper_pdf_sha256"] == wp.sha256_file(pdf_path)
        normalized = json.loads(_norm(raw_json, folder))
        expected_json = {
            "caveats": YEARLY_CAVEATS,
            "currency": "CAD",
            "foreign_per_usd": "1.25",
            "proof": {
                "limitations": ["Golden limitation line."],
                "saved_files": [
                    {
                        "filename": "source-proof-1.txt",
                        "packet_relative_path": "source-proof-1.txt",
                        "path": "<F>/source-proof-1.txt",
                        "sha256": proof_sha,
                    }
                ],
                "workpaper_json": "<F>/workpaper.json",
                "workpaper_md": "<F>/workpaper.md",
                "workpaper_pdf": "<F>/workpaper.pdf",
                "workpaper_pdf_sha256": data["proof"]["workpaper_pdf_sha256"],
            },
            "rate": "1.25",
            "rate_direction": "foreign-per-usd",
            "skill": "get-yearly-fx-rate",
            "source": {
                "category": "golden published average",
                "note": "Golden note for the kit self-test.",
                "retrieved": "2024-01-15",
                "title": "Golden FX Source",
                "url": "https://example.test/golden",
            },
            "usd_per_foreign": "0.8",
            "year": 2024,
        }
        assert normalized == expected_json, f"JSON golden mismatch:\n{json.dumps(normalized, indent=2, sort_keys=True)}"

        # --- byte determinism: same inputs -> identical md + json bytes ---
        rebuilt_root = tmp / "work2"
        wp.build_workpaper(_yearly_spec(rebuilt_root, proof))
        folder2 = next(rebuilt_root.glob("cad-2024-*"))
        assert _norm((folder / "workpaper.md").read_text(), folder) == _norm(
            (folder2 / "workpaper.md").read_text(), folder2
        )
        assert _norm((folder / "workpaper.json").read_text(), folder) == _norm(
            (folder2 / "workpaper.json").read_text(), folder2
        )
        # PDF embeds no paths -> byte-identical across roots (deterministic sha)
        assert (folder / "workpaper.pdf").read_bytes() == (folder2 / "workpaper.pdf").read_bytes()


def test_yearly_pdf_structural_terms() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proof = tmp / "golden-proof.txt"
        proof.write_bytes(b"workpaper-kit golden proof source\n")
        workpaper = wp.build_workpaper(_yearly_spec(tmp / "work", proof))
        pdf = Path(workpaper["proof"]["workpaper_pdf"])  # type: ignore[index]
        data = pdf.read_bytes()
        assert data.startswith(b"%PDF-1."), "PDF header"
        assert data.rstrip().endswith(b"%%EOF"), "PDF trailer"
        assert b"xref" in data
        assert b"/Type /Page /Parent" in data
        assert b"/Helvetica-Bold" in data
        assert b"/Helvetica-Oblique" in data
        assert len(data) > 3000
        for term in (
            "Yearly FX Rate Workpaper",
            "Published annual average exchange-rate support",
            "RETAINED SUPPORT WORKPAPER",
            "EXCHANGE RATE",
            "1 USD = 1.25 CAD",
            "yearly average",
            "Reciprocal: 1 CAD = 0.8 USD",
            "Golden FX Source",
            "SOURCE PROOF",
            "Saved source 1",
            "source-proof-1.txt",
            "retained in this proof packet",
            "SHA-256 1",
            "Reviewer note",
            "PROOF LIMITATIONS",
            "CAVEATS",
            # this fixed spec paginates to two pages: pins the footer +
            # continuation header path
            "get-yearly-fx-rate support workpaper | Page 1 of 2",
            "get-yearly-fx-rate support workpaper | Page 2 of 2",
            # page-2 continuation ribbon tag
            "PROOF PACKET",
        ):
            assert wp.clean_text(term).encode("latin-1", "replace") in data, term
        # continuation header renders the doc title on page 2
        assert b"CAD 2024 Yearly FX Rate Workpaper" in data
        # /Title metadata carries the same generalized title
        assert b"/Title (CAD 2024 Yearly FX Rate Workpaper)" in data
        # no absolute path leaks into the PDF (reviewer-portable)
        assert str(tmp).encode("latin-1", "replace") not in data


def test_year_end_divergence_knobs() -> None:
    """extra_json deep-merge, extra_rows, rate_phrase, proof_required."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "work"
        folder = root / "cop-2025-treasury-example"
        folder.mkdir(parents=True)
        proof = folder / "treasury.json"  # already inside the packet -> no copy
        proof.write_text('{"data": []}', encoding="utf-8")

        treasury_record = {"record_date": "2025-12-31", "exchange_rate": "3900.25", "api_query_url": "https://api.example/x"}
        spec = wp.WorkpaperSpec(
            output_root=str(root),
            skill_name="get-year-end-fx-rate",
            currency_code="COP",
            year=2025,
            rate=Decimal("3900.25"),
            rate_direction="foreign-per-usd",
            source_title="Treasury Example",
            source_url="https://fiscaldata.example/x",
            source_category="Treasury/Fiscal Data year-end reporting rate",
            retrieval_date="2026-02-01",
            source_note="Year-end record.",
            document_title="Year-End FX Rate Workpaper",
            document_subtitle="Year-end exchange-rate proof packet",
            rate_phrase="year-end (2025-12-31)",
            caveats=["Year-end caveat."],
            extra_rows=[("Year-end date", "2025-12-31")],
            extra_json={
                "purpose": "FBAR-style year-end USD exchange-rate support",
                "year_end_date": "2025-12-31",
                "treasury_record": treasury_record,
                "source": {"year_end_confirmed": True},
            },
            saved_proofs=[proof],
            proof_required=True,
        )
        workpaper = wp.build_workpaper(spec)
        data = json.loads((folder / "workpaper.json").read_text(encoding="utf-8"))

        # extra_json merged at TOP level
        assert data["purpose"] == "FBAR-style year-end USD exchange-rate support"
        assert data["year_end_date"] == "2025-12-31"
        assert data["treasury_record"] == treasury_record
        # nested source merge did NOT clobber the base source block
        assert data["source"]["year_end_confirmed"] is True
        assert data["source"]["title"] == "Treasury Example"
        assert set(data["source"]) == {"title", "url", "category", "retrieved", "note", "year_end_confirmed"}
        # base fields intact
        assert data["skill"] == "get-year-end-fx-rate"
        assert data["foreign_per_usd"] == "3900.25"
        assert data["usd_per_foreign"] == "0.000256393821"

        # extra_rows land in the MD between Year and Rate; rate_phrase applied
        md = (folder / "workpaper.md").read_text(encoding="utf-8")
        assert "Year: 2025\nYear-end date: 2025-12-31\nRate: 1 USD = 3900.25 COP year-end (2025-12-31)" in md

        # final_text uses the rate_phrase from the presentation stash
        ft = wp.final_text(workpaper)
        assert ft.startswith("Rate: 1 USD = 3900.25 COP year-end (2025-12-31)")

        # PDF adopts yearly's engine but must forbid average language
        pdf = (folder / "workpaper.pdf").read_bytes()
        assert b"Year-End FX Rate Workpaper" in pdf
        assert b"Year-end date" in pdf
        for forbidden in ("yearly average", "annual average"):
            assert forbidden.encode("latin-1", "replace") not in pdf, forbidden


def test_proof_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        missing = tmp / "does-not-exist.txt"

        # proof_required=True -> raise on a missing proof
        strict = wp.WorkpaperSpec(
            output_root=str(tmp / "strict"),
            skill_name="get-year-end-fx-rate",
            currency_code="COP",
            year=2025,
            rate=Decimal("1"),
            rate_direction="foreign-per-usd",
            source_title="S",
            source_url="u",
            source_category="c",
            retrieval_date="2026-01-01",
            source_note="n",
            document_title="Year-End FX Rate Workpaper",
            document_subtitle="sub",
            rate_phrase="year-end (2025-12-31)",
            caveats=["c"],
            saved_proofs=[missing],
            proof_required=True,
        )
        try:
            wp.build_workpaper(strict)
        except wp.RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("proof_required must raise on a missing proof")

        # proof_required=True + zero proofs -> auto limitation, no raise
        empty = wp.WorkpaperSpec(
            output_root=str(tmp / "empty"),
            skill_name="get-year-end-fx-rate",
            currency_code="COP",
            year=2025,
            rate=Decimal("1"),
            rate_direction="foreign-per-usd",
            source_title="S",
            source_url="u",
            source_category="c",
            retrieval_date="2026-01-01",
            source_note="n",
            document_title="Year-End FX Rate Workpaper",
            document_subtitle="sub",
            rate_phrase="year-end (2025-12-31)",
            caveats=["c"],
            saved_proofs=[],
            proof_required=True,
        )
        w = wp.build_workpaper(empty)
        assert wp.NO_PROOF_LIMITATION in w["proof"]["limitations"]  # type: ignore[index]

        # proof_required=False -> tolerantly skip a missing proof, no limitation
        tolerant = wp.WorkpaperSpec(
            output_root=str(tmp / "tol"),
            skill_name="get-yearly-fx-rate",
            currency_code="CAD",
            year=2024,
            rate=Decimal("1"),
            rate_direction="foreign-per-usd",
            source_title="S",
            source_url="u",
            source_category="c",
            retrieval_date="2026-01-01",
            source_note="n",
            document_title="Yearly FX Rate Workpaper",
            document_subtitle="sub",
            rate_phrase="yearly average",
            caveats=["c"],
            saved_proofs=[missing],
            proof_required=False,
        )
        w2 = wp.build_workpaper(tolerant)
        assert w2["proof"]["saved_files"] == []  # type: ignore[index]
        assert wp.NO_PROOF_LIMITATION not in w2["proof"]["limitations"]  # type: ignore[index]


def test_overwrite_warning() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proof = tmp / "p.txt"
        proof.write_bytes(b"x\n")
        spec = _yearly_spec(tmp / "work", proof)
        wp.build_workpaper(spec)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            wp.build_workpaper(_yearly_spec(tmp / "work", proof))
        assert "replacing existing workpaper packet" in err.getvalue()


def test_link_and_artifact_helpers() -> None:
    # markdown_file_link escapes label brackets and the '>' path delimiter
    link = wp.markdown_file_link("a [b] c", "/tmp/we>ird/workpaper.pdf")
    assert link == "[a \\[b\\] c](</tmp/we%3Eird/workpaper.pdf>)"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proof = tmp / "p.txt"
        proof.write_bytes(b"x\n")
        workpaper = wp.build_workpaper(_yearly_spec(tmp / "work", proof))
        links = wp.artifact_links(workpaper)
        assert "[workpaper.pdf](<" in links
        assert "[workpaper.md](<" in links
        assert "[workpaper.json](<" in links
        assert "[source-proof-1.txt](<" in links
        ft = wp.final_text(workpaper)
        assert ft.splitlines()[0] == "Rate: 1 USD = 1.25 CAD yearly average"
        assert ft.splitlines()[1] == "Reciprocal: 1 CAD = 0.8 USD"
        assert "Proof: [workpaper.pdf](<" in ft
        assert "Artifacts: [workpaper.pdf](<" in ft


def test_helpers() -> None:
    assert wp.slugify("IRS Yearly Average!") == "irs-yearly-average"
    assert wp.slugify("---") == "source"
    assert wp.fmt_decimal(Decimal("1.2500")) == "1.25"
    assert wp.fmt_decimal(Decimal("0.729927007299270072992700")) == "0.729927007299"
    assert wp.build_rate_values(Decimal("0.25"), "usd-per-foreign") == (Decimal("4"), Decimal("0.25"))
    for bad_rate in (Decimal("0"), Decimal("-1")):
        try:
            wp.build_rate_values(bad_rate, "foreign-per-usd")
        except wp.RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("non-positive rate must raise")
    try:
        wp.build_rate_values(Decimal("1"), "sideways")
    except wp.RateError as exc:
        assert exc.code == 2
    else:
        raise AssertionError("unknown direction must raise")


def test_hardening_boundary() -> None:
    """Adversarial inputs at the trust boundary fail cleanly — never crash,
    escape the output root, or depend on ambient global state."""
    from decimal import getcontext

    # Non-finite rates -> clean RateError (NaN <= 0 would raise InvalidOperation;
    # Infinity would slip a garbage rate through).
    for bad in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        try:
            wp.build_rate_values(bad, "foreign-per-usd")
        except wp.RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"non-finite rate {bad} must raise")

    # The reciprocal is independent of the caller's ambient decimal precision.
    original = getcontext().prec
    try:
        getcontext().prec = 28
        a = wp.fmt_decimal(wp.build_rate_values(Decimal("3900.25"), "foreign-per-usd")[1])
        getcontext().prec = 5
        b = wp.fmt_decimal(wp.build_rate_values(Decimal("3900.25"), "foreign-per-usd")[1])
        assert a == b == "0.000256393821", (a, b)
    finally:
        getcontext().prec = original

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "out"
        proof = tmp / "p.txt"
        proof.write_bytes(b"x\n")

        def spec(**kw: object) -> wp.WorkpaperSpec:
            base = dict(
                output_root=str(root), skill_name="kit", currency_code="CAD", year=2024,
                rate=Decimal("1.37"), rate_direction="foreign-per-usd", source_title="Src",
                source_url="u", source_category="c", retrieval_date="2026-01-01", source_note="n",
                document_title="T", document_subtitle="s", rate_phrase="p", caveats=["c"],
            )
            base.update(kw)
            return wp.WorkpaperSpec(**base)  # type: ignore[arg-type]

        # currency_code cannot inject path traversal (slugged into the folder name)
        w = wp.build_workpaper(spec(currency_code="../../PWNED", saved_proofs=[proof]))
        folder = Path(w["proof"]["workpaper_json"]).parent.resolve()  # type: ignore[index]
        assert str(folder).startswith(str(root.resolve())), folder
        assert folder.name == "pwned-2024-src", folder.name

        # a non-int year is rejected rather than injected into the path
        try:
            wp.build_workpaper(spec(year="2024/../etc", saved_proofs=[proof]))
        except wp.RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("non-int year must raise")

        # a directory as a required proof -> clean RateError, not IsADirectoryError
        adir = tmp / "adir"
        adir.mkdir()
        try:
            wp.build_workpaper(spec(currency_code="AUD", saved_proofs=[adir], proof_required=True))
        except wp.RateError as exc:
            assert exc.code == 2
        else:
            raise AssertionError("directory proof must raise when required")
        # ...and is tolerantly skipped (no crash) when not required
        w2 = wp.build_workpaper(spec(currency_code="EUR", saved_proofs=[adir], proof_required=False))
        assert w2["proof"]["saved_files"] == []  # type: ignore[index]

        # extra_json cannot overwrite reserved keys or supply a non-dict source
        for bad_extra in ({"skill": "hijacked"}, {"proof": {"saved_files": "no"}}, {"source": "x"}):
            try:
                wp.build_workpaper(spec(currency_code="GBP", saved_proofs=[proof], extra_json=bad_extra))
            except wp.RateError as exc:
                assert exc.code == 2
            else:
                raise AssertionError(f"extra_json {bad_extra} must raise")


def test_pdf_transliteration() -> None:
    """Non-latin1 source text degrades legibly in the PDF; json/md keep UTF-8."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proof = tmp / "p.txt"
        proof.write_bytes(b"x\n")
        title = "Banco Central — €/₩ al cierre “oficial”"
        workpaper = wp.build_workpaper(
            wp.WorkpaperSpec(
                output_root=str(tmp / "out"), skill_name="get-year-end-fx-rate",
                currency_code="MXN", year=2024, rate=Decimal("17"),
                rate_direction="foreign-per-usd", source_title=title, source_url="u",
                source_category="c", retrieval_date="2026-01-01", source_note="fee €100",
                document_title="Year-End FX Rate Workpaper", document_subtitle="s",
                rate_phrase="year-end (2024-12-31)", caveats=["c"],
                saved_proofs=[proof], proof_required=True,
            )
        )
        folder = Path(workpaper["proof"]["workpaper_json"]).parent  # type: ignore[index]
        # json keeps the original UTF-8 (machine-truth), untouched by transliteration
        data = json.loads((folder / "workpaper.json").read_text(encoding="utf-8"))
        assert data["source"]["title"] == title
        assert "€" in data["source"]["note"]
        # the latin-1 PDF degrades legibly: symbols -> codes, smart quotes -> ascii,
        # and no non-latin1 bytes survive
        pdf = (folder / "workpaper.pdf").read_bytes()
        assert b"EUR" in pdf and b"KRW" in pdf
        assert b'"oficial"' in pdf
        assert "—".encode("utf-8") not in pdf
        assert "€".encode("utf-8") not in pdf
        # latin-1 accents that render fine are NOT stripped
        assert wp._to_latin1_safe("Zürich café") == "Zürich café"


def test_optional_reportlab_packet_renderer() -> None:
    """The shared rich-packet API stays optional for standard-library FX runs."""
    if not wp.reportlab_available():
        return
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "packet.pdf"
        renderer = wp.ReportlabPacketRenderer(
            output,
            footer_text="Generated support packet - not an official IRS form.",
        )
        styles = renderer.make_styles()
        story: list = []
        renderer.add_hero(story, styles, "Example Bank", 2025)
        renderer.add_kpi_cards(story, [("Interest rows", "1"), ("USD support total", "USD 10.00")], styles)
        renderer.add_table(story, [["Field", "Value"], ["Account", "[redacted]"]], [2.1 * 72, 5.1 * 72], styles)
        renderer.add_note_box(story, "Warnings for preparer review", ["A reviewed warning."], styles, tone="warning")
        assert renderer.build(story) == output
        data = output.read_bytes()
        assert data.startswith(b"%PDF-1."), "ReportLab packet PDF header"
        assert data.rstrip().endswith(b"%%EOF"), "ReportLab packet PDF trailer"
        assert len(data) > 1000


def main() -> int:
    tests = [
        test_yearly_golden_md_and_json,
        test_yearly_pdf_structural_terms,
        test_year_end_divergence_knobs,
        test_proof_policy,
        test_overwrite_warning,
        test_link_and_artifact_helpers,
        test_helpers,
        test_hardening_boundary,
        test_pdf_transliteration,
        test_optional_reportlab_packet_renderer,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print("workpaper-kit golden test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
