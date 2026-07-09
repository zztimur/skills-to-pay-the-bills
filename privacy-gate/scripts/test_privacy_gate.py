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

    def test_prefixed_credential_assignments_block(self):
        # The keyword is the trailing component of a longer identifier; a bare
        # \b boundary missed these before. Lines are assembled from split
        # literals so this test file stays clean under the gate's own scan.
        eq = " = "
        cases = {
            "settings.py": "DJANGO_SECRET_KEY" + eq + "x7Kj9mPqR2wN8vB4tY6uI1oL3eF5gH0z",
            "db.cfg": "DB_PASSWORD" + eq + "SuperReal" + "-Passw0rd!",
            "aws.cfg": "AWS_SECRET_ACCESS_KEY" + eq + "wJalrXUtnFEMI" + "K7bPxRfiCYz99KEY",
        }
        for path, text in cases.items():
            findings = self.scan_text(path, text + "\n")
            self.assertTrue(
                any(item.severity == "block" for item in findings),
                f"{path} should block: {findings}",
            )

    def test_provider_tokens_block(self):
        cases = {
            "secret_aws_access_key_id": "id = AKIA" + ("Q" * 16),
            "secret_stripe_key": "key=sk_live_" + ("a" * 24),
            "secret_google_api_key": "GMAPS=AIza" + ("b" * 35),
            "secret_gitlab_pat": "token: glpat-" + ("c" * 20),
            "secret_github_fine_grained_pat": "gh=github_pat_" + ("d" * 30),
            "secret_npm_token": "npm=npm_" + ("e" * 36),
        }
        for code, text in cases.items():
            findings = self.scan_text("conf.txt", text + "\n")
            self.assertTrue(
                any(item.code == code and item.severity == "block" for item in findings),
                f"{code} not detected in {text!r}: {[f.code for f in findings]}",
            )

    def test_prefixed_placeholder_still_allowed(self):
        text = "DJANGO_SECRET_KEY = <YOUR_SECRET_KEY>\nDB_PASSWORD=changeme\n"
        findings = self.scan_text("example.md", text)
        self.assertEqual(findings, [])

    def test_env_suffix_file_blocks(self):
        for name in ("prod.env", "staging.env", "production.env"):
            findings = self.scan_text(name, "API=1\n")
            self.assertTrue(
                any(item.code == "secret_env_file" for item in findings),
                f"{name} should block as an env file",
            )

    def test_env_template_suffix_allowed(self):
        for name in ("example.env", "sample.env", ".env.example"):
            findings = self.scan_text(name, "API=1\n")
            self.assertFalse(
                any(item.code == "secret_env_file" for item in findings),
                f"{name} should be treated as a template",
            )

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

    def test_installed_hook_blocks_without_vendored_copy(self):
        # PG1: the generated pre-commit hook must work in a repo that does NOT
        # vendor privacy-gate/ at its root, by falling back to the absolute
        # script path recorded at install time.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            quiet = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
            subprocess.run(["git", "init"], cwd=root, check=True, **quiet)
            subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True, **quiet)
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, **quiet)
            install = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "install-hook"],
                cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            hook_text = (root / ".githooks" / "pre-commit").read_text(encoding="utf-8")
            self.assertIn(str(SCRIPT_PATH.resolve()), hook_text)
            secret = "sk-" + ("C" * 32)
            (root / "leak.txt").write_text("OPENAI_API_KEY=" + secret + "\n", encoding="utf-8")
            subprocess.run(["git", "add", "leak.txt"], cwd=root, check=True, **quiet)
            commit = subprocess.run(
                ["git", "commit", "-m", "x"],
                cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        self.assertNotEqual(commit.returncode, 0, "hook must block a staged secret cross-repo")
        self.assertNotIn(secret, commit.stdout + commit.stderr)

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
