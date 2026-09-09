# Two skill pitches

Install commands follow the repository's CLI syntax. A fresh project installation of the two promoted skills plus the FBAR companion skills passed for Codex and Claude Code using skills CLI 1.5.25; see [verification](installation.md). Node/npx is needed for this installer; the agent host needs local-file and shell access. Release ZIPs provide another installation route.

## Privacy Gate

**Headline:** Check what you are about to share.

**Description:** Scan staged changes, the complete proposed Git index, or a filesystem export for secrets, private-looking data, and unsafe artifacts before they leave your machine.

**Install**

```bash
npx skills add zztimur/skills-to-pay-the-bills --skill privacy-gate
```

Set `DISABLE_TELEMETRY=1` before the command to disable the installer's anonymous telemetry.

**First prompt**

> Use privacy-gate to review the complete proposed Git index in this repository. Explain any findings. Do not edit files or install hooks.

**See it work:** [synthetic block-and-clear demonstration](demos/README.md#privacy-check).

Before: a synthetic credential assignment in a text file. After: an explicit blocking finding and nonzero exit. Removing the assignment makes this fixture pass.

Python is required. The scanner is heuristic and may miss secrets or flag innocent content. Binary files it cannot inspect may block. Passing a scan is not proof that every secret has been found. No claim of automatic hook installation is made.

[Source and complete instructions](https://github.com/zztimur/skills-to-pay-the-bills/tree/main/privacy-gate)

## Get Year-End FX Rate

**Headline:** Keep the exchange rate and the evidence together.

**Description:** Retrieve a year-end USD exchange rate and retain a readable workpaper, machine-readable result, source response, and provenance for later review.

**Install**

```bash
npx skills add zztimur/skills-to-pay-the-bills --skill get-year-end-fx-rate
```

Set `DISABLE_TELEMETRY=1` before the command to disable the installer's anonymous telemetry.

**First prompt**

> Use get-year-end-fx-rate to find the 2025 year-end COP to USD rate with retained source proof. Explain the rate direction and return the workpaper links.

**See it work:** [offline source replay](demos/README.md#fx-workpaper).

Before: a source response that would otherwise be separated from a calculation. After: JSON, Markdown, PDF, and retained source evidence together. The demo uses a frozen fixture; the prompt above requests a live lookup.

Use Python 3.11–3.13 and ReportLab for the workpaper. Live lookup requires access to the public source. This skill documents year-end rates; annual-average conversion is a separate skill. It does not decide filing obligations or provide official approval.

[Source and complete instructions](https://github.com/zztimur/skills-to-pay-the-bills/tree/main/get-year-end-fx-rate)
