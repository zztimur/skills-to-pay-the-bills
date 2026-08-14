# Privacy Gate

This skill exists for one annoying repo-hygiene question:

```text
Am I about to commit something private?
```

The point is not to prove the repo is magically safe. The point is to catch the obvious leaks before they leave the machine: API keys, `.env` files, generated work folders, statement exports, and private-looking personal data.

The script is the source of truth. The skill wrapper and Git hook are only adapters, so there is not one policy for Codex, another policy for Claude, and a third policy hiding in a shell snippet.

## The Fast Path

Scan only the changed blobs staged for a focused commit review:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --staged --strict
```

Scan the complete proposed Git index before a commit, push, or Git-based release:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --index --strict
```

That reads indexed blob objects, including unchanged tracked files and staged additions or modifications. It excludes staged deletions plus untracked and ignored workspace files. If an ignored, untracked `.env` exists on your laptop, it is outside the Git proposal and does not affect this scan. If an environment file is tracked or force-added to the index, it blocks.

Scan the actual file or folder when those physical bytes are being copied, packaged, synchronized, or published:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --path .
python3 privacy-gate/scripts/privacy_gate.py scan --path privacy-gate/SKILL.md
```

A directory scan skips fixed high-noise folders: VCS metadata, tool caches,
virtual environments, and `node_modules`. It reports those structural skips.
`build/` and `dist/` are not skipped because packaged output is exactly where a
private artifact can hitch a ride. Files it cannot safely inspect as text,
including binary and non-UTF files, block unless the policy recognizes a safe
placeholder format.

Install the tracked Git hook locally:

```bash
python3 privacy-gate/scripts/privacy_gate.py install-hook
```

The hook runs the focused staged scan. The complete repository gate is `scan --index --strict`; the two commands answer different questions.

The default hook embeds the installing script's absolute path, which is convenient solo but discloses a local path and won't resolve on a teammate's machine. For a shared repo use `install-hook --portable` and pair it with a vendored `privacy-gate/` or the `PRIVACY_GATE_SCRIPT` override. Pass `install-hook --force` to take over a foreign pre-commit hook or an existing `core.hooksPath`.

## What Stops The Commit

`privacy-gate` blocks the stuff that should not be waved through:

- Real-looking API keys, access tokens, private keys, service-account JSON, JWT-like tokens, and password assignments — including secrets written as quoted JSON/dict keys (`"api_key": "..."`) and credentials embedded in connection strings (`postgres://user:<secret>@host`).
- `.env` and local environment files, except placeholder examples like `.env.example`.
- Generated `work/` and `outputs/` artifacts.
- PDFs, images, spreadsheets, and office documents that the text scanner cannot safely inspect.
- Missing paths, symlinks, and text files too large for a complete bounded read.

If it blocks a credential, do not just redact it and move on. Remove it, rotate it, and rerun the scan.

## What Gets A Warning

Some things are private only in context. Those get warnings instead of automatic blocks:

- Emails, phone numbers, SSN/ITIN/EIN-like values, IBAN-like values, card-number-like values, account/routing-number contexts, and street-address-like lines.
- Credential-looking filenames when the content does not prove there is a real secret inside.

Warnings do not fail the normal hook. Use `--fail-on-warn` (aliased `--strict`) when you want a stricter review gate:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --path . --fail-on-warn
```

## Allowlisting False Positives

A reviewed false positive does not have to mean disabling the gate. Two inline markers leave a visible audit trail in the diff:

- `privacy-gate: allow` in a comment suppresses PII **warnings** on that line. A high-confidence secret on the same line still blocks.
- `privacy-gate: allow-secret` is required to suppress a **secret** block on that line, and relies entirely on diff review.

Inline markers cannot suppress file-level blocks. `.privacygateignore` can skip reviewed content paths, including exact synthetic binary/export fixtures, and reports the skip count. It cannot suppress environment-file identities, private-key/service-credential filenames, or files under `work/` and `outputs/`. Never add `.env` to `.privacygateignore`: a force-added environment file must remain a block.

For Git scans, the ignore file also comes from the index. An unstaged edit to `.privacygateignore` cannot change a staged or index result. Indexed symlinks block. Gitlinks/submodules are commit pointers rather than blobs, so the parent scan counts them as skipped; scan the submodule separately using its own `--index` or the exported submodule path.

## Sanitizing Text

For plain text files with private-looking personal data:

```bash
python3 privacy-gate/scripts/privacy_gate.py sanitize --path path/to/file.txt
# Review the preview, then apply it deliberately:
python3 privacy-gate/scripts/privacy_gate.py sanitize --path path/to/file.txt --write
```

Sanitizing is deliberately boring. It replaces things like emails and phone numbers with placeholders. It will not try to make a leaked credential safe, and it will not rewrite PDFs, scans, spreadsheets, or statement exports. Regenerate those from a sanitized source instead.

## Limits

This is a guardrail, not a guarantee. Secret and PII detection is heuristic and non-exhaustive. A clean scan is useful evidence; it is not a certificate of purity from the repo heavens.
