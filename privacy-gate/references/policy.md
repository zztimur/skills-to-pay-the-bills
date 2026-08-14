# Privacy Gate Policy

## Source Of Truth

The dependency-free CLI at `scripts/privacy_gate.py` is the source of truth. Client integrations, skills, hooks, and future CI jobs should call the CLI instead of copying detection rules.

## Scan Boundaries

Choose the boundary that matches what is leaving the machine:

- `scan --staged` reads only changed staged blobs. It is the focused pre-commit view, not proof of the complete repository proposal.
- `scan --index` enumerates the complete proposed Git index and reads each indexed blob object. It includes unchanged tracked files plus staged additions, modifications, and rename destinations; excludes untracked/ignored workspace files and staged deletions; and uses the indexed `.privacygateignore`. Mutable working-tree versions do not affect the result.
- `scan --path PATH` reads the actual filesystem path. Use it for a directory or artifact being copied, packaged, synchronized, or published outside a Git-tree boundary.

The Git modes block indexed symlinks. Regular indexed blobs retain the same bounded text, binary, large-file, detection, reporting, and exit-code policy as path scans. Unresolved or unreadable index entries fail closed. A gitlink/submodule entry contains a commit pointer rather than file content, so it is counted as skipped and is not recursively scanned; scan the submodule's own index for a Git release or its filesystem path for a physical export.

`--index` requires a Git repository. Outside one, it exits with a clear usage error instead of silently falling back to a filesystem scan.

## Filesystem Coverage

A directory scan walks the whole tree except a fixed set of high-noise, low-risk directories: VCS metadata (`.git`, `.hg`, `.svn`), tool caches (`__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `coverage`, `htmlcov`), virtualenvs (`.venv`, `venv`, `env`), and `node_modules`. Build outputs (`build/`, `dist/`) are **not** skipped - they ship in packages, so they are scanned. Every structurally skipped directory is counted and named in the scan output, and `.privacygateignore` skips are reported separately, so coverage is never silently reduced.

## Blocking Findings

Block commits and releases for high-confidence sensitive content:

- Environment files in dotfile form (`.env`, `.env.local`, `.env.production`) and suffix form (`prod.env`, `staging.env`); `example.env`/`sample.env` and the `.env.example` family are treated as templates.
- Private key files or private key blocks.
- Service-account JSON.
- Provider tokens that match a known shape: OpenAI, Anthropic, GitHub (classic and fine-grained), Slack, AWS access key IDs, Stripe live keys, Google API keys, GitLab tokens, npm tokens, and JWT-like tokens.
- API key, access token, refresh token, client secret, and password assignments that look real, including when the keyword is the trailing part of a longer identifier (`DJANGO_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `DB_PASSWORD`) and when the key is written in quoted JSON/dict form (`"api_key": "..."`, `'password': '...'`).
- Credentials embedded in a connection-string / URL userinfo (`postgres://user:<secret>@host`, `redis://:<secret>@host`), unless the password is a documentation placeholder (`user:password@`) or an interpolation (`${PW}`, `{pw}`).
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

## Allowlisting

Adopt the gate without disabling it, using in-repo, reviewable escape hatches:

- Inline: two markers, both traveling with the code and visible in review. `privacy-gate: allow` in a comment suppresses PII warnings on that line only; a high-confidence secret on the same line still blocks. `privacy-gate: allow-secret` also suppresses a secret on that line - a deliberately louder marker so a real credential is never waved through by the softer PII marker, and it relies entirely on diff review. Neither affects file-level blocks such as binary exports or environment files.
- Path: list reviewed glob patterns in `.privacygateignore` at the scan root. Filesystem scans read that file from the scan root; Git scans read its indexed blob. The scan reports how many content items were skipped, and a symlinked ignore file is not honored. Exact reviewed binary/export paths may be ignored, but environment files, private-key/service-credential filenames, and `work/`/`outputs/` artifact paths remain blocking even when a glob matches them.

Neither mechanism hides anything silently: an inline marker lives on the suppressed line, and skipped paths are counted in the scan output. Suppression is a deliberate, reviewable act, not a way to turn the gate off.
