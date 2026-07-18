# FBAR Proof Kit plugin

This native Codex and Claude Code plugin bundles exactly three canonical skills:

- `statement-intake-preflight`
- `get-year-end-fx-rate`
- `fbar-threshold-check`

Do not edit `skills/` or the copied preview assets directly. The top-level skill
folders and `docs/assets/` are canonical. Regenerate the bundle with:

```bash
python3 scripts/sync-plugin-bundles.py
```

CI verifies it without changing files:

```bash
python3 scripts/sync-plugin-bundles.py --check
```

The plugin does not add hosted services, telemetry, OCR, filing, or new tax
calculation behavior. See the repository's
[privacy boundary](https://github.com/zztimur/skills-to-pay-the-bills/blob/main/PRIVACY.md)
and [disclaimer](https://github.com/zztimur/skills-to-pay-the-bills/blob/main/DISCLAIMER.md).
