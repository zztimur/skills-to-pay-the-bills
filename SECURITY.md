# Security Policy

## Supported versions

Security fixes are made on the current `main` branch and included in the next repository release. The latest published release is the supported packaged version. Older releases and unmerged forks may not receive fixes.

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/zztimur/skills-to-pay-the-bills/security/advisories/new) for a vulnerability in this repository or its packaged skills.

Do not open a public issue for a suspected vulnerability. Do not attach, paste, or link raw statements, real financial records, account data, personal information, credentials, or private repository content. The maintainers will not ask you to provide those materials.

Please include:

- the affected skill, script, path, and version or commit;
- the security impact and conditions needed to trigger it;
- a minimal reproduction built entirely from synthetic data;
- the expected and observed behavior;
- any temporary mitigation you have tested.

Remove local usernames, absolute paths, secrets, and unrelated environment details from logs before sharing them.

## What happens next

The maintainer aims to acknowledge a report within five business days and then provide a best-effort triage update. This is a small open-source project and cannot promise a remediation deadline or service-level agreement.

Please allow reasonable time for investigation and release before public disclosure. When a fix is ready, the project may credit the reporter if they want to be named.

## Scope

In scope:

- project code transmitting, exposing, or retaining data contrary to its documented boundary;
- path traversal, unsafe archive handling, command injection, or untrusted-code execution in project scripts;
- secret-scanning or privacy-gate bypasses with a concrete project impact;
- release artifacts that differ materially from the reviewed source.

Usually out of scope:

- tax, legal, accounting, filing, or financial interpretations;
- vulnerabilities in an agent host, model provider, operating system, package registry, GitHub, or source website that do not originate in this project;
- unsupported modified forks or obsolete releases;
- reports that require real private data to demonstrate.

For ordinary bugs that can be shown safely with synthetic data, use the public bug-report form.
