---
name: privacy-gate
description: Scan staged changes, proposed Git indexes, or filesystem paths for secrets, private data, and unsafe artifacts; sanitize PII. Use before commit, push, release, packaging, syncing, or sharing.
---

# Privacy Gate

Use this skill when repository content needs a privacy and secret check before commit, push, packaging, installation, or sharing.

## Core Rule

Call the bundled CLI. Do not reimplement detection logic in chat or in a client-specific adapter. `<package-root>` below means the directory containing this SKILL.md (for example the installed skill path or a vendored `privacy-gate/`).

```bash
python3 "<package-root>/scripts/privacy_gate.py" scan --staged
python3 "<package-root>/scripts/privacy_gate.py" scan --index --strict
python3 "<package-root>/scripts/privacy_gate.py" scan --path .
python3 "<package-root>/scripts/privacy_gate.py" sanitize --path FILE --write
python3 "<package-root>/scripts/privacy_gate.py" install-hook
```

The `install-hook` command writes a `.githooks/pre-commit` that resolves the scanner at run time (a vendored copy, an installed absolute path, or the `PRIVACY_GATE_SCRIPT` override), so it keeps working from any repository, not only one that vendors `privacy-gate/`. Pass `install-hook --force` to replace a foreign pre-commit hook or reassign an existing `core.hooksPath`. Pass `install-hook --portable` for a shared repo so the committed hook carries no machine-specific path; pair it with a vendored `privacy-gate/` or the `PRIVACY_GATE_SCRIPT` override.

Bundled files: `scripts/privacy_gate.py` (scanner, source of truth), `scripts/test_privacy_gate.py` (regression tests), `references/policy.md` (policy).

## Flags And Exit Codes

The three scan sources are mutually exclusive and intentionally different:

- `--staged` scans only changed staged blobs. Use it for a focused pre-commit review.
- `--index` scans every entry in the proposed Git index from indexed blob contents, including unchanged tracked files and staged additions/modifications. It excludes untracked and ignored workspace files plus staged deletions. Use it before a Git commit, push, or Git-based release.
- `--path` scans the actual filesystem file or directory. Use it when copying, packaging, synchronizing, or publishing those physical bytes.

Both Git modes use the indexed `.privacygateignore`, if present. Indexed symlinks block. Gitlinks are commit pointers rather than blobs, so they are counted as skipped and not recursively scanned; scan the submodule separately at the boundary being shipped.

- Default `scan` exits nonzero only on `BLOCK` findings (high-confidence secrets); `WARN` findings (PII) print but do not fail. This is the pre-commit default.
- `--fail-on-warn` (or its alias `--strict`) also fails on `WARN` findings; use it for stricter CI gates and release checks.
- `--json` emits a structured report instead of text.

Allowlist reviewed false positives without disabling the gate. Two inline markers, both leaving a visible in-diff audit trail: `privacy-gate: allow` in a comment suppresses PII warnings on that line, but a high-confidence secret still blocks; `privacy-gate: allow-secret` is required to suppress a secret on that line and relies entirely on diff review. Or list reviewed path globs in a committed `.privacygateignore`. Path ignores can cover reviewed binary exports, but they never suppress environment files, private-key/service-credential filenames, or `work/`/`outputs/` artifact paths. See `references/policy.md`.

## Workflow

1. Run `scan --staged` while reviewing a focused staged change.
2. Run `scan --index --strict` before a commit, push, or Git-based release.
3. Run `scan --path PATH --strict` before copying, packaging, synchronizing, or publishing that filesystem path.
4. Treat `BLOCK` findings as release blockers.
5. Treat `WARN` findings as review items that may need redaction or manual confirmation.
6. Use `sanitize --path FILE --write` only for text-file PII cleanup; review the printed preview first.
7. Remove and rotate credentials instead of sanitizing them.

## Policy Reference

Read `references/policy.md` before changing detection rules, hook behavior, or sanitizer behavior. The policy distinguishes high-confidence secrets that block release from privacy-like content that should be reviewed.

## Maintainer Checks

After changing this skill (not needed for normal use), run the regression tests plus the repo gatekeeper:

```bash
python3 "<package-root>/scripts/test_privacy_gate.py"
python3 -S skill-forge/scripts/inspect_skill_package.py "<package-root>" --json --strict
claude plugin validate --strict "<package-root>"  # when Claude tooling is available
```
