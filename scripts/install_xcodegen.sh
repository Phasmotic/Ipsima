#!/usr/bin/env bash
# Install the exact XcodeGen release used by Tier B after verifying its publisher hash.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"

XCODEGEN_VERSION="2.46.0"
XCODEGEN_URL="https://github.com/yonaskolb/XcodeGen/releases/download/${XCODEGEN_VERSION}/xcodegen.zip"
XCODEGEN_SHA256="4d9e34b62172d645eed6457cac13fc222569974098ef4ee9c3368bedf0196806"
TOOLS="$ROOT/.gauntlet/tools"
ARCHIVE="$TOOLS/xcodegen-$XCODEGEN_VERSION.zip"
INSTALL_ROOT="$TOOLS/xcodegen-$XCODEGEN_VERSION"
XCODEGEN_BIN="$INSTALL_ROOT/bin/xcodegen"

mkdir -p "$TOOLS"

for prerequisite in curl shasum unzip find; do
    command -v "$prerequisite" >/dev/null 2>&1 \
        || { echo "ERROR: $prerequisite is required to install XcodeGen" >&2; exit 2; }
done

if [ ! -f "$ARCHIVE" ]; then
    partial="$ARCHIVE.part"
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        --output "$partial" "$XCODEGEN_URL"
    printf '%s  %s\n' "$XCODEGEN_SHA256" "$partial" | shasum -a 256 -c -
    mv "$partial" "$ARCHIVE"
fi
printf '%s  %s\n' "$XCODEGEN_SHA256" "$ARCHIVE" | shasum -a 256 -c -

if [ ! -x "$XCODEGEN_BIN" ]; then
    if [ -e "$INSTALL_ROOT" ]; then
        echo "ERROR: incomplete XcodeGen installation already exists" >&2
        exit 2
    fi

    extraction_root="$(mktemp -d "$TOOLS/xcodegen-extract.XXXXXX")"
    case "$extraction_root" in
        "$TOOLS"/xcodegen-extract.*) ;;
        *) echo "ERROR: refusing cleanup outside the XcodeGen tools directory" >&2; exit 2 ;;
    esac
    trap 'rm -rf -- "$extraction_root"' EXIT
    unzip -q "$ARCHIVE" -d "$extraction_root"

    candidates="$(find "$extraction_root" -type f -path '*/bin/xcodegen' -print)"
    candidate_count="$(printf '%s\n' "$candidates" | sed '/^$/d' | wc -l | tr -d '[:space:]')"
    if [ "$candidate_count" != "1" ]; then
        echo "ERROR: expected one bin/xcodegen in the verified archive; found $candidate_count" >&2
        exit 2
    fi
    candidate="$(printf '%s\n' "$candidates" | sed -n '1p')"
    distribution_root="$(dirname "$(dirname "$candidate")")"
    mv "$distribution_root" "$INSTALL_ROOT"
    chmod +x "$XCODEGEN_BIN"
fi

if ! actual_version="$($XCODEGEN_BIN --version 2>&1)"; then
    echo "ERROR: installed XcodeGen could not report its version" >&2
    exit 2
fi
case "$actual_version" in
    "$XCODEGEN_VERSION"|"Version: $XCODEGEN_VERSION") ;;
    *)
        echo "ERROR: expected XcodeGen $XCODEGEN_VERSION, found ${actual_version:-unknown}" >&2
        exit 2
        ;;
esac

if [ -n "${GITHUB_PATH:-}" ]; then
    printf '%s\n' "$(dirname "$XCODEGEN_BIN")" >> "$GITHUB_PATH"
fi
printf 'XcodeGen %s installed from verified release archive\n' "$XCODEGEN_VERSION"
