#!/usr/bin/env python3
"""Regression tests for privacy_gate.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
SCRIPT_PATH = Path(__file__).with_name("privacy_gate.py")
SPEC = importlib.util.spec_from_file_location("privacy_gate", SCRIPT_PATH)
privacy_gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["privacy_gate"] = privacy_gate
SPEC.loader.exec_module(privacy_gate)


class PrivacyGateTests(unittest.TestCase):
    def scan_text(self, path: str, text: str):
        return privacy_gate.scan_bytes(path, text.encode("utf-8"))

    def test_clean_file_passes(self):
        findings = self.scan_text("README.md", "Use placeholders in docs.\n")
        self.assertEqual(findings, [])

    def test_openai_key_blocks(self):
        fake_key = "sk-" + ("A" * 32)
        findings = self.scan_text("config.txt", "OPENAI_API_KEY=" + fake_key + "\n")
        self.assertTrue(any(item.code == "secret_openai_api_key" for item in findings))
        self.assertTrue(any(item.severity == "block" for item in findings))

    def test_generated_artifact_path_blocks(self):
        findings = self.scan_text("work/interest-analysis.json", "{}\n")
        self.assertTrue(any(item.code == "generated_artifact_path" for item in findings))

    def test_missing_path_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = privacy_gate.scan_path(Path(tmpdir) / "missing.txt")
        self.assertEqual(result.scanned_files, 0)
        self.assertTrue(any(item.code == "scan_path_missing" for item in result.findings))
        self.assertTrue(any(item.severity == "block" for item in result.findings))

    def test_large_text_blocks_as_partial_coverage(self):
        data = ("a" * (privacy_gate.MAX_TEXT_BYTES + 1)).encode("utf-8")
        findings = privacy_gate.scan_bytes("huge.txt", data)
        self.assertTrue(any(item.code == "text_read_limit_exceeded" for item in findings))
        self.assertTrue(any(item.severity == "block" for item in findings))

    def test_symlink_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "outside.txt"
            target.write_text("outside\n", encoding="utf-8")
            scan_root = root / "scan"
            scan_root.mkdir()
            link = scan_root / "linked.txt"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            result = privacy_gate.scan_path(scan_root)
        self.assertTrue(any(item.code == "symlink_found" for item in result.findings))
        self.assertTrue(any(item.severity == "block" for item in result.findings))

    def test_placeholder_examples_are_allowed(self):
        text = "OPENAI_API_KEY=<OPENAI_API_KEY>\ncontact=team@example.com\n"
        findings = self.scan_text(".env.example", text)
        self.assertEqual(findings, [])

    def test_private_data_warns(self):
        email = "person" + "@" + "private.test"
        phone = "-".join(["415", "555", "1212"])
        findings = self.scan_text("notes.txt", f"Contact {email} at {phone}.\n")
        codes = {item.code for item in findings}
        self.assertIn("private_email", codes)
        self.assertIn("private_phone", codes)
        self.assertTrue(all(item.severity == "warning" for item in findings))

    def test_sanitizer_redacts_text_pii(self):
        phone = "-".join(["415", "555", "1212"])
        text = "Email: " + "person" + "@" + "private.test\nPhone: " + phone + "\n"
        redacted = privacy_gate.redact_text(text)
        self.assertIn("<REDACTED_EMAIL>", redacted)
        self.assertIn("<REDACTED_PHONE>", redacted)
        self.assertNotIn(phone, redacted)

    def test_staged_scan_reads_index_blob(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            secret = "sk-" + ("B" * 32)
            (root / "leak.txt").write_text("OPENAI_API_KEY=" + secret + "\n", encoding="utf-8")
            subprocess.run(["git", "add", "leak.txt"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "scan", "--staged", "--strict"],
                cwd=root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret_openai_api_key", result.stdout)
        self.assertNotIn(secret, result.stdout)


if __name__ == "__main__":
    unittest.main()
