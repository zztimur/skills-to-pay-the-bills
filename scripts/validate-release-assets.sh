#!/usr/bin/env sh
# Build, checksum, and strictly inspect the exact release ZIPs in scratch space.
set -eu

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
version="$(tr -d '\r\n' < VERSION)"
asset_dir="$(mktemp -d "${TMPDIR:-/tmp}/fbar-release-assets.XXXXXX")"

cleanup() {
    case "$asset_dir" in
        "${TMPDIR:-/tmp}"/fbar-release-assets.*)
            rm -rf -- "$asset_dir"
            ;;
        *)
            echo "validate-release-assets: refusing unexpected cleanup path: $asset_dir" >&2
            ;;
    esac
}
trap cleanup EXIT HUP INT TERM

python3 scripts/package-release-assets.py --version "$version" --output-dir "$asset_dir"
python3 scripts/package-release-assets.py --version "$version" --output-dir "$asset_dir" --verify

for skill in \
    privacy-gate \
    get-yearly-fx-rate \
    get-year-end-fx-rate \
    statement-intake-preflight \
    fbar-threshold-check \
    statements-to-interest
do
    archive="$asset_dir/skills-to-pay-the-bills-v$version-$skill.zip"
    python3 -S skill-forge/scripts/inspect_skill_package.py "$archive" --json --strict --target openai
done

python3 scripts/package-release-assets.py --version "$version" --check-reproducible
echo "Exact release ZIP inspection and reproducibility checks passed for v$version."
