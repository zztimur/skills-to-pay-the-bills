---
name: privacy-gate
description: Scan staged commits, repo trees, or files for secrets, private data, generated artifacts, and unsafe binary exports. Use before commit, release, packaging, syncing, or sharing.
---

# Privacy Gate

Use this skill when repository content needs a privacy and secret check before commit, push, packaging, installation, or sharing.

## Core Rule

Call the bundled CLI. Do not reimplement detection logic in chat or in a client-specific adapter.

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --staged --strict
python3 privacy-gate/scripts/privacy_gate.py scan --path .
python3 privacy-gate/scripts/privacy_gate.py sanitize --path <file> --write
python3 privacy-gate/scripts/privacy_gate.py install-hook
```

## Workflow

1. Run `scan --staged --strict` before committing staged changes.
2. Run `scan --path .` before publishing, packaging, or syncing a repo copy.
3. Treat `BLOCK` findings as release blockers.
4. Treat `WARN` findings as review items that may need redaction or manual confirmation.
5. Use `sanitize --path <file> --write` only for text-file PII cleanup.
6. Remove and rotate credentials instead of sanitizing them.

## Policy Reference

Read `references/policy.md` before changing detection rules, hook behavior, or sanitizer behavior. The policy distinguishes high-confidence secrets that block release from privacy-like content that should be reviewed.
