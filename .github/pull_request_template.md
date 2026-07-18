## Summary

<!-- What changed, and what problem does it solve? -->

## Scope

<!-- Name the skill or shared concern. Note any intentionally unchanged areas. -->

## Validation

<!-- List exact checks and results. Say when a check was not run and why. -->

- [ ] Package self-tests or focused regression tests pass.
- [ ] Strict `skill-forge` inspection passes for each changed skill package.
- [ ] `privacy-gate` staged scan passes.
- [ ] `git diff --cached --check` passes.

## Privacy and proof boundary

- [ ] This change contains no raw statements, real financial or account data, personal information, credentials, private links, local usernames, or private absolute paths.
- [ ] Fixtures, screenshots, logs, and sample artifacts are wholly fictional and were created from scratch.
- [ ] New failure modes stop or request explicit review instead of guessing.
- [ ] User-visible claims and retained proof artifacts match the implemented behavior.

## Documentation and release impact

- [ ] Relevant package documentation is updated, or no documentation change is needed.
- [ ] Version and release impact is stated; no release is implied by this pull request alone.

<!-- Security vulnerabilities must use private vulnerability reporting, not a pull request. -->
