# Privacy Gate Policy

## Source Of Truth

The dependency-free CLI at `scripts/privacy_gate.py` is the source of truth. Client integrations, skills, hooks, and future CI jobs should call the CLI instead of copying detection rules.

## Blocking Findings

Block commits and releases for high-confidence sensitive content:

- Environment files in dotfile form (`.env`, `.env.local`, `.env.production`) and suffix form (`prod.env`, `staging.env`); `example.env`/`sample.env` and the `.env.example` family are treated as templates.
- Private key files or private key blocks.
- Service-account JSON.
- Provider tokens that match a known shape: OpenAI, Anthropic, GitHub (classic and fine-grained), Slack, AWS access key IDs, Stripe live keys, Google API keys, GitLab tokens, npm tokens, and JWT-like tokens.
- API key, access token, refresh token, client secret, and password assignments that look real, including when the keyword is the trailing part of a longer identifier (`DJANGO_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `DB_PASSWORD`).
- Files under generated `work/` or `outputs/` directories.
- PDFs, images, scans, spreadsheets, and office documents, because they often carry statements, account data, or private exports that text sanitizers cannot safely inspect.
- Binary or non-UTF-8 files (including UTF-16/UTF-32 text): the scanner cannot safely inspect them and blocks them fail-closed rather than passing them unread.
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
