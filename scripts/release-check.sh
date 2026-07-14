#!/usr/bin/env sh
# Run the deterministic release checks locally and in CI.
set -eu

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if ! python3 -c 'import pdfplumber, pypdf, reportlab' >/dev/null 2>&1; then
  echo "release-check: PDF integration requires pdfplumber, pypdf, and reportlab in the active python3 runtime." >&2
  exit 2
fi

bash scripts/release-validate.sh
python3 -S skill-forge/scripts/inspect_skill_package.py privacy-gate --json --strict
python3 -S skill-forge/scripts/inspect_skill_package.py skill-forge --json --strict
python3 -S skill-forge/scripts/inspect_skill_package.py get-yearly-fx-rate --json --strict
python3 -S skill-forge/scripts/inspect_skill_package.py get-year-end-fx-rate --json --strict
python3 -S skill-forge/scripts/inspect_skill_package.py statement-intake-preflight --json --strict
python3 -S skill-forge/scripts/inspect_skill_package.py fbar-threshold-check --json --strict
python3 -S skill-forge/scripts/inspect_skill_package.py statements-to-interest --json --strict
bash workpaper-kit/sync.sh --check
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
python3 statements-to-interest/scripts/statements_to_interest.py smoke-test
python3 statements-to-interest/tests/run_preflight_integration.py
