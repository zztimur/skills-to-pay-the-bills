# FBAR Proof Kit plugin

This native Codex and Claude Code plugin bundles exactly three canonical skills:

- `statement-intake-preflight`
- `get-year-end-fx-rate`
- `fbar-threshold-check`

Do not edit `skills/` or the copied preview assets directly. The top-level skill
folders and `docs/assets/` are canonical. Regenerate the bundle with:

```bash
python3 -B scripts/sync-plugin-bundles.py
```

CI verifies it without changing files:

```bash
python3 -B scripts/sync-plugin-bundles.py --check
```

The plugin does not add hosted services, telemetry, OCR, filing, or new tax
calculation behavior. See the repository's
[privacy boundary](https://github.com/zztimur/skills-to-pay-the-bills/blob/main/PRIVACY.md)
and [disclaimer](https://github.com/zztimur/skills-to-pay-the-bills/blob/main/DISCLAIMER.md).

The bundled FBAR workflow classifies formal, reconstructed, and attested
evidence separately; reports interval answers when a maximum date is unknown;
and emits a portable postflight manifest that verifies the exact ledger, FX,
JSON, CSV, and PDF set after copying.
