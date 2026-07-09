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

    def _init_repo(self, root: Path):
        q = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        subprocess.run(["git", "init"], cwd=root, check=True, **q)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True, **q)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, **q)

    def _install_hook(self, root: Path, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "install-hook", *extra],
            cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def _hooks_path(self, root: Path) -> str:
        return subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=root, check=False, stdout=subprocess.PIPE, text=True,
        ).stdout.strip()

    def test_install_hook_refuses_foreign_hooks_path(self):
        # PG4: do not silently disable an existing hook manager (e.g. husky).
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            husky = root / ".husky"
            husky.mkdir()
            (husky / "pre-commit").write_text("#!/bin/sh\necho husky\n", encoding="utf-8")
            subprocess.run(["git", "config", "core.hooksPath", ".husky"], cwd=root, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            refused = self._install_hook(root)
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertEqual(self._hooks_path(root), ".husky")  # unchanged
            forced = self._install_hook(root, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(self._hooks_path(root), ".githooks")

    def test_install_hook_refuses_foreign_precommit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            hook = root / ".githooks" / "pre-commit"
            hook.parent.mkdir()
            hook.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
            refused = self._install_hook(root)
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertIn("echo custom", hook.read_text(encoding="utf-8"))  # unchanged
            forced = self._install_hook(root, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertIn(privacy_gate.HOOK_MARKER, hook.read_text(encoding="utf-8"))

    def test_install_hook_idempotent_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._init_repo(root)
            self.assertEqual(self._install_hook(root).returncode, 0)
            hook = root / ".githooks" / "pre-commit"
            fresh = hook.read_text(encoding="utf-8")
            # Simulate a stale managed hook (our marker, outdated body).
            hook.write_text(f"#!/bin/sh\n# {privacy_gate.HOOK_MARKER}\npython3 /old/path.py scan\n",
                            encoding="utf-8")
            again = self._install_hook(root)  # no --force: our marker allows refresh
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(hook.read_text(encoding="utf-8"), fresh)  # refreshed to current

    def _sanitize(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "sanitize", *args],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def test_sanitize_under_work_ancestor_not_refused(self):
        # PG3: an ancestor directory named work/ must not falsely block an
        # explicitly chosen sanitize target.
        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir) / "work"
            work.mkdir()
            target = work / "notes.txt"
            email = "jane" + "@" + "private.test"
            target.write_text("contact " + email + "\n", encoding="utf-8")
            result = self._sanitize("--path", str(target), "--write")
            content = target.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<REDACTED_EMAIL>", content)
        self.assertNotIn(email, content)

    def test_sanitize_preview_shows_redacted_lines_not_raw_pii(self):
        # PG5: dry-run previews the change as redacted lines, never echoing the
        # raw value, and does not modify the file.
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "notes.txt"
            email = "jane" + "@" + "private.test"
            original = "email " + email + "\n"
            target.write_text(original, encoding="utf-8")
            result = self._sanitize("--path", str(target))
            after = target.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would redact", result.stdout)
        self.assertIn("<REDACTED_EMAIL>", result.stdout)
        self.assertNotIn(email, result.stdout)
        self.assertEqual(after, original)  # dry-run leaves the file untouched

    def test_sanitize_missing_file_clean_error(self):
        # PG7: missing path exits 2 cleanly, no traceback.
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._sanitize("--path", str(Path(tmpdir) / "nope.txt"))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("cannot read", result.stderr)

    def test_sanitize_directory_clean_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._sanitize("--path", tmpdir)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_strict_flag_fails_on_warnings(self):
        # PG6: --strict is a real alias for --fail-on-warn (no longer a no-op).
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "notes.txt"
            email = "jane" + "@" + "private.test"
            target.write_text("contact " + email + "\n", encoding="utf-8")

            def run(*flags):
                return subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), "scan", "--path", str(target), *flags],
                    check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ).returncode

            # A PII warning does not fail the default gate, but does under --strict.
            self.assertEqual(run(), 0)
            self.assertEqual(run("--strict"), 1)
            self.assertEqual(run("--fail-on-warn"), 1)

    def test_generated_hook_uses_block_only_default(self):
        # The installed hook must use the block-only default so PII does not
        # hard-block every commit.
        self.assertIn("scan --staged\n", privacy_gate.hook_body())
        self.assertNotIn("--strict", privacy_gate.hook_body())

    def test_inline_allow_suppresses_line(self):
        # Chunk 6: an explicit per-line marker suppresses content findings on
        # that line only; the same content without the marker still blocks.
        secret = "sk-" + ("A" * 32)
        allowed = self.scan_text("c.txt", "OPENAI_API_KEY=" + secret + "  # privacy-gate: allow\n")
        self.assertEqual(allowed, [])
        blocked = self.scan_text("c.txt", "OPENAI_API_KEY=" + secret + "\n")
        self.assertTrue(any(item.severity == "block" for item in blocked))

    def test_inline_allow_does_not_bypass_file_block(self):
        # A marker in content must not bypass a file-level block (.env).
        findings = self.scan_text(".env", "API=1  # privacy-gate: allow\n")
        self.assertTrue(any(item.code == "secret_env_file" for item in findings))

    def test_ignore_file_skips_and_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".privacygateignore").write_text("fixtures\n", encoding="utf-8")
            (root / "fixtures").mkdir()
            secret = "sk-" + ("A" * 32)
            (root / "fixtures" / "sample.txt").write_text("OPENAI_API_KEY=" + secret + "\n", encoding="utf-8")
            (root / "real.txt").write_text("OPENAI_API_KEY=" + secret + "\n", encoding="utf-8")
            result = privacy_gate.scan_path(root)
        self.assertTrue(any(f.path == "real.txt" and f.severity == "block" for f in result.findings))
        self.assertFalse(any(f.path.startswith("fixtures/") for f in result.findings))
        self.assertGreaterEqual(result.skipped_files, 1)

    def test_ignore_file_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "outside_patterns").write_text("*\n", encoding="utf-8")
            link = root / ".privacygateignore"
            try:
                link.symlink_to(root / "outside_patterns")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            secret = "sk-" + ("A" * 32)
            (root / "leak.txt").write_text("OPENAI_API_KEY=" + secret + "\n", encoding="utf-8")
            result = privacy_gate.scan_path(root)
        # A symlinked ignore file is not honored, so nothing is skipped by it.
        self.assertTrue(any(f.severity == "block" and f.path == "leak.txt" for f in result.findings))
        self.assertEqual(result.skipped_files, 0)

    def test_report_never_prints_secret_value(self):
        secret = "sk-ant-" + ("A" * 28)
        findings = self.scan_text("c.txt", "ANTHROPIC_API_KEY=" + secret + "\n")
        self.assertTrue(findings)
        for item in findings:
            self.assertNotIn(secret, item.message)
            self.assertNotIn(secret, item.remediation)
            self.assertNotIn(secret, item.path)
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "c.txt"
            target.write_text("ANTHROPIC_API_KEY=" + secret + "\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "scan", "--path", str(target), "--json"],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout)

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
