#!/usr/bin/env sh
# Show repository-wide release changes since the latest global vX.Y.Z tag.
set -eu

usage() {
    cat <<'EOF'
Usage: scripts/release-diff.sh [options]

Review the repository changes that would be included in the next global release.
Without --from, compare HEAD with the latest vX.Y.Z tag. During the migration
from the previous per-skill release model, it instead uses the most recent
commit that changed VERSION when no global tag exists.

Options:
  --from <ref>   Start ref instead of the default global-release baseline.
  --to <ref>     End ref. Defaults to HEAD.
  --out <path>   Write the full diff to <path> instead of stdout. The release
                 summary and commit log are always printed to stdout.
  -h, --help     Show this help.

Examples:
  scripts/release-diff.sh
  scripts/release-diff.sh --from v1.3.3
  scripts/release-diff.sh --out /tmp/release.diff
EOF
}

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
            echo "release-diff: unexpected argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if ! git rev-parse --verify --quiet "${to_ref}^{commit}" >/dev/null; then
    echo "release-diff: --to ref does not resolve to a commit: $to_ref" >&2
    exit 2
fi

empty_tree="$(git hash-object -t tree /dev/null)"
from_label=""
if [ -z "$from_ref" ]; then
    from_ref="$(git tag -l 'v[0-9]*' --sort=-v:refname | head -n 1)"
    if [ -n "$from_ref" ]; then
        from_label="$from_ref (latest global release tag)"
    else
        from_ref="$(git log -1 --format=%H -- VERSION)"
        if [ -n "$from_ref" ]; then
            from_label="$from_ref (VERSION baseline before the first global tag)"
        else
            from_ref="$empty_tree"
            from_label="(no global tag or VERSION baseline; full history)"
        fi
    fi
else
    from_label="$from_ref"
fi

if [ "$from_ref" != "$empty_tree" ] \
    && ! git rev-parse --verify --quiet "${from_ref}^{commit}" >/dev/null; then
    echo "release-diff: --from ref does not resolve to a commit: $from_ref" >&2
    exit 2
fi

echo "== Repository release diff =="
echo "From: $from_label"
echo "To:   $to_ref"
echo ""
echo "-- Changed top-level areas --"
git diff --name-only "$from_ref" "$to_ref" \
    | awk -F/ '
        NF == 1 { root = 1; next }
        { seen[$1] = 1 }
        END {
            if (root) print "repository root"
            for (area in seen) print area
        }
    ' \
    | sort \
    | sed 's/^/- /'
echo ""
echo "-- Commits --"
if [ "$from_ref" = "$empty_tree" ]; then
    git log --oneline "$to_ref"
else
    git log --oneline "${from_ref}..${to_ref}"
fi
echo ""
echo "-- Diff stat --"
git diff --stat "$from_ref" "$to_ref"
echo ""

if [ -n "$out_path" ]; then
    git diff "$from_ref" "$to_ref" >"$out_path"
    echo "Diff written to $out_path"
else
    echo "-- Diff --"
    git diff "$from_ref" "$to_ref"
fi
