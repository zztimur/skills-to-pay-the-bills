# Privacy

Skills To Pay The Bills is a local-first, open-source project. The repository and its static website do not provide a hosted statement-processing service.

## The project boundary

The public website has no input forms, cookies, analytics, external JavaScript, or upload endpoint. The project code reads files by path in the environment where you run it and writes its work products there.

The statement workflow does not intentionally transmit statement content. A source lookup, such as a year-end exchange-rate lookup, may contact a public source without including statement data. Your agent host, model provider, operating system, shell, and source websites remain outside this project boundary and may have their own data practices. Review those practices before working with sensitive material.

The project collects no first-party telemetry. The optional third-party `skills`
CLI sends anonymous skill-install telemetry by default for skills.sh ranking; it
does not send statement content. Set `DISABLE_TELEMETRY=1` before invoking that
CLI to opt out, or install from a versioned GitHub Release ZIP after verifying
`SHA256SUMS`. See the [skills CLI documentation](https://www.skills.sh/docs/cli)
for its current telemetry terms.

## What maintainers receive

The maintainers receive information only when someone chooses to share it through GitHub or another public project channel. Public issues, pull requests, discussions, commit history, and released artifacts may be copied, indexed, cached, or forked.

Do not post, attach, commit, or link:

- raw or altered bank statements;
- real account, tax, government-ID, or transaction data;
- names, addresses, email addresses, or other identifying details from financial records;
- credentials, tokens, private keys, or private repository links;
- private notes or research records that identify an individual.

The maintainers will not ask for raw statements. Reproduce bugs with wholly fictional names, identifiers, values, and dates. Removing a few fields from a real statement is not the same as creating synthetic test data.

## Local storage

Keep sensitive source files and private notes outside the repository or under the root `.private/` directory, which this project gitignores. Treat generated workpapers as sensitive when they derive from real records, even if they look like ordinary JSON, CSV, Markdown, or PDF files.

You control retention in your own environment. Delete local source and output files according to your own security and record-retention requirements.

## Getting help safely

For an ordinary bug or feature request, use the public issue forms with a minimal synthetic reproduction. Do not include private files in logs, screenshots, terminal recordings, paths, or metadata.

For a security or privacy vulnerability in the project code, use [GitHub private vulnerability reporting](https://github.com/zztimur/skills-to-pay-the-bills/security/advisories/new). Describe the behavior with synthetic data and do not attach statements or real financial information.

See [SECURITY.md](SECURITY.md) for the coordinated-disclosure process.
