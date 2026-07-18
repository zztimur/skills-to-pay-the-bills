#!/usr/bin/env sh
# Apply the public repository settings used by FBAR Proof Kit.
set -eu

repo="zztimur/skills-to-pay-the-bills"
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if ! command -v gh >/dev/null 2>&1; then
    echo "configure-github-repository: gh is required" >&2
    exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "configure-github-repository: authenticate gh with repository administration access first" >&2
    exit 2
fi

gh api --method PATCH "repos/$repo" \
    -f 'description=FBAR Proof Kit: local, source-linked support packets from foreign-bank statements.' \
    -f 'homepage=https://zztimur.github.io/skills-to-pay-the-bills/' \
    -F 'has_discussions=true' >/dev/null

gh api --method PUT "repos/$repo/topics" \
    -f 'names[]=agent-skills' \
    -f 'names[]=claude-code' \
    -f 'names[]=codex' \
    -f 'names[]=fbar' \
    -f 'names[]=fbar-proof-kit' \
    -f 'names[]=foreign-bank-accounts' \
    -f 'names[]=local-first' \
    -f 'names[]=pdf-extraction' \
    -f 'names[]=privacy' \
    -f 'names[]=python' \
    -f 'names[]=tax-workpapers' \
    -f 'names[]=treasury-rates' \
    -f 'names[]=us-expats' \
    -f 'names[]=workpapers' >/dev/null

gh api --method PATCH "repos/$repo" \
    --input .github/settings/security-and-analysis.json >/dev/null

gh api --method PUT "repos/$repo/private-vulnerability-reporting" >/dev/null

if gh api "repos/$repo/pages" >/dev/null 2>&1; then
    gh api --method PUT "repos/$repo/pages" \
        -f 'build_type=legacy' \
        -f 'source[branch]=main' \
        -f 'source[path]=/docs' >/dev/null
else
    gh api --method POST "repos/$repo/pages" \
        -f 'build_type=legacy' \
        -f 'source[branch]=main' \
        -f 'source[path]=/docs' >/dev/null
fi

gh api --method PUT "repos/$repo/branches/main/protection" \
    --input .github/settings/branch-protection.json >/dev/null

cat <<'EOF'
GitHub repository settings applied:
- focused description, Pages homepage, and topics
- Discussions
- GitHub Pages from main:/docs
- Advanced Security, secret scanning, and push protection
- private vulnerability reporting
- main branch protection with the Python 3.11-3.13 CI matrix

GitHub has no supported REST endpoint for repository social-preview uploads.
Upload docs/assets/social-preview.png in Settings > General > Social preview.
EOF
