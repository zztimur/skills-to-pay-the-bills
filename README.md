# Skills To Pay The Bills

Small, practical agent skills for work where guessing is expensive.

Each top-level folder is a standalone skill package with its own `SKILL.md` entrypoint. The pattern here is boring on purpose: one clear workflow, reusable scripts where determinism matters, platform adapters kept thin, and enough proof left behind that Future Me can tell what happened.

## Skills

- `skill-forge/` - the gatekeeper. It audits, pressure tests, validates, and grades skill packages before release. Source repo: https://github.com/zztimur/skill-forge
- `statements-to-interest/` - turns one institution's machine-readable statement PDFs for one tax year into an IRS-oriented interest support packet.
- `get-yearly-fx-rate/` - finds a published yearly average FX rate and leaves a cited proof workpaper instead of asking everyone to trust a number in chat.
- `fbar-threshold-check/` - builds daily foreign-account ledgers, consumes `get-year-end-fx-rate` (preferred) or FBAR-compatible `get-yearly-fx-rate` proof workpapers for non-USD accounts, and checks FBAR daily/max-value thresholds.
- `get-year-end-fx-rate/` - finds a Treasury/Fiscal Data or verified manual year-end FX rate for FBAR-style conversion proof.

## Clone Setup

This collection links `skill-forge` as a git submodule. After cloning, initialize linked skills with:

```bash
git submodule update --init --recursive
```

## Gatekeeper Workflow

Use `skill-forge` before shipping any skill change in this repo. If the package cannot survive the gatekeeper, it is not ready to push.

1. Inspect the changed skill package:

   ```bash
   python -S skill-forge/scripts/inspect_skill_package.py <skill-folder> --json --strict
   ```

2. If the changed skill has a Claude plugin manifest, also run:

   ```bash
   claude plugin validate --strict <skill-folder>
   ```

3. If `skill-forge` itself changes, run its regression suite:

   ```bash
   python -S skill-forge/scripts/run_self_tests.py
   ```

4. Fix all release-blocking findings before committing.

Do not validate the repository root as a single skill. Validate each top-level skill folder independently.
