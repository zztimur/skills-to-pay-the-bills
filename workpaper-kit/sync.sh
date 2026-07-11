#!/usr/bin/env bash
# Vendor the canonical workpaper-kit engine into each skill as a byte-identical
# scripts/_workpaper.py copy. You only ever edit workpaper-kit/workpaper.py; the
# copies are generated and must never be hand-edited.
#
#   sync.sh            copy the canonical source -> each skill's _workpaper.py
#   sync.sh --check    verify every copy is in sync; exit nonzero and list any
#                      stale or missing copy (CI backstop; cannot commit back)
#
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/.." && pwd)"
canonical="$here/workpaper.py"

# Skill packages that vendor the kit. Add a package here whenever it uses a
# public workpaper-kit API so standalone installs keep working.
skills=(
  "get-yearly-fx-rate"
  "get-year-end-fx-rate"
  "statements-to-interest"
)

if [ ! -f "$canonical" ]; then
  echo "sync.sh: canonical source not found: $canonical" >&2
  exit 2
fi

mode="sync"
if [ "$#" -gt 0 ]; then
  case "$1" in
    --check) mode="check" ;;
    -h | --help)
      echo "usage: sync.sh [--check]"
      exit 0
      ;;
    *)
      echo "sync.sh: unknown argument: $1" >&2
      echo "usage: sync.sh [--check]" >&2
      exit 2
      ;;
  esac
fi

stale=()
for skill in "${skills[@]}"; do
  target="$repo_root/$skill/scripts/_workpaper.py"
  if [ "$mode" = "check" ]; then
    if [ ! -f "$target" ] || ! cmp -s "$canonical" "$target"; then
      stale+=("$skill/scripts/_workpaper.py")
    fi
  else
    mkdir -p "$(dirname "$target")"
    cp "$canonical" "$target"
    echo "synced $skill/scripts/_workpaper.py"
  fi
done

if [ "$mode" = "check" ]; then
  if [ "${#stale[@]}" -gt 0 ]; then
    echo "sync.sh --check: vendored copies are stale or missing:" >&2
    for f in "${stale[@]}"; do
      echo "  - $f" >&2
    done
    echo "fix with: workpaper-kit/sync.sh" >&2
    exit 1
  fi
  echo "sync.sh --check: all vendored _workpaper.py copies are in sync"
fi
