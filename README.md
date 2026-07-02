# Skills To Pay The Bills

An assorted collection of agent skills. Each top-level folder is a standalone skill package with its own `SKILL.md` entrypoint.

## Skills

- `skill-forge/` - linked gatekeeper skill for auditing, pressure testing, validating, and grading skill packages before release. Source repo: https://github.com/zztimur/skill-forge
- `statements-to-interest/` - tax-support workflow for extracting interest income from one institution's text PDF statements for one tax year.

## Clone Setup

This collection links `skill-forge` as a git submodule. After cloning, initialize linked skills with:

```bash
git submodule update --init --recursive
```

## Gatekeeper Workflow

Use `skill-forge` before shipping any skill change in this repo.

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
