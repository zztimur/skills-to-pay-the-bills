#!/usr/bin/env sh
# Validate the metadata that defines a global repository release.
set -eu

usage() {
    cat <<'EOF'
Usage: scripts/release-validate.sh [options]

Validate VERSION and CHANGELOG.md for a global release. With --tag, also verify
that the named vX.Y.Z tag exists and points at HEAD.

Options:
  --version <X.Y.Z>  Require VERSION to equal this version.
  --tag <vX.Y.Z>     Require this tag to match VERSION and point at HEAD.
  -h, --help         Show this help.
EOF
}

expected_version=""
tag=""

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            expected_version="${2:?--version requires X.Y.Z}"
            shift 2
            ;;
        --tag)
            tag="${2:?--tag requires vX.Y.Z}"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "release-validate: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [ ! -f VERSION ] || [ ! -f CHANGELOG.md ]; then
    echo "release-validate: VERSION and CHANGELOG.md are required" >&2
    exit 1
fi

version="$(tr -d '\r\n' < VERSION)"
if ! printf '%s\n' "$version" | awk -F. '
    NF != 3 { exit 1 }
    $1 !~ /^(0|[1-9][0-9]*)$/ { exit 1 }
    $2 !~ /^(0|[1-9][0-9]*)$/ { exit 1 }
    $3 !~ /^(0|[1-9][0-9]*)$/ { exit 1 }
    { exit 0 }
'; then
    echo "release-validate: VERSION must be a numeric X.Y.Z value, got: $version" >&2
    exit 1
fi

if [ -n "$expected_version" ] && [ "$version" != "$expected_version" ]; then
    echo "release-validate: VERSION is $version, expected $expected_version" >&2
    exit 1
fi

if ! awk -v heading="## [$version]" 'index($0, heading) == 1 { found = 1 } END { exit !found }' CHANGELOG.md; then
    echo "release-validate: CHANGELOG.md has no ## [$version] entry" >&2
    exit 1
fi

if [ -n "$tag" ]; then
    expected_tag="v$version"
    if [ "$tag" != "$expected_tag" ]; then
        echo "release-validate: tag $tag does not match VERSION $version" >&2
        exit 1
    fi
    if ! git rev-parse --verify --quiet "refs/tags/$tag" >/dev/null; then
        echo "release-validate: tag does not exist locally: $tag" >&2
        exit 1
    fi
    if [ "$(git rev-list -n 1 "$tag")" != "$(git rev-parse HEAD)" ]; then
        echo "release-validate: $tag does not point at HEAD" >&2
        exit 1
    fi
fi

echo "release validation passed: v$version"
