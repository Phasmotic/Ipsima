#!/usr/bin/env bash
# Verify the exact Apple build toolchain selected for authoritative Tier B jobs.
set -euo pipefail

EXPECTED_XCODE_VERSION="Xcode 26.6"
EXPECTED_XCODE_BUILD="Build version 17F113"
EXPECTED_SWIFT_VERSION="6.3.3"

xcode_output="$(xcodebuild -version 2>&1)"
xcode_version="$(printf '%s\n' "$xcode_output" | sed -n '1p')"
xcode_build="$(printf '%s\n' "$xcode_output" | sed -n '2p')"
if [ "$xcode_version" != "$EXPECTED_XCODE_VERSION" ] \
    || [ "$xcode_build" != "$EXPECTED_XCODE_BUILD" ]; then
    printf 'ERROR: expected %s (%s); found %s (%s)\n' \
        "$EXPECTED_XCODE_VERSION" "$EXPECTED_XCODE_BUILD" \
        "${xcode_version:-unknown}" "${xcode_build:-unknown}" >&2
    exit 2
fi

swift_output="$(xcrun swift --version 2>&1)"
swift_version_line="$(printf '%s\n' "$swift_output" | sed -n '1p')"
if ! printf '%s\n' "$swift_version_line" \
    | grep -Eq "^(Apple )?Swift version ${EXPECTED_SWIFT_VERSION}([ (]|$)"; then
    printf 'ERROR: expected Swift %s; found %s\n' \
        "$EXPECTED_SWIFT_VERSION" "${swift_version_line:-unknown}" >&2
    exit 2
fi

printf '%s\n%s\n%s\n' "$xcode_version" "$xcode_build" "$swift_version_line"
