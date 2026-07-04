# Privacy Gate Policy

## Source Of Truth

The dependency-free CLI at `scripts/privacy_gate.py` is the source of truth. Client integrations, skills, hooks, and future CI jobs should call the CLI instead of copying detection rules.

## Blocking Findings

Block commits and releases for high-confidence sensitive content:

- `.env`, `.env.local`, `.env.production`, and similar local environment files.
- Private key files or private key blocks.
- Service-account JSON.
- OpenAI, Anthropic, GitHub, Slack, JWT-like, API key, access token, refresh token, client secret, and password assignments that look real.
- Files under generated `work/` or `outputs/` directories.
- PDFs, images, scans, spreadsheets, and office documents, because they often carry statements, account data, or private exports that text sanitizers cannot safely inspect.
- Missing scan paths, symlinked paths, and text files that exceed the bounded full-read limit.

## Warning Findings

Warn for content that may be private but often appears legitimately in docs or examples:

- Email addresses.
- Phone numbers.
- SSN, ITIN, EIN, IBAN, credit-card-like, bank-account-like, or routing-number-like values.
- Street-address-like lines.
- Credential-like filenames without high-confidence secret content.

Warnings require human review. Use `--fail-on-warn` for stricter gates.

## Sanitization

Sanitization is opt-in and text-only. It may replace PII-like values with placeholders such as `<REDACTED_EMAIL>` or `<REDACTED_TAX_ID>`.

Do not auto-sanitize credentials. Remove them, rotate them, and rerun the scan.

Do not sanitize PDFs, images, scans, spreadsheets, or binary statement exports in place. Regenerate a sanitized source artifact instead.

Do not follow symlinks during direct folder scans. Scan the real target path explicitly only after confirming it is inside the intended review boundary.
