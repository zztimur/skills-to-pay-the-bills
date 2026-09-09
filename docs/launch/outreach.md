# Publication drafts

Not sent. Review facts, destination rules, duplicate submissions, and links before publication. Directory eligibility remains as recorded in distribution.md. Do not describe these script demonstrations as verified agent-host sessions.

## Developer-community post

**Title: An agent skill for checking what is about to leave your repo**

Privacy Gate scans a proposed Git index or an export folder for secret patterns, private-looking data, and files it cannot safely inspect. The skill calls the same Python scanner used by its CLI and optional hook.

The synthetic demo plants a credential-shaped assignment, captures the block, removes it, and reruns. Detection is heuristic; a clean result is not a security certificate.

Source, installation, and limitations:
https://github.com/zztimur/skills-to-pay-the-bills/tree/main/privacy-gate

I'd welcome feedback on setup friction and reproducible false positives using synthetic text only.

## FX workflow post

**Title: An exchange-rate lookup that leaves a workpaper behind**

Get Year-End FX Rate retains the rate, direction, source response, provenance, and JSON/Markdown/PDF workpaper. The reproducible example replays a committed Treasury response instead of depending on a live request.

It is one focused skill within FBAR Proof Kit and can be installed separately. Annual-average conversion is a different workflow.

Source and example instructions:
https://github.com/zztimur/skills-to-pay-the-bills/tree/main/get-year-end-fx-rate

## Show HN

**Title:** Show HN: FBAR Proof Kit – local workpapers with a missing-records stop

**URL:** https://github.com/zztimur/skills-to-pay-the-bills

**Opening comment:**

I maintain a set of Agent Skills for turning machine-readable foreign-bank statements into reviewed support artifacts. The runnable demo uses four fictional quarterly statements and a retained FX source response.

The interesting case is deleting Q2: the workflow reports insufficient records and refuses ledger confirmation. The complete fixture produces a ledger, summary, and portable verification manifest.

The scripts run locally. Agent-provider data handling depends on the host you choose. This is support tooling, not a filing service. The README includes dependencies, synthetic sample outputs, and a reproducible demo.

I'd particularly value feedback on whether another person can follow the evidence without rebuilding the work. Please use synthetic examples only.

## VoltAgent conditional entry

Not ready to submit until real community usage can be documented. Target the matching community Development and Testing category after checking current structure and duplicates.

**PR title:** Add skill: zztimur/privacy-gate

```markdown
- **[zztimur/privacy-gate](https://github.com/zztimur/skills-to-pay-the-bills/tree/main/privacy-gate)** - Scan Git proposals and exports for secrets and private data.
```

The description is ten words. Do not add a usage claim until public evidence exists.

## Reviewer invitation

I maintain an open-source workflow that generates reviewable support artifacts from synthetic financial statements. There is also a missing-quarter example that stops instead of producing a complete answer.

Would you be willing to try the synthetic demo and tell me where the setup or output becomes unclear? No financial records are needed.

https://github.com/zztimur/skills-to-pay-the-bills/tree/main/examples/fbar-proof-demo
