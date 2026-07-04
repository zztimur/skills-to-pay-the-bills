---
description: Scan staged or local files for secrets and private data before commit or release
argument-hint: "scan --staged --strict | scan --path <path> | sanitize --path <path> --write | install-hook"
---

You are running `/privacy-gate:privacy-gate $ARGUMENTS`.

Use the package root `SKILL.md` as the control plane. This command is a thin client entrypoint only; keep detection and sanitization behavior in `scripts/privacy_gate.py`.

## Command Handling

1. Parse `$ARGUMENTS` as arguments for `scripts/privacy_gate.py`.
2. Default to `scan --staged --strict` when no arguments are provided.
3. Run the CLI without reimplementing detection logic.
4. Report `BLOCK` findings as release blockers.
5. Report `WARN` findings as privacy review items.
6. Do not print matched secret values beyond what the CLI reports.
