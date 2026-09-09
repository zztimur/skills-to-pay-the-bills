# Distribution audit

Checked 2026-09-09 against primary channel documentation. These are eligibility and route checks, not evidence of acceptance or traffic. Recent merge cadence and duplicate submissions still require a pre-submission check.

| Rank | Destination | Verified route and fit | Campaign status / next action |
| --- | --- | --- | --- |
| 1 | [skills.sh](https://www.skills.sh/docs/faq) | Public GitHub skills appear through installer telemetry; no directory PR is required. Fits all six root skills. | Remote install verified for four skills in both host directories. The two promoted listing pages display unavailable content despite HTTP 200; audit status unavailable. See installation.md. Genuine user discovery remains the next step. |
| 2 | [Show HN](https://news.ycombinator.com/showhn.html) | Runnable projects qualify; articles and landing pages alone do not. Submit repository URL with Show HN title. | Draft prepared, not submitted. Browser automation could not initialize, so account access and submission were not possible in this run. Maintainer must be available to discuss the work. No requests for votes. |
| 3 | [VoltAgent/awesome-agent-skills](https://raw.githubusercontent.com/VoltAgent/awesome-agent-skills/main/CONTRIBUTING.md) | Link-only PR in matching community category; author prefix, description at most ten words, real community usage required. | Eligibility unestablished: repository has one star at this check; independent community usage has not been established. Conditional privacy-gate entry prepared. Check duplicate entries and PRs before submitting. |
| 4 | [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills/blob/master/CONTRIBUTING.md) | Instructions request a skill folder, README entry, examples, and tested Claude use. | Deferred: requires platform validation and agreement on distributing this script-backed skill without an incomplete copy or ongoing drift. Not a simple link submission. |
| Exclude | [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills/blob/main/CONTRIBUTING.md) | Requires social proof, specifies a ten-star floor, and disallows AI-assisted PR generation/submission. | Excluded from this AI-assisted submission campaign. No submission draft prepared for this destination. |

## Important distinctions

The repository's native plugin packages are installation mechanisms, not proof of an official marketplace listing. Host support in an ecosystem directory is not validation of this particular workflow. skills.sh listing or security-audit status does not certify tax results.

The native plugin and universal install commands are already in the repository README. Preserve the disclosed installer telemetry opt-out. Do not run repeated installs to influence rankings.

Before spending time on additional directories, complete one outside-use cycle. A verified eligible placement is more useful than a large unverified destination inventory.
