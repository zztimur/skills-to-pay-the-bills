#!/usr/bin/env sh
# Validate every public root skill with the official Agent Skills reference CLI.
set -eu

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if ! command -v skills-ref >/dev/null 2>&1; then
    cat >&2 <<'EOF'
validate-agent-skills: skills-ref is required.
Install the pinned official reference validator with:
  python3 -m pip install 'git+https://github.com/agentskills/agentskills.git@38a2ff82958afee88dadf4831509e6f7e9d8ef4e#subdirectory=skills-ref'
EOF
    exit 2
fi

for skill in \
    privacy-gate \
    get-yearly-fx-rate \
    get-year-end-fx-rate \
    statement-intake-preflight \
    fbar-threshold-check \
    statements-to-interest
do
    skills-ref validate "$skill"
done

echo "Agent Skills specification validation passed for 6 public skills."
