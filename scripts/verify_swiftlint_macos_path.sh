#!/usr/bin/env bash
# Prove that authoritative G4 resolves the digest-verified SwiftLint install.
set -euo pipefail

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
    printf 'SWIFTLINT PATH BLOCKED: expected executable path is required\n' >&2
    exit 2
fi

expected="$1"
resolved="$(command -v swiftlint || true)"

if [ -z "$resolved" ]; then
    printf 'SWIFTLINT PATH BLOCKED: command resolution returned empty evidence\n' >&2
    exit 2
fi
if [ "$resolved" != "$expected" ]; then
    printf 'SWIFTLINT PATH BLOCKED: command resolved outside the verified install\n' >&2
    exit 2
fi

printf 'SwiftLint command path: verified\n'
