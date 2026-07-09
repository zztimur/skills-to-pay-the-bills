#!/usr/bin/env python3
"""Scan repository content for likely secrets and private data."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Sequence, Set, Tuple

MAX_TEXT_BYTES = 1_000_000

# Adoptability without disabling the gate. Both mechanisms leave a visible,
# in-repo audit trail (the marker sits on the suppressed line; the ignore file
# is committed and its skip count is reported), so nothing is hidden silently.
IGNORE_FILE_NAME = ".privacygateignore"
# Per-line escape hatches for reviewed false positives, both leaving a visible
# in-diff audit trail. `allow` suppresses WARN-level content findings (PII) only.
# `allow-secret` also suppresses BLOCK-level content findings (secrets) - a
# louder, deliberately distinct marker so a real secret is never waved through
# by the softer PII marker. Neither affects file-level blocks (binary, .env).
INLINE_ALLOW_PATTERN = re.compile(r"privacy-gate:\s*allow\b", re.IGNORECASE)
INLINE_ALLOW_SECRET_PATTERN = re.compile(r"privacy-gate:\s*allow-secret\b", re.IGNORECASE)

# Directories never worth scanning: VCS metadata, tool caches, vendored deps,
# and virtualenvs. NOT build/ or dist/ - those ship in packages, so they are
# scanned. Skips are counted and reported (see ScanResult.skipped_dirs).
SKIP_DIR_NAMES = {
    ".git",
    ".git-rewrite",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "coverage",
    "htmlcov",
}

GENERATED_DIR_NAMES = {"work", "outputs"}

ALLOWED_ENV_EXAMPLES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.defaults",
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

# Assignments are found by scanning for the separator and reading the name
# backwards in Python (see iter_assignments). Any greedy "identifier then
# separator" regex backtracks O(n^2) on long identifier-like input with no
# reachable separator, which is a denial-of-service risk for a commit/CI gate.
_ASSIGNMENT_SEPARATOR = re.compile(r"[:=]")
_ASSIGNMENT_VALUE = re.compile(r"['\"]?([^'\"\s#]+)")

# A credential keyword that is the whole name or its trailing component
# (DJANGO_SECRET_KEY, AWS_SECRET_ACCESS_KEY). Longest forms first.
CRED_NAME_SUFFIX = re.compile(
    r"(?:^|[_-])(?:secret[_-]?access[_-]?key|api[_-]?key|secret[_-]?key|access[_-]?key"
    r"|access[_-]?token|client[_-]?secret|refresh[_-]?token|auth[_-]?token)$",
    re.IGNORECASE,
)
PASSWORD_NAME_SUFFIX = re.compile(r"(?:^|[_-])(?:password|passwd)$", re.IGNORECASE)

# A credential embedded in a connection-string / URL userinfo:
# scheme://user:password@host (postgres, mysql, mongodb, redis, amqp, ...).
# The user part is optional (redis://:pass@host); the password is captured so
# placeholders and interpolations can be filtered before blocking.
CONNECTION_STRING_PATTERN = re.compile(
    r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]*:([^\s/@]+)@",
    re.IGNORECASE,
)
# Literal words that are the textbook connection-string placeholder rather than
# a real credential (postgres://user:password@localhost). Kept separate from
# PLACEHOLDER_WORDS so this precision only relaxes the URL-userinfo detector.
CONNECTION_STRING_PLACEHOLDERS = frozenset(
    {"password", "passwd", "pass", "pwd", "secret", "user", "username", "credentials", "token"}
)

# A dotted attribute chain such as settings.SECRET_KEY or os.environ.get.
_DOTTED_REFERENCE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def looks_like_code_reference(value: str) -> bool:
    """True when an assignment value reads a secret rather than hardcoding one.

    Real secret literals do not contain call/subscript/interpolation syntax, and
    a bare dotted attribute chain (settings.SECRET_KEY) is a reference. JWT-style
    dotted tokens are still caught by the dedicated JWT content pattern, so this
    filter does not open a hole for them.
    """
    stripped = value.strip().strip("'\"")
    if any(character in stripped for character in "()[]{}$"):
        return True
    return _DOTTED_REFERENCE.fullmatch(stripped) is not None


def iter_assignments(line: str) -> Iterable[Tuple[str, str]]:
    """Yield (name, value) for each `name = value` / `name: value` on the line.

    Linear time: find each separator, then read the trailing identifier to its
    left and the value to its right. No greedy identifier-then-separator regex,
    so long identifier-like input cannot trigger catastrophic backtracking.
    """
    for separator in _ASSIGNMENT_SEPARATOR.finditer(line):
        left = line[: separator.start()].rstrip()
        end = len(left)
        # A quoted key such as "api_key": ... or 'password': ... in JSON/dict
        # literals: step over the closing quote so the name read below sees the
        # identifier instead of stopping dead on the quote.
        if end > 0 and left[end - 1] in "'\"":
            end -= 1
        start = end
        while start > 0 and (left[start - 1].isalnum() or left[start - 1] in "_-"):
            start -= 1
        name = left[start:end]
        if not name:
            continue
        value_match = _ASSIGNMENT_VALUE.match(line[separator.end():].lstrip())
        if value_match:
            yield name, value_match.group(1)

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
    skipped_files: int = 0
    skipped_dirs: List[str] = field(default_factory=list)  # structural skips (display paths)


def load_ignore_patterns(base: Path) -> List[str]:
    """Read committed .privacygateignore glob patterns from the scan base.

    The file is regular-only (a symlinked ignore file is refused, matching the
    scanner's no-follow policy) and comment/blank lines are dropped.
    """
    ignore_file = base / IGNORE_FILE_NAME
    if ignore_file.is_symlink() or not ignore_file.is_file():
        return []
    patterns: List[str] = []
    for line in ignore_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped.rstrip("/"))
    return patterns


def is_ignored(display_path: str, patterns: Sequence[str]) -> bool:
    """True if the path or any ancestor directory matches an ignore glob."""
    if not patterns:
        return False
    posix = normalize_display_path(display_path)
    parts = posix.split("/")
    prefixes = ["/".join(parts[: index + 1]) for index in range(len(parts))]
    for pattern in patterns:
        for candidate in prefixes:
            if fnmatch.fnmatch(candidate, pattern):
                return True
    return False


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
        allow_secret = bool(INLINE_ALLOW_SECRET_PATTERN.search(line))
        # `allow-secret` implies `allow`; `\ballow\b` also matches inside
        # "allow-secret", so allow_any is true for either marker.
        allow_any = allow_secret or bool(INLINE_ALLOW_PATTERN.search(line))

        if not allow_secret:
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

            cred_flagged = password_flagged = False
            for name, value in iter_assignments(line):
                # A value that reads a secret (os.environ.get(...), settings.SECRET_KEY)
                # or is a placeholder is not a hardcoded credential.
                if is_placeholder_value(value) or looks_like_code_reference(value):
                    continue
                if not cred_flagged and len(value) >= 12 and CRED_NAME_SUFFIX.search(name):
                    add_line_finding(
                        findings,
                        "block",
                        "secret_assignment",
                        display_path,
                        line_number,
                        "possible API key, token, or client secret assignment found",
                        "Remove the credential and rotate it if it was real.",
                    )
                    cred_flagged = True
                elif not password_flagged and len(value) >= 8 and PASSWORD_NAME_SUFFIX.search(name):
                    add_line_finding(
                        findings,
                        "block",
                        "secret_password_assignment",
                        display_path,
                        line_number,
                        "possible password assignment found",
                        "Remove the password and rotate it if it was real.",
                    )
                    password_flagged = True

            for match in CONNECTION_STRING_PATTERN.finditer(line):
                userinfo_pw = match.group(1)
                # Skip placeholders (postgres://user:password@host) and
                # interpolations (${PW}, {pw}, $PW) - those are not literals.
                if is_placeholder_value(userinfo_pw) or userinfo_pw.lower() in CONNECTION_STRING_PLACEHOLDERS:
                    continue
                if "{" in userinfo_pw or "}" in userinfo_pw or userinfo_pw.startswith("$"):
                    continue
                add_line_finding(
                    findings,
                    "block",
                    "secret_connection_string",
                    display_path,
                    line_number,
                    "possible password in a connection string found",
                    "Remove the inline credential and rotate it; use an env var or secret store.",
                )
                break

        if not allow_any:
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


def _safe_git_root(start: Path) -> Optional[Path]:
    """Git toplevel for `start`, or None if not a repo / git is unavailable.

    Swallows a missing git binary so --path scans never require git."""
    try:
        result = run_git(["rev-parse", "--show-toplevel"], cwd=start, check=False)
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.decode("utf-8", errors="replace").strip())


def policy_display_for_file(path: Path) -> str:
    """Display path for an explicitly named file, preserving ancestor dirs
    (work/, outputs/) so path policy applies. Relative to the git root if the
    file is inside a repo, else CWD, else the path as given."""
    resolved = path.resolve()
    for base in (_safe_git_root(resolved.parent), Path.cwd()):
        if base is not None:
            try:
                return normalize_display_path(str(resolved.relative_to(base)))
            except ValueError:
                continue
    return normalize_display_path(str(path))


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
    # Ignore patterns apply to directory scans only; an explicitly named single
    # file is always scanned so the user is never surprised by a silent skip.
    ignore_patterns = load_ignore_patterns(base) if path.is_dir() else []
    skipped = 0
    skipped_dirs: List[str] = []
    single_file_display: Optional[str] = None
    if path.is_file():
        candidate_files = [path]
        single_file_display = policy_display_for_file(path)
    else:
        candidate_files = []
        for current_root, dirnames, filenames in os.walk(path):
            current = Path(current_root)
            kept_dirnames = []
            for dirname in sorted(dirnames):
                dir_path = current / dirname
                display = relative_display_path(dir_path, base)
                if dirname in SKIP_DIR_NAMES:
                    skipped_dirs.append(display)
                    continue
                if dir_path.is_symlink():
                    findings.append(symlink_finding(display))
                    continue
                kept_dirnames.append(dirname)
            dirnames[:] = kept_dirnames

            for filename in sorted(filenames):
                candidate_files.append(current / filename)

    for file_path in candidate_files:
        display = single_file_display if single_file_display is not None else relative_display_path(file_path, base)
        if is_ignored(display, ignore_patterns):
            skipped += 1
            continue
        if file_path.is_symlink():
            findings.append(symlink_finding(display))
            continue
        try:
            data = read_limited_bytes(file_path)
        except OSError as exc:
            findings.append(
                Finding(
                    "block",
                    "file_read_failed",
                    display,
                    f"could not read file, so it cannot be inspected: {exc}",
                    remediation="Fix permissions and rescan, or remove the file; unreadable files are blocked fail-closed.",
                )
            )
            continue
        scanned += 1
        findings.extend(scan_bytes(display, data))
    return ScanResult(scanned, findings, skipped, skipped_dirs)


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


def staged_gitlinks(root: Path, paths: Sequence[str]) -> Set[str]:
    """Staged paths that are submodule gitlinks (mode 160000) rather than blobs.

    `git show :<path>` fails on these (the index entry is a commit reference, not a
    blob), which without this check surfaces as a false-positive staged_blob_read_failed
    warning on every submodule bump.
    """
    if not paths:
        return set()
    result = run_git(["ls-files", "-s", "-z", "--", *paths], cwd=root)
    gitlinks: Set[str] = set()
    for entry in result.stdout.split(b"\x00"):
        if not entry:
            continue
        meta, _, entry_path = entry.partition(b"\t")
        if meta.split(b" ", 1)[0] == b"160000":
            gitlinks.add(entry_path.decode("utf-8", errors="surrogateescape"))
    return gitlinks


def staged_symlinks(root: Path, paths: Sequence[str]) -> Set[str]:
    """Staged paths that are symlinks (mode 120000) rather than regular blobs.

    A staged symlink's blob is just its target path string, so reading it as
    content only ever sees that path - the real target is never inspected and a
    secret it points to slips through. Path scans already block symlinks; block
    them here too so `--staged` matches that policy instead of waving them by.
    """
    if not paths:
        return set()
    result = run_git(["ls-files", "-s", "-z", "--", *paths], cwd=root)
    symlinks: Set[str] = set()
    for entry in result.stdout.split(b"\x00"):
        if not entry:
            continue
        meta, _, entry_path = entry.partition(b"\t")
        if meta.split(b" ", 1)[0] == b"120000":
            symlinks.add(entry_path.decode("utf-8", errors="surrogateescape"))
    return symlinks


def scan_staged() -> ScanResult:
    root = git_root()
    ignore_patterns = load_ignore_patterns(root)
    paths = staged_paths(root)
    gitlinks = staged_gitlinks(root, paths)
    symlinks = staged_symlinks(root, paths)
    findings: List[Finding] = []
    scanned = 0
    skipped = 0
    for path in paths:
        if is_ignored(path, ignore_patterns):
            skipped += 1
            continue
        if path in gitlinks:
            skipped += 1
            continue
        if path in symlinks:
            findings.append(symlink_finding(path))
            continue
        try:
            data = staged_blob(root, path)
        except RuntimeError as exc:
            findings.append(
                Finding(
                    "block",
                    "staged_blob_read_failed",
                    path,
                    f"could not read staged blob, so it cannot be inspected: {exc}",
                    remediation="Resolve the staged blob and rescan; uninspectable staged content is blocked fail-closed.",
                )
            )
            continue
        scanned += 1
        findings.extend(scan_bytes(path, data))
    return ScanResult(scanned, findings, skipped)


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
    try:
        data = path.read_bytes()
    except OSError as exc:
        # Missing file, a directory argument, or a permission error must exit
        # cleanly, not raise an uncaught traceback (main only catches RuntimeError).
        print(f"Privacy Gate: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    text = decode_text(data)
    if text is None:
        print(f"Privacy Gate: cannot sanitize binary file: {path}", file=sys.stderr)
        return 2

    # Evaluate block policy on the basename only. The sanitizer acts on one
    # explicitly chosen file, so an ancestor directory named work/ or outputs/
    # must not falsely refuse it; credential content is still refused.
    block_findings = [item for item in scan_bytes(path.name, data) if item.severity == "block"]
    if block_findings:
        print("Privacy Gate: refusing to sanitize credential-like or blocked content.", file=sys.stderr)
        print_findings(block_findings, json_output=False)
        return 2

    redacted = redact_text(text)
    if redacted == text:
        print(f"Privacy Gate: no sanitizer changes needed for {path}")
        return 0

    # Preview the change as redacted (post-substitution) lines with line numbers.
    # This shows exactly what will be written without echoing the raw PII values.
    changes = [
        (index + 1, new_line)
        for index, (old_line, new_line) in enumerate(zip(text.splitlines(), redacted.splitlines()))
        if old_line != new_line
    ]
    verb = "Redacted" if write else "Would redact"
    print(f"Privacy Gate: {verb} {len(changes)} line(s) in {path}:")
    for line_number, new_line in changes:
        print(f"  {line_number}: {new_line}")

    if write:
        try:
            path.write_text(redacted, encoding="utf-8")
        except OSError as exc:
            print(f"Privacy Gate: cannot write {path}: {exc}", file=sys.stderr)
            return 2
        print(f"Privacy Gate: wrote sanitized {path}")
    else:
        print("Privacy Gate: rerun with --write to apply.")
    return 0


HOOKS_DIR_NAME = ".githooks"
# Present in every hook this tool writes; used to tell our managed hook apart
# from a hook the user authored, so a refresh never clobbers a foreign hook.
HOOK_MARKER = "Installed by Privacy Gate"


def hook_body(portable: bool = False) -> str:
    """Render the managed pre-commit hook.

    Scanner resolution at run time: a PRIVACY_GATE_SCRIPT override, then a
    repo-vendored privacy-gate/ copy, then (unless portable) the absolute path of
    the installing script. Portable mode omits the absolute path so the committed
    hook carries no machine-specific home directory - use it for shared repos,
    paired with vendoring or PRIVACY_GATE_SCRIPT.
    """
    lines = [
        "#!/usr/bin/env sh",
        "set -eu",
        "",
        f"# {HOOK_MARKER}. The block below resolves the scanner across layouts;",
        "# do not hardcode a single path here.",
        'repo_root="$(git rev-parse --show-toplevel)"',
        'cd "$repo_root"',
        "",
        'if [ -n "${PRIVACY_GATE_SCRIPT:-}" ] && [ -f "${PRIVACY_GATE_SCRIPT}" ]; then',
        '  script="${PRIVACY_GATE_SCRIPT}"',
        'elif [ -f "privacy-gate/scripts/privacy_gate.py" ]; then',
        '  script="privacy-gate/scripts/privacy_gate.py"',
    ]
    if not portable:
        script_path = Path(__file__).resolve()
        lines += [f'elif [ -f "{script_path}" ]; then', f'  script="{script_path}"']
    lines += [
        "else",
        '  echo "Privacy Gate: scanner not found; set PRIVACY_GATE_SCRIPT or vendor privacy-gate/." >&2',
        "  exit 1",
        "fi",
        "",
        # Default gate: block the commit on high-confidence secrets, print PII
        # warnings without blocking. Add --strict here to also fail on warnings.
        'python3 "$script" scan --staged',
        "",
    ]
    return "\n".join(lines)


def write_managed_hook(hook_path: Path, portable: bool = False) -> None:
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(hook_body(portable), encoding="utf-8")
    current_mode = hook_path.stat().st_mode
    hook_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def default_pre_commit_hook(root: Path) -> Path:
    """Path to the default-location pre-commit hook ($GIT_DIR/hooks/pre-commit)."""
    result = run_git(["rev-parse", "--git-path", "hooks/pre-commit"], cwd=root)
    raw = result.stdout.decode("utf-8", errors="replace").strip()
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (root / candidate)


def current_hooks_path(root: Path) -> str:
    result = run_git(["config", "--get", "core.hooksPath"], cwd=root, check=False)
    return result.stdout.decode("utf-8", errors="replace").strip()


def install_hook(force: bool = False, portable: bool = False) -> int:
    root = git_root()
    hook_path = root / HOOKS_DIR_NAME / "pre-commit"

    # Never silently overwrite a pre-commit hook we did not author. Our own
    # managed hook carries HOOK_MARKER, so re-running is a safe idempotent
    # refresh (it also updates a stale embedded script path).
    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER not in existing and not force:
            print(
                f"Privacy Gate: {hook_path} already exists and was not created by Privacy Gate. "
                "Re-run with --force to replace it, or call the scan from your own hook.",
                file=sys.stderr,
            )
            return 2

    # Never silently hijack another hook manager (husky, pre-commit, lefthook).
    existing_hooks_path = current_hooks_path(root)
    if existing_hooks_path and existing_hooks_path != HOOKS_DIR_NAME and not force:
        print(
            f"Privacy Gate: core.hooksPath is already set to '{existing_hooks_path}'; another hook "
            f"manager may own it. Re-run with --force to point it at '{HOOKS_DIR_NAME}', or add "
            "'privacy_gate.py scan --staged' to that manager's pre-commit step instead.",
            file=sys.stderr,
        )
        return 2

    # core.hooksPath is unset but a default-location hook already runs. Pointing
    # core.hooksPath at .githooks would silently disable it - refuse unless forced.
    if not existing_hooks_path and not force:
        default_hook = default_pre_commit_hook(root)
        if default_hook.is_file():
            print(
                f"Privacy Gate: a pre-commit hook already exists at {default_hook}. "
                f"Setting core.hooksPath to '{HOOKS_DIR_NAME}' would silently disable it. "
                "Re-run with --force to take over, or add 'privacy_gate.py scan --staged' "
                "to that hook instead.",
                file=sys.stderr,
            )
            return 2

    write_managed_hook(hook_path, portable)
    run_git(["config", "core.hooksPath", HOOKS_DIR_NAME], cwd=root)
    print(f"Privacy Gate: installed Git hook at {hook_path}")
    print(f"Privacy Gate: core.hooksPath is now {HOOKS_DIR_NAME}")
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
            "skipped_files": result.skipped_files,
            "skipped_dirs": result.skipped_dirs,
            "block_count": block_count,
            "warning_count": warning_count,
            "findings": [asdict(item) for item in sorted(result.findings, key=finding_sort_key)],
            "note": "Privacy Gate scanning is heuristic and non-exhaustive.",
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    notes = []
    if result.skipped_files:
        notes.append(f"{result.skipped_files} skipped via {IGNORE_FILE_NAME}")
    if result.skipped_dirs:
        names = sorted({PurePosixPath(d).name for d in result.skipped_dirs})
        notes.append(f"{len(result.skipped_dirs)} dir(s) skipped structurally: {', '.join(names)}")
    skipped_note = f" ({'; '.join(notes)})" if notes else ""
    if not result.findings:
        print(f"Privacy Gate: pass. Scanned {result.scanned_files} file(s){skipped_note}; no findings.")
        return

    print(
        f"Privacy Gate: scanned {result.scanned_files} file(s){skipped_note}; "
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
    return exit_code_for(result, args.fail_on_warn or args.strict)


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
    scan.add_argument("--strict", action="store_true", help="strict CI gate: warnings fail too (alias for --fail-on-warn)")
    scan.add_argument("--fail-on-warn", action="store_true", help="exit nonzero for warning findings too")
    scan.add_argument("--json", action="store_true", help="emit structured JSON")
    scan.set_defaults(func=command_scan)

    sanitize = subparsers.add_parser("sanitize", help="redact PII-like text patterns in one text file")
    sanitize.add_argument("--path", required=True, help="text file to sanitize")
    sanitize.add_argument("--write", action="store_true", help="write redactions in place")
    sanitize.set_defaults(func=lambda args: sanitize_path(Path(args.path), args.write))

    hook = subparsers.add_parser("install-hook", help="configure this repo to use .githooks")
    hook.add_argument(
        "--force",
        action="store_true",
        help="replace a foreign pre-commit hook or reassign an existing core.hooksPath",
    )
    hook.add_argument(
        "--portable",
        action="store_true",
        help="omit the installer's absolute path; rely on a vendored copy or PRIVACY_GATE_SCRIPT (use for shared repos)",
    )
    hook.set_defaults(func=lambda args: install_hook(args.force, args.portable))

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError:
        print(
            "Privacy Gate: 'git' was not found on PATH. Install Git, or use "
            "'scan --path <path>', which does not require Git.",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as exc:
        print(f"Privacy Gate: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Privacy Gate: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
