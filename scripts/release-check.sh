#!/usr/bin/env sh
# Run the deterministic release checks locally and in CI.
set -eu

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

# Do not create bytecode during validation, and remove stale bytecode before
# strict package inspection. The inspector intentionally treats ignored files
# as a coverage gap, so leftover caches must not make a release nonportable.
export PYTHONDONTWRITEBYTECODE=1
bash scripts/clean-python-caches.sh

if ! python3 -c 'import pdfplumber, pypdf, reportlab; from PIL import Image' >/dev/null 2>&1; then
  echo "release-check: demo and PDF integration require pdfplumber, pypdf, reportlab, and Pillow in the active python3 runtime." >&2
  exit 2
fi

bash scripts/release-validate.sh
bash scripts/validate-agent-skills.sh
for skill in \
  privacy-gate \
  skill-forge \
  get-yearly-fx-rate \
  get-year-end-fx-rate \
  statement-intake-preflight \
  fbar-threshold-check \
  statements-to-interest
do
  python3 -S skill-forge/scripts/inspect_skill_package.py "$skill" --json --strict --target openai
done
bash workpaper-kit/sync.sh --check
python3 scripts/sync-plugin-bundles.py --check
python3 privacy-gate/scripts/privacy_gate.py scan --path . --strict
python3 -S skill-forge/scripts/run_self_tests.py
python3 workpaper-kit/test_workpaper.py
python3 privacy-gate/scripts/test_privacy_gate.py
python3 get-yearly-fx-rate/scripts/get_yearly_fx_rate.py self-test
python3 get-year-end-fx-rate/scripts/get_year_end_fx_rate.py self-test
python3 get-year-end-fx-rate/tests/run_regressions.py
python3 statement-intake-preflight/scripts/statement_intake_preflight.py self-test
python3 fbar-threshold-check/scripts/fbar_threshold_check.py self-test
python3 statements-to-interest/scripts/statements_to_interest.py self-test
python3 statement-intake-preflight/scripts/statement_intake_preflight.py smoke-test
python3 statement-intake-preflight/tests/run_pressure_suite.py
python3 fbar-threshold-check/tests/run_preflight_integration.py
python3 examples/fbar-proof-demo/run_demo.py --check
cmp examples/fbar-proof-demo/checksums.sha256 docs/assets/demo/checksums.sha256
sha256sum --check examples/fbar-proof-demo/checksums.sha256
python3 statements-to-interest/scripts/statements_to_interest.py smoke-test
python3 statements-to-interest/tests/run_preflight_integration.py
bash scripts/validate-release-assets.sh
