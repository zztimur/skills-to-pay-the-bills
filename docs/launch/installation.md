# Fresh installation verification

Verified 2026-09-09 using skills CLI 1.5.25 against remote source commit `550f77f42296639a1066388b97782f815a75b8ec` (v1.6.1).

From a new empty temporary directory:

```bash
DISABLE_TELEMETRY=1 npx --yes skills add zztimur/skills-to-pay-the-bills \
  --skill privacy-gate get-year-end-fx-rate statement-intake-preflight fbar-threshold-check \
  --agent codex claude-code --copy --yes
```

Result: exit 0, six skills discovered, exactly four selected skills copied to each host's project skill directory. No global installation was requested. The existing user installations were left untouched.

Every installed file matched its source bytes: 42 files per host, 84 total. The installed privacy scanner returned exit 1 for a synthetic credential and exit 0 after removal. The installed FX script replayed the committed Treasury fixture and produced its four expected artifacts for both hosts.

[Machine-readable check results](install-verification.json)

This proves installation and the checked script behavior. It does not prove that a particular model follows every review instruction, validate native plugin installation, or constitute independent community adoption. Python dependencies were supplied by the existing runtime; this was not a clean operating-system dependency setup.

## Directory visibility

All six skill URL requests returned HTTP 200, but HTTP status alone was misleading. The two promoted pages displayed a 404/unavailable message in their actual content:

- [privacy-gate](https://skills.sh/zztimur/skills-to-pay-the-bills/privacy-gate)
- [get-year-end-fx-rate](https://skills.sh/zztimur/skills-to-pay-the-bills/get-year-end-fx-rate)

Their directory listings and audit results therefore remain unavailable, even though remote installation works. Do not infer that the other four are listed from their HTTP status. The [skills.sh FAQ](https://www.skills.sh/docs/faq) says listing is driven by installer telemetry. This QA run deliberately disabled telemetry and is not evidence of a listing event. No installation count or adoption claim is made.
