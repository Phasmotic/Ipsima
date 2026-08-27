#!/usr/bin/env bash
# Install the exact SwiftLint release used by both gauntlet tiers.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"

SWIFTLINT_VERSION="0.65.0"
SWIFTLINT_URL="https://github.com/realm/SwiftLint/releases/download/${SWIFTLINT_VERSION}/SwiftLintBinary.artifactbundle.zip"
SWIFTLINT_SHA256="eb333bd76dfb5f46d21fdf3615fe39bb938956ca0b8e94c241c4b2db6e696b90"
SWIFTLINT_MEMBER="SwiftLintBinary.artifactbundle/macos/swiftlint"
TOOLS=".gauntlet/tools"
ARCHIVE="$TOOLS/swiftlint-macos-$SWIFTLINT_VERSION.zip"
SWIFTLINT_BIN="$TOOLS/swiftlint-macos-$SWIFTLINT_VERSION"

blocked() {
    printf 'SWIFTLINT INSTALL BLOCKED: %s\n' "$1" >&2
    exit 2
}

mkdir -p "$TOOLS" || blocked "could not create the tools directory"

for prerequisite in cat chmod curl dirname mktemp mv rm shasum unzip; do
    command -v "$prerequisite" >/dev/null 2>&1 \
        || blocked "$prerequisite is required"
done

if [ ! -f "$ARCHIVE" ]; then
    partial="$ARCHIVE.part"
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        --output "$partial" "$SWIFTLINT_URL" \
        || blocked "release download failed"
    printf '%s  %s\n' "$SWIFTLINT_SHA256" "$partial" \
        | shasum -a 256 -c - >/dev/null \
        || blocked "downloaded release checksum mismatch"
    mv "$partial" "$ARCHIVE" \
        || blocked "verified release could not be promoted"
fi

printf '%s  %s\n' "$SWIFTLINT_SHA256" "$ARCHIVE" \
    | shasum -a 256 -c - >/dev/null \
    || blocked "cached release checksum mismatch"

extraction_root="$(mktemp -d "$TOOLS/swiftlint-extract.XXXXXX")" \
    || blocked "fresh extraction directory could not be created"
case "$extraction_root" in
    "$TOOLS"/swiftlint-extract.*) ;;
    *) blocked "fresh extraction directory escaped the tools directory" ;;
esac
trap 'rm -rf "$extraction_root"' EXIT
candidate="$extraction_root/swiftlint"
version_stdout="$extraction_root/version.stdout"
version_stderr="$extraction_root/version.stderr"

unzip -p "$ARCHIVE" "$SWIFTLINT_MEMBER" >"$candidate" \
    || blocked "verified release could not be extracted"
[ -s "$candidate" ] \
    || blocked "verified release produced an empty executable"
chmod +x "$candidate" \
    || blocked "extracted executable could not be made runnable"

if "$candidate" version >"$version_stdout" 2>"$version_stderr"; then
    version_rc=0
else
    version_rc=$?
fi
[ "$version_rc" -eq 0 ] \
    || blocked "installed executable could not report its version"
[ -s "$version_stdout" ] \
    || blocked "installed executable returned empty version evidence"
[ ! -s "$version_stderr" ] \
    || blocked "installed executable emitted version stderr"
actual_version="$(cat "$version_stdout")" \
    || blocked "installed version evidence could not be read"
[ "$actual_version" = "$SWIFTLINT_VERSION" ] \
    || blocked "expected version $SWIFTLINT_VERSION, found ${actual_version:-unknown}"

[ ! -d "$SWIFTLINT_BIN" ] \
    || blocked "install target is a directory"
mv -f "$candidate" "$SWIFTLINT_BIN" \
    || blocked "verified executable could not be promoted"

if [ -n "${GITHUB_PATH:-}" ]; then
    printf '%s\n' "$ROOT/$(dirname "$SWIFTLINT_BIN")" >>"$GITHUB_PATH" \
        || blocked "could not publish the executable path"
fi

printf 'SwiftLint %s sha256:%s\n' "$SWIFTLINT_VERSION" "$SWIFTLINT_SHA256"
