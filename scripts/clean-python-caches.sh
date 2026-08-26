#!/usr/bin/env sh
# Remove only generated Python bytecode caches that can make strict package
# inspection report incomplete coverage. This intentionally does not use a
# repo-wide find: every removable path is an expected package cache location.
set -eu

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

cache_dirs='
examples/fbar-proof-demo/__pycache__
fbar-threshold-check/scripts/__pycache__
fbar-threshold-check/tests/__pycache__
get-year-end-fx-rate/scripts/__pycache__
get-year-end-fx-rate/tests/__pycache__
get-yearly-fx-rate/scripts/__pycache__
privacy-gate/scripts/__pycache__
scripts/__pycache__
skill-forge/scripts/__pycache__
statement-intake-preflight/scripts/__pycache__
statement-intake-preflight/tests/__pycache__
statements-to-interest/scripts/__pycache__
statements-to-interest/tests/__pycache__
workpaper-kit/__pycache__
'

for cache_dir in $cache_dirs; do
    [ ! -e "$cache_dir" ] && continue

    if [ -L "$cache_dir" ] || [ ! -d "$cache_dir" ]; then
        echo "cache cleanup: refusing unexpected path: $cache_dir" >&2
        exit 1
    fi

    rm -rf "$cache_dir"
    echo "cache cleanup: removed $cache_dir"
done
