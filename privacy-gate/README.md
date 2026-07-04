# Privacy Gate

`privacy-gate` is a client-agnostic privacy and secret guard for repository content. It scans staged Git blobs, files, or folder trees before content is committed, packaged, synced, or shared.

The scanner is dependency-free Python. The skill wrapper and Git hook are thin adapters around the same CLI.

## Common Commands

Scan exactly what is staged for commit:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --staged --strict
```

Scan a file or folder:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --path .
python3 privacy-gate/scripts/privacy_gate.py scan --path privacy-gate/SKILL.md
```

Sanitize text-file PII in place:

```bash
python3 privacy-gate/scripts/privacy_gate.py sanitize --path path/to/file.txt --write
```

Install the tracked Git hook locally:

```bash
python3 privacy-gate/scripts/privacy_gate.py install-hook
```

## What Blocks

- Real-looking API keys, access tokens, private keys, service-account JSON, JWT-like tokens, and password assignments.
- `.env` and local environment files except placeholder examples such as `.env.example`.
- Generated `work/` and `outputs/` artifacts.
- Binary/private export formats such as PDFs, images, spreadsheets, and office documents.

## What Warns

- Emails, phone numbers, SSN/ITIN/EIN-like values, IBAN-like values, Luhn-valid card numbers, account/routing-number contexts, and street-address-like lines.
- Suspicious credential-like filenames when content does not prove a high-confidence leak.

Warnings do not automatically fail the hook. Use `--fail-on-warn` when a stricter review gate is needed.

## Important Limits

Secret and PII detection is heuristic and non-exhaustive. A clean scan is useful evidence, not a proof that no private data exists. Credentials should be removed and rotated; redaction alone does not make leaked credentials safe.
