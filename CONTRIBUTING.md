# Contributing

Thanks for helping make proof-heavy agent work less fragile. Contributions are welcome when they keep the project local-first, reviewable, and honest about uncertainty.

## Before you start

- Open an issue before a large behavioral change so the scope can be agreed first.
- Keep a pull request focused on one skill or one shared concern.
- Preserve explicit review gates. A smoother workflow is not an improvement if it hides unresolved evidence.
- Use fictional fixtures and examples only.

Never post, attach, commit, or link raw statements or real financial, tax, identity, contact, or account data. Do not derive a fixture by lightly editing a real document. Create a synthetic reproduction from scratch. See [PRIVACY.md](PRIVACY.md).

## Set up the repository

Clone with the linked package initialized:

```bash
git clone --recurse-submodules https://github.com/zztimur/skills-to-pay-the-bills.git
cd skills-to-pay-the-bills
git config core.hooksPath .githooks
```

If you already cloned the repository, run:

```bash
git submodule update --init --recursive
```

Use a supported Python 3.11–3.13 environment. The full release gate needs PDF
dependencies and the pinned official Agent Skills reference validator:

```bash
python3 -m pip install pdfplumber reportlab pypdf pillow \
  'git+https://github.com/agentskills/agentskills.git@38a2ff82958afee88dadf4831509e6f7e9d8ef4e#subdirectory=skills-ref'
```

## Make the change

1. Create a short-lived branch.
2. Change the canonical source. For shared proof-packet code, edit `workpaper-kit/workpaper.py`, not a vendored `_workpaper.py` copy.
3. Add or update deterministic tests for behavioral changes.
4. Update the relevant skill README when the interface or user-visible behavior changes.
5. Keep unrelated formatting and generated files out of the pull request.

## Validate it

Run the changed package's own self-test or regression entrypoint and inspect it strictly:

```bash
python3 -S skill-forge/scripts/inspect_skill_package.py <skill-folder> --json --strict
```

For a full repository check, use:

```bash
bash scripts/release-check.sh
```

Release owners also run the networked distribution smoke tests outside CI:

1. Run `DISABLE_TELEMETRY=1 npx skills add zztimur/skills-to-pay-the-bills --skill statement-intake-preflight --skill get-year-end-fx-rate --skill fbar-threshold-check` in an empty temporary directory and confirm that exactly those three skills install for both Codex and Claude Code.
2. Add this checkout as a marketplace in clean temporary Codex and Claude configurations, install `fbar-proof-kit@skills-to-pay-the-bills`, and confirm that the installed bundle contains exactly the same three skill folders.
3. Run `python3 scripts/sync-plugin-bundles.py --check`, `codex plugin marketplace list`, `codex plugin list`, and `claude plugin validate --strict .` from a fresh shell, then record the commands and results in the release evidence.

These checks intentionally use temporary configurations and telemetry-disabled
CLI installs. Do not overwrite a contributor's normal installed skills.

Before committing, scan the staged change:

```bash
python3 privacy-gate/scripts/privacy_gate.py scan --staged --strict
git diff --cached --check
```

If your environment lacks an optional dependency, say exactly which check you could not run and why. Do not report an unrun check as passing.

## Open the pull request

Complete the pull request template. Explain the problem, the behavior change, and the evidence you used to validate it. Screenshots and logs must be generated from synthetic data and checked for local paths, usernames, tokens, and other private metadata before posting.

By contributing, you agree that your contribution is licensed under this repository's [MIT License](LICENSE).

Security vulnerabilities belong in [private vulnerability reporting](https://github.com/zztimur/skills-to-pay-the-bills/security/advisories/new), not a public issue. See [SECURITY.md](SECURITY.md).
