#!/usr/bin/env python3
"""Scan repository content for likely secrets and private data."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Sequence, Tuple

MAX_TEXT_BYTES = 1_000_000

SKIP_DIR_NAMES = {
    ".git",
    ".git-rewrite",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "venv",
}

GENERATED_DIR_NAMES = {"work", "outputs"}

ALLOWED_ENV_EXAMPLES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.defaults",
}

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

BLOCK_BINARY_SUFFIXES = {
    ".doc",
    ".docx",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".numbers",
    ".ods",
    ".odt",
    ".pdf",
    ".png",
    ".tif",
    ".tiff",
    ".xls",
    ".xlsm",
    ".xlsx",
}

SECRET_FILENAME_PATTERNS = [
    re.compile(r"(^|/)(id_rsa|id_dsa|id_ed25519|id_ecdsa)$", re.IGNORECASE),
    re.compile(r"private[-_]?key|service[-_]?account|\.pem$|\.p12$|\.pfx$", re.IGNORECASE),
]

SUSPICIOUS_FILENAME_PATTERNS = [
    re.compile(r"secret|credential|token", re.IGNORECASE),
]

SECRET_CONTENT_PATTERNS = [
    (
        "secret_private_key_block",
        "private key block",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        "Remove the private key from the repo and rotate it.",
    ),
    (
        "secret_anthropic_api_key",
        "Anthropic-style API key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        "Remove the API key and rotate it.",
    ),
    (
        "secret_openai_api_key",
        "OpenAI API key",
        re.compile(r"\bsk-(?!ant-)(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
        "Remove the API key and rotate it.",
    ),
    (
        "secret_github_token",
        "GitHub token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
        "Remove the GitHub token and rotate it.",
    ),
    (
        "secret_slack_token",
        "Slack token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
        "Remove the Slack token and rotate it.",
    ),
    (
        "secret_google_service_account",
        "Google service account JSON",
        re.compile(r'"type"\s*:\s*"service_account"'),
        "Remove the service-account file and rotate the credential.",
    ),
    (
        "secret_jwt_like_token",
        "JWT-like token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "Remove the token and rotate it if it was real.",
    ),
    (
        "secret_github_fine_grained_pat",
        "GitHub fine-grained token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
        "Remove the GitHub token and rotate it.",
    ),
    (
        "secret_aws_access_key_id",
        "AWS access key ID",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "Remove the AWS key, rotate it, and delete the key pair in IAM.",
    ),
    (
        "secret_stripe_key",
        "Stripe live secret key",
        re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}\b"),
        "Remove the Stripe key and roll it in the Stripe dashboard.",
    ),
    (
        "secret_google_api_key",
        "Google API key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "Remove the Google API key and regenerate it.",
    ),
    (
        "secret_gitlab_pat",
        "GitLab personal access token",
        re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}\b"),
        "Remove the GitLab token and revoke it.",
    ),
    (
        "secret_npm_token",
        "npm access token",
        re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
        "Remove the npm token and revoke it.",
    ),
]

# Match a credential keyword even when it is the trailing component of a longer
# snake_case/kebab identifier (DJANGO_SECRET_KEY, AWS_SECRET_ACCESS_KEY). A bare
# \b boundary fails there because "_" is a word character; the optional prefix
# below is what closes that false-negative. Longest keyword forms come first so
# the alternation prefers the most specific match.
_CRED_KEYWORD = (
    r"(?:secret[_-]?access[_-]?key|api[_-]?key|secret[_-]?key|access[_-]?key"
    r"|access[_-]?token|client[_-]?secret|refresh[_-]?token|auth[_-]?token)"
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?:[A-Za-z0-9]+[_-])*" + _CRED_KEYWORD + r"(?![A-Za-z0-9])"
    r"\s*[:=]\s*['\"]?([^'\"\s#]+)"
)
PASSWORD_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[_-])*(?:password|passwd)(?![A-Za-z0-9])"
    r"\s*[:=]\s*['\"]?([^'\"\s#]+)"
)

EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?[2-9]\d{2}\)?[-.\s]?)[2-9]\d{2}[-.\s]?\d{4}(?!\d)"
)
SSN_ITIN_PATTERN = re.compile(r"\b(?:\d{3}-\d{2}-\d{4}|9\d{2}-[78]\d-\d{4})\b")
EIN_PATTERN = re.compile(r"\b\d{2}-\d{7}\b")
IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
ACCOUNT_CONTEXT_PATTERN = re.compile(
    r"(?i)\b(account\s*(?:number|no\.?|#)|routing\s*(?:number|no\.?|#)|sort\s*code|iban|swift|bic)\b"
)
ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.' -]{2,50}\s+"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Lane|Ln\.?|Court|Ct\.?|Way|Place|Pl\.?)\b",
    re.IGNORECASE,
)

EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net", "test.com", "localhost"}
PLACEHOLDER_WORDS = {
    "changeme",
    "example",
    "fake",
    "placeholder",
    "redacted",
    "replace",
    "sample",
    "test",
    "todo",
    "your",
}


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str
    line: Optional[int] = None
    remediation: str = ""

    def label(self) -> str:
        return "BLOCK" if self.severity == "block" else "WARN"


@dataclass
class ScanResult:
    scanned_files: int
    findings: List[Finding]


def normalize_display_path(path: str) -> str:
    return path.replace(os.sep, "/")


def path_parts(display_path: str) -> Tuple[str, ...]:
    return PurePosixPath(normalize_display_path(display_path)).parts


def is_allowed_env_example(name: str) -> bool:
    return name.lower() in ALLOWED_ENV_EXAMPLES


def is_env_secret_file(name: str) -> bool:
    """Return True for environment files that likely hold local credentials.

    Covers the dotfile forms (`.env`, `.env.production`) and suffix forms
    (`prod.env`, `staging.env`). Known example/template names are allowed, and a
    placeholder-word stem such as `example.env` or `sample.env` is treated as a
    template rather than a real secrets file.
    """
    lower = name.lower()
    if is_allowed_env_example(lower):
        return False
    if lower == ".env" or lower.startswith(".env."):
        return True
    if lower.endswith(".env"):
        stem = lower[: -len(".env")].strip(".")
        return stem not in PLACEHOLDER_WORDS
    return False


def is_placeholder_value(value: str) -> bool:
    stripped = value.strip().strip("'\"")
    lowered = stripped.lower()
    if not stripped:
        return True
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    if stripped.startswith("${") and stripped.endswith("}"):
        return True
    if stripped.startswith("$"):
        return True
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    return any(word in normalized.split() for word in PLACEHOLDER_WORDS)


def is_text_bytes(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:MAX_TEXT_BYTES].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def decode_text(data: bytes) -> Optional[str]:
    if not is_text_bytes(data):
        return None
    return data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")


def path_policy_findings(display_path: str) -> List[Finding]:
    normalized = normalize_display_path(display_path)
    parts = path_parts(normalized)
    basename = parts[-1] if parts else normalized
    lower_basename = basename.lower()
    suffix = Path(basename).suffix.lower()
    findings: List[Finding] = []

    if any(part in GENERATED_DIR_NAMES for part in parts[:-1]):
        findings.append(
            Finding(
                "block",
                "generated_artifact_path",
                normalized,
                "file is inside a generated work/output artifact directory",
                remediation="Move generated artifacts out of Git or regenerate a sanitized source artifact.",
            )
        )

    if is_env_secret_file(lower_basename):
        findings.append(
            Finding(
                "block",
                "secret_env_file",
                normalized,
                "environment files may contain local credentials",
                remediation="Remove the environment file from the repo; commit only placeholder examples.",
            )
        )

    if suffix in BLOCK_BINARY_SUFFIXES:
        findings.append(
            Finding(
                "block",
                "private_binary_export",
                normalized,
                "binary/private export formats cannot be safely sanitized by this text scanner",
                remediation="Do not commit PDFs, images, spreadsheets, or office exports unless a sanitized source artifact is intentionally reviewed.",
            )
        )

    for pattern in SECRET_FILENAME_PATTERNS:
        if pattern.search(normalized):
            findings.append(
                Finding(
                    "block",
                    "secret_filename",
                    normalized,
                    "filename suggests a private key or service credential",
                    remediation="Remove the credential file and rotate it if it was real.",
                )
            )
            break

    if not findings:
        for pattern in SUSPICIOUS_FILENAME_PATTERNS:
            if pattern.search(normalized):
                findings.append(
                    Finding(
                        "warning",
                        "suspicious_credential_filename",
                        normalized,
                        "filename looks credential-related",
                        remediation="Confirm this is documentation or a placeholder, not a bundled secret.",
                    )
                )
                break

    return findings


def luhn_valid(digits: str) -> bool:
    total = 0
    reverse_digits = list(map(int, reversed(digits)))
    for index, digit in enumerate(reverse_digits):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def is_example_email(match: re.Match[str]) -> bool:
    domain = match.group(1).lower()
    return domain in EXAMPLE_DOMAINS or domain.endswith(".example")


def add_line_finding(
    findings: List[Finding],
    severity: str,
    code: str,
    display_path: str,
    line_number: int,
    message: str,
    remediation: str,
) -> None:
    findings.append(
        Finding(
            severity=severity,
            code=code,
            path=normalize_display_path(display_path),
            line=line_number,
            message=message,
            remediation=remediation,
        )
    )


def scan_text_content(display_path: str, text: str) -> List[Finding]:
    findings: List[Finding] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        for code, label, pattern, remediation in SECRET_CONTENT_PATTERNS:
            if pattern.search(line):
                add_line_finding(
                    findings,
                    "block",
                    code,
                    display_path,
                    line_number,
                    f"possible {label} found",
                    remediation,
                )

        for match in ASSIGNMENT_PATTERN.finditer(line):
            value = match.group(1)
            if len(value) >= 12 and not is_placeholder_value(value):
                add_line_finding(
                    findings,
                    "block",
                    "secret_assignment",
                    display_path,
                    line_number,
                    "possible API key, token, or client secret assignment found",
                    "Remove the credential and rotate it if it was real.",
                )
                break

        for match in PASSWORD_ASSIGNMENT_PATTERN.finditer(line):
            value = match.group(1)
            if len(value) >= 8 and not is_placeholder_value(value):
                add_line_finding(
                    findings,
                    "block",
                    "secret_password_assignment",
                    display_path,
                    line_number,
                    "possible password assignment found",
                    "Remove the password and rotate it if it was real.",
                )
                break

        for match in EMAIL_PATTERN.finditer(line):
            if not is_example_email(match):
                add_line_finding(
                    findings,
                    "warning",
                    "private_email",
                    display_path,
                    line_number,
                    "possible private email address found",
                    "Redact or replace with a placeholder if this is not public documentation.",
                )
                break

        if PHONE_PATTERN.search(line):
            add_line_finding(
                findings,
                "warning",
                "private_phone",
                display_path,
                line_number,
                "possible phone number found",
                "Redact or replace with a placeholder if this is private.",
            )

        if SSN_ITIN_PATTERN.search(line):
            add_line_finding(
                findings,
                "warning",
                "private_tax_id",
                display_path,
                line_number,
                "possible SSN or ITIN found",
                "Redact tax identifiers before committing.",
            )

        if EIN_PATTERN.search(line):
            add_line_finding(
                findings,
                "warning",
                "private_ein",
                display_path,
                line_number,
                "possible EIN found",
                "Confirm this tax identifier is public or redact it.",
            )

        if IBAN_PATTERN.search(line):
            add_line_finding(
                findings,
                "warning",
                "private_iban",
                display_path,
                line_number,
                "possible IBAN found",
                "Redact bank identifiers before committing.",
            )

        for candidate in CARD_CANDIDATE_PATTERN.finditer(line):
            digits = re.sub(r"\D", "", candidate.group(0))
            if 13 <= len(digits) <= 19 and luhn_valid(digits):
                add_line_finding(
                    findings,
                    "warning",
                    "private_card_number",
                    display_path,
                    line_number,
                    "possible payment card number found",
                    "Redact card numbers before committing.",
                )
                break

        if ACCOUNT_CONTEXT_PATTERN.search(line) and re.search(r"\d{4,}", line):
            add_line_finding(
                findings,
                "warning",
                "private_account_context",
                display_path,
                line_number,
                "possible bank account or routing context found",
                "Redact account and routing details before committing.",
            )

        if ADDRESS_PATTERN.search(line):
            add_line_finding(
                findings,
                "warning",
                "private_address",
                display_path,
                line_number,
                "possible street address found",
                "Redact addresses before committing unless intentionally public.",
            )

    return findings


def scan_bytes(display_path: str, data: bytes) -> List[Finding]:
    findings = path_policy_findings(display_path)
    text = decode_text(data)
    if text is None:
        already_blocked = any(item.severity == "block" for item in findings)
        if not already_blocked:
            findings.append(
                Finding(
                    "block",
                    "binary_file",
                    normalize_display_path(display_path),
                    "binary file cannot be safely inspected by the text scanner",
                    remediation="Remove the file or regenerate a sanitized text artifact.",
                )
        )
        return findings
    if len(data) > MAX_TEXT_BYTES:
        findings.append(
            Finding(
                "block",
                "text_read_limit_exceeded",
                normalize_display_path(display_path),
                "text file exceeds the safe full-read limit, so the scan would be partial",
                remediation="Split, remove, or manually review the file before committing.",
            )
        )
    findings.extend(scan_text_content(display_path, text))
    return findings


def relative_display_path(file_path: Path, base: Path) -> str:
    try:
        display = str(file_path.relative_to(base))
    except ValueError:
        display = str(file_path)
    if display == ".":
        display = file_path.name
    return normalize_display_path(display)


def symlink_finding(display_path: str) -> Finding:
    return Finding(
        "block",
        "symlink_found",
        normalize_display_path(display_path),
        "symlinked paths are not scanned because they can point outside the intended tree",
        remediation="Replace the symlink with an intentionally reviewed regular file or scan the real target path explicitly.",
    )


def read_limited_bytes(file_path: Path) -> bytes:
    with file_path.open("rb") as handle:
        return handle.read(MAX_TEXT_BYTES + 1)


def scan_path(path: Path) -> ScanResult:
    findings: List[Finding] = []
    scanned = 0
    if path.is_symlink():
        return ScanResult(0, [symlink_finding(str(path))])
    if not path.exists():
        return ScanResult(
            0,
            [
                Finding(
                    "block",
                    "scan_path_missing",
                    str(path),
                    "scan path does not exist",
                    remediation="Fix the path and rerun the scan.",
                )
            ],
        )

    base = path if path.is_dir() else path.parent
    if path.is_file():
        candidate_files = [path]
    else:
        candidate_files = []
        for current_root, dirnames, filenames in os.walk(path):
            current = Path(current_root)
            kept_dirnames = []
            for dirname in sorted(dirnames):
                dir_path = current / dirname
                display = relative_display_path(dir_path, base)
                if dirname in SKIP_DIR_NAMES:
                    continue
                if dir_path.is_symlink():
                    findings.append(symlink_finding(display))
                    continue
                kept_dirnames.append(dirname)
            dirnames[:] = kept_dirnames

            for filename in sorted(filenames):
                candidate_files.append(current / filename)

    for file_path in candidate_files:
        display = relative_display_path(file_path, base)
        if file_path.is_symlink():
            findings.append(symlink_finding(display))
            continue
        try:
            data = read_limited_bytes(file_path)
        except OSError as exc:
            findings.append(
                Finding(
                    "warning",
                    "file_read_failed",
                    display,
                    f"could not read file: {exc}",
                    remediation="Inspect this file manually.",
                )
            )
            continue
        scanned += 1
        findings.extend(scan_bytes(display, data))
    return ScanResult(scanned, findings)


def run_git(args: Sequence[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"git {' '.join(args)} failed")
    return result


def git_root() -> Path:
    result = run_git(["rev-parse", "--show-toplevel"])
    return Path(result.stdout.decode("utf-8", errors="replace").strip())


def staged_paths(root: Path) -> List[str]:
    result = run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=root)
    raw_paths = [item for item in result.stdout.split(b"\x00") if item]
    return [item.decode("utf-8", errors="surrogateescape") for item in raw_paths]


def staged_blob(root: Path, path: str) -> bytes:
    result = run_git(["show", f":{path}"], cwd=root)
    return result.stdout


def scan_staged() -> ScanResult:
    root = git_root()
    findings: List[Finding] = []
    scanned = 0
    for path in staged_paths(root):
        try:
            data = staged_blob(root, path)
        except RuntimeError as exc:
            findings.append(
                Finding(
                    "warning",
                    "staged_blob_read_failed",
                    path,
                    f"could not read staged blob: {exc}",
                    remediation="Inspect the staged file manually.",
                )
            )
            continue
        scanned += 1
        findings.extend(scan_bytes(path, data))
    return ScanResult(scanned, findings)


def redact_text(text: str) -> str:
    redacted = EMAIL_PATTERN.sub(
        lambda match: match.group(0) if is_example_email(match) else "<REDACTED_EMAIL>",
        text,
    )
    redacted = PHONE_PATTERN.sub("<REDACTED_PHONE>", redacted)
    redacted = SSN_ITIN_PATTERN.sub("<REDACTED_TAX_ID>", redacted)
    redacted = EIN_PATTERN.sub("<REDACTED_TAX_ID>", redacted)
    redacted = IBAN_PATTERN.sub("<REDACTED_IBAN>", redacted)
    redacted = ADDRESS_PATTERN.sub("<REDACTED_ADDRESS>", redacted)

    def redact_card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            return "<REDACTED_CARD_NUMBER>"
        return match.group(0)

    redacted = CARD_CANDIDATE_PATTERN.sub(redact_card, redacted)

    lines = []
    for line in redacted.splitlines(keepends=True):
        if ACCOUNT_CONTEXT_PATTERN.search(line):
            line = re.sub(r"\d{4,}", "<REDACTED_NUMBER>", line)
        lines.append(line)
    return "".join(lines)


def sanitize_path(path: Path, write: bool) -> int:
    data = path.read_bytes()
    text = decode_text(data)
    if text is None:
        print(f"Privacy Gate: cannot sanitize binary file: {path}", file=sys.stderr)
        return 2

    block_findings = [item for item in scan_bytes(str(path), data) if item.severity == "block"]
    if block_findings:
        print("Privacy Gate: refusing to sanitize credential-like or blocked content.", file=sys.stderr)
        print_findings(block_findings, json_output=False)
        return 2

    redacted = redact_text(text)
    if redacted == text:
        print(f"Privacy Gate: no sanitizer changes needed for {path}")
        return 0

    if write:
        path.write_text(redacted, encoding="utf-8")
        print(f"Privacy Gate: sanitized {path}")
    else:
        print(f"Privacy Gate: sanitizer would modify {path}; rerun with --write to apply.")
    return 0


def ensure_hook_file(root: Path) -> Path:
    hook_path = root / ".githooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    if not hook_path.exists():
        hook_path.write_text(
            "#!/usr/bin/env sh\n"
            "set -eu\n\n"
            'repo_root="$(git rev-parse --show-toplevel)"\n'
            'cd "$repo_root"\n\n'
            "python3 privacy-gate/scripts/privacy_gate.py scan --staged --strict\n",
            encoding="utf-8",
        )
    current_mode = hook_path.stat().st_mode
    hook_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def install_hook() -> int:
    root = git_root()
    hook_path = ensure_hook_file(root)
    run_git(["config", "core.hooksPath", ".githooks"], cwd=root)
    print(f"Privacy Gate: installed Git hook at {hook_path}")
    print("Privacy Gate: core.hooksPath is now .githooks")
    return 0


def finding_sort_key(item: Finding) -> Tuple[int, str, int, str]:
    severity_rank = 0 if item.severity == "block" else 1
    return (severity_rank, item.path, item.line or 0, item.code)


def print_findings(findings: Sequence[Finding], json_output: bool) -> None:
    if json_output:
        print(json.dumps([asdict(item) for item in findings], indent=2, sort_keys=True))
        return
    for item in sorted(findings, key=finding_sort_key):
        location = item.path
        if item.line is not None:
            location = f"{location}:{item.line}"
        print(f"{item.label()} {location} {item.code}: {item.message}")
        if item.remediation:
            print(f"  Fix: {item.remediation}")


def report_scan(result: ScanResult, json_output: bool) -> None:
    block_count = sum(1 for item in result.findings if item.severity == "block")
    warning_count = sum(1 for item in result.findings if item.severity == "warning")
    if json_output:
        payload = {
            "scanned_files": result.scanned_files,
            "block_count": block_count,
            "warning_count": warning_count,
            "findings": [asdict(item) for item in sorted(result.findings, key=finding_sort_key)],
            "note": "Privacy Gate scanning is heuristic and non-exhaustive.",
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if not result.findings:
        print(f"Privacy Gate: pass. Scanned {result.scanned_files} file(s); no findings.")
        return

    print(
        f"Privacy Gate: scanned {result.scanned_files} file(s); "
        f"{block_count} block finding(s), {warning_count} warning(s)."
    )
    print_findings(result.findings, json_output=False)
    print("Note: scanning is heuristic and non-exhaustive.")


def exit_code_for(result: ScanResult, fail_on_warn: bool) -> int:
    has_block = any(item.severity == "block" for item in result.findings)
    has_warning = any(item.severity == "warning" for item in result.findings)
    if has_block or (fail_on_warn and has_warning):
        return 1
    return 0


def command_scan(args: argparse.Namespace) -> int:
    if args.staged:
        result = scan_staged()
    else:
        result = scan_path(Path(args.path))
    report_scan(result, args.json)
    return exit_code_for(result, args.fail_on_warn)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="privacy_gate.py",
        description="Scan repository content for likely secrets and private data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="scan staged content or a file/folder path")
    scan_source = scan.add_mutually_exclusive_group()
    scan_source.add_argument("--staged", action="store_true", help="scan Git staged blobs")
    scan_source.add_argument("--path", default=".", help="file or folder to scan")
    scan.add_argument("--strict", action="store_true", help="hook/CI-friendly alias; block findings fail")
    scan.add_argument("--fail-on-warn", action="store_true", help="exit nonzero for warning findings too")
    scan.add_argument("--json", action="store_true", help="emit structured JSON")
    scan.set_defaults(func=command_scan)

    sanitize = subparsers.add_parser("sanitize", help="redact PII-like text patterns in one text file")
    sanitize.add_argument("--path", required=True, help="text file to sanitize")
    sanitize.add_argument("--write", action="store_true", help="write redactions in place")
    sanitize.set_defaults(func=lambda args: sanitize_path(Path(args.path), args.write))

    hook = subparsers.add_parser("install-hook", help="configure this repo to use .githooks")
    hook.set_defaults(func=lambda args: install_hook())

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RuntimeError as exc:
        print(f"Privacy Gate: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Privacy Gate: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
