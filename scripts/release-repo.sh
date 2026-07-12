#!/usr/bin/env sh
# Publish one global repository release after deterministic local validation.
set -eu

usage() {
    cat <<'EOF'
Usage: scripts/release-repo.sh [--dry-run] [patch|minor|major]

Prepare and publish one repository-wide release. This is the only command that
updates root VERSION, writes a CHANGELOG.md entry, creates a vX.Y.Z tag, and
pushes a release commit and tag to origin/main. Individual skill manifests are
package metadata, not independent releases.

The default bump is patch. Use --dry-run to print the generated changelog entry
and run all checks without modifying files, committing, tagging, or pushing.
EOF
}

dry_run=0
bump="patch"

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            dry_run=1
            shift
            ;;
        patch | minor | major)
            bump="$1"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "release-repo: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

branch="$(git symbolic-ref --quiet --short HEAD || true)"
if [ "$branch" != "main" ]; then
    echo "release-repo: releases must start from main, currently: ${branch:-detached HEAD}" >&2
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "release-repo: worktree must be clean before releasing" >&2
    exit 1
fi

if ! git remote get-url origin >/dev/null 2>&1; then
    echo "release-repo: origin remote is required" >&2
    exit 1
fi

if [ "$dry_run" -eq 0 ]; then
    git fetch origin --prune --tags
else
    echo "Dry run: using the currently fetched origin/main state."
fi

if ! git rev-parse --verify --quiet origin/main >/dev/null; then
    echo "release-repo: origin/main is required" >&2
    exit 1
fi

set -- $(git rev-list --left-right --count HEAD...origin/main)
ahead="$1"
behind="$2"
if [ "$behind" -ne 0 ]; then
    echo "release-repo: main is $behind commit(s) behind origin/main; update it first" >&2
    exit 1
fi
echo "Release base is main (${ahead} local commit(s) ahead of origin/main, ${behind} behind)."

current_version="$(tr -d '\r\n' < VERSION)"
bash scripts/release-validate.sh --version "$current_version"

if ! printf '%s\n' "$current_version" | awk -F. '
    NF != 3 { exit 1 }
    $1 !~ /^(0|[1-9][0-9]*)$/ { exit 1 }
    $2 !~ /^(0|[1-9][0-9]*)$/ { exit 1 }
    $3 !~ /^(0|[1-9][0-9]*)$/ { exit 1 }
    { exit 0 }
'; then
    echo "release-repo: VERSION must be a numeric X.Y.Z value" >&2
    exit 1
fi

old_ifs="$IFS"
IFS=.
set -- $current_version
IFS="$old_ifs"
major="$1"
minor="$2"
patch="$3"

case "$bump" in
    patch)
        next_version="$major.$minor.$((patch + 1))"
        ;;
    minor)
        next_version="$major.$((minor + 1)).0"
        ;;
    major)
        next_version="$((major + 1)).0.0"
        ;;
esac
tag="v$next_version"

if git rev-parse --verify --quiet "refs/tags/$tag" >/dev/null; then
    echo "release-repo: tag already exists: $tag" >&2
    exit 1
fi

empty_tree="$(git hash-object -t tree /dev/null)"
baseline="$(git tag -l 'v[0-9]*' --sort=-v:refname | head -n 1)"
if [ -n "$baseline" ]; then
    baseline_label="$baseline (latest global release tag)"
else
    baseline="$(git log -1 --format=%H -- VERSION)"
    if [ -n "$baseline" ]; then
        baseline_label="$baseline (VERSION baseline before the first global tag)"
    else
        baseline="$empty_tree"
        baseline_label="(no global tag or VERSION baseline; full history)"
    fi
fi

if [ "$baseline" = "$empty_tree" ]; then
    commit_list="$(git rev-list --reverse HEAD)"
else
    commit_list="$(git rev-list --reverse "${baseline}..HEAD")"
fi
if [ -z "$commit_list" ]; then
    echo "release-repo: no commits to release since $baseline_label" >&2
    exit 1
fi

notes_file="$(mktemp "${TMPDIR:-/tmp}/release-notes.XXXXXX")"
changelog_file="$(mktemp "${TMPDIR:-/tmp}/release-changelog.XXXXXX")"
cleanup() {
    rm -f "$notes_file" "$changelog_file"
}
trap cleanup EXIT HUP INT TERM

{
    printf '## [%s] - %s\n\n' "$next_version" "$(date -u +%F)"
    printf '### Changed\n\n'
    printf '%s\n' "$commit_list" | while IFS= read -r commit; do
        subject="$(git log -1 --format=%s "$commit")"
        areas="$(git diff-tree --no-commit-id --name-only -r "$commit" \
            | awk -F/ '
                NF == 1 { root = 1; next }
                { seen[$1] = 1 }
                END {
                    if (root) print "repository"
                    for (area in seen) print area
                }
            ' \
            | sort)"
        area_count="$(printf '%s\n' "$areas" | sed '/^$/d' | wc -l | tr -d ' ')"
        if [ "$area_count" -eq 1 ]; then
            area="$areas"
        else
            area="repository"
        fi
        printf '%s\n' "- \`$area\`: $subject"
    done
} >"$notes_file"

echo "== Planned global release: $tag =="
echo "From: $baseline_label"
cat "$notes_file"
echo ""
echo "-- Release diff --"
bash scripts/release-diff.sh --from "$baseline" --to HEAD --out /dev/null

if [ "$dry_run" -eq 1 ]; then
    echo "-- Running release checks (dry run) --"
    bash scripts/release-check.sh
    echo "Dry run complete: no files changed, no commit created, no tag created, and nothing pushed."
    exit 0
fi

printf '%s\n' "$next_version" > VERSION
awk -v notes_file="$notes_file" '
    !inserted && /^## \[/ {
        while ((getline line < notes_file) > 0) print line
        print ""
        inserted = 1
    }
    { print }
    END {
        if (!inserted) exit 1
    }
' CHANGELOG.md >"$changelog_file"
mv "$changelog_file" CHANGELOG.md

bash scripts/release-validate.sh --version "$next_version"
echo "-- Running release checks --"
bash scripts/release-check.sh

git add VERSION CHANGELOG.md
git commit -m "chore(release): $tag"
git tag -a "$tag" -m "Release $tag"
bash scripts/release-validate.sh --tag "$tag"
git push origin main "refs/tags/$tag"
echo "Released $tag. GitHub Actions will create the GitHub Release."
