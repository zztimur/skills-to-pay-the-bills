#!/usr/bin/env sh
# Generate a release diff for one skill directory in this repo: the commit
# log and full diff scoped to <skill-dir> between two refs, plus the
# .claude-plugin/plugin.json version change if present. Meant to be reviewed
# before tagging a scoped release such as privacy-gate-v1.1.0.
set -eu

usage() {
    cat <<'EOF'
Usage: scripts/release-diff.sh <skill-dir> [options]

Options:
  --from <ref>   Start ref. Defaults to the latest tag matching
                 '<skill-dir>-v*' (sorted by version). If no such tag
                 exists, defaults to the full history of <skill-dir>.
  --to <ref>     End ref. Defaults to HEAD.
  --out <path>   Write the diff to <path> instead of stdout. The version
                 summary and commit log are always printed to stdout.
  -h, --help     Show this help.

Examples:
  scripts/release-diff.sh privacy-gate
  scripts/release-diff.sh privacy-gate --from privacy-gate-v1.0.0
  scripts/release-diff.sh fbar-threshold-check --out /tmp/fbar.diff
EOF
}

skill=""
from_ref=""
to_ref="HEAD"
out_path=""

while [ $# -gt 0 ]; do
    case "$1" in
        --from)
            from_ref="${2:?--from requires a ref}"
            shift 2
            ;;
        --to)
            to_ref="${2:?--to requires a ref}"
            shift 2
            ;;
        --out)
            out_path="${2:?--out requires a path}"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*)
            echo "release-diff: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [ -n "$skill" ]; then
                echo "release-diff: unexpected extra argument: $1" >&2
                exit 2
            fi
            skill="$1"
            shift
            ;;
    esac
done

if [ -z "$skill" ]; then
    echo "release-diff: missing <skill-dir>" >&2
    usage >&2
    exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

# Strip a trailing slash and reject path traversal; this must be a plain
# directory name at the repo root, not an arbitrary path.
skill="${skill%/}"
case "$skill" in
    */* | . | ..)
        echo "release-diff: <skill-dir> must be a single directory name at the repo root, got: $skill" >&2
        exit 2
        ;;
esac

if [ ! -d "$skill" ]; then
    echo "release-diff: no such directory: $skill" >&2
    exit 2
fi
if [ ! -f "$skill/SKILL.md" ] && [ ! -f "$skill/.claude-plugin/plugin.json" ]; then
    echo "release-diff: $skill does not look like a skill (no SKILL.md or .claude-plugin/plugin.json)" >&2
    exit 2
fi

if ! git rev-parse --verify --quiet "${to_ref}^{commit}" >/dev/null; then
    echo "release-diff: --to ref does not resolve to a commit: $to_ref" >&2
    exit 2
fi

used_default_from=0
if [ -z "$from_ref" ]; then
    from_ref="$(git tag -l "${skill}-v*" --sort=-v:refname | head -n 1)"
    used_default_from=1
fi

empty_tree="$(git hash-object -t tree /dev/null)"
if [ -z "$from_ref" ]; then
    from_ref="$empty_tree"
    from_label="(no prior ${skill}-v* tag; full history)"
else
    if ! git rev-parse --verify --quiet "${from_ref}^{commit}" >/dev/null 2>&1 \
        && ! git rev-parse --verify --quiet "${from_ref}^{tree}" >/dev/null 2>&1; then
        echo "release-diff: --from ref does not resolve: $from_ref" >&2
        exit 2
    fi
    if [ "$used_default_from" -eq 1 ]; then
        from_label="$from_ref (latest ${skill}-v* tag)"
    else
        from_label="$from_ref"
    fi
fi

plugin_json="$skill/.claude-plugin/plugin.json"
from_version=""
to_version=""
if git cat-file -e "${from_ref}:${plugin_json}" 2>/dev/null; then
    from_version="$(git show "${from_ref}:${plugin_json}" | grep -m1 '"version"' | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')"
fi
if git cat-file -e "${to_ref}:${plugin_json}" 2>/dev/null; then
    to_version="$(git show "${to_ref}:${plugin_json}" | grep -m1 '"version"' | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')"
fi

echo "== Release diff: $skill =="
echo "From: $from_label"
echo "To:   $to_ref"
if [ -n "$from_version" ] || [ -n "$to_version" ]; then
    echo "Version: ${from_version:-?} -> ${to_version:-?}"
fi
echo ""
echo "-- Commits touching $skill/ --"
if [ "$from_ref" = "$empty_tree" ]; then
    git log --oneline "$to_ref" -- "$skill" || true
else
    git log --oneline "${from_ref}..${to_ref}" -- "$skill" || true
fi
echo ""

if [ -n "$out_path" ]; then
    git diff "$from_ref" "$to_ref" -- "$skill" >"$out_path"
    echo "Diff written to $out_path"
else
    echo "-- Diff --"
    git diff "$from_ref" "$to_ref" -- "$skill"
fi
