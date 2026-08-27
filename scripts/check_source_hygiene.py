#!/usr/bin/env python3
"""G5 source-literal checks with exact per-occurrence host handling."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


REPO = Path(__file__).resolve().parent.parent
SOURCE_DIRECTORIES = ("Packages", "App", "Tests")
URL = re.compile(r"\b(?:https?|wss?)://[^\s\"'\\]+", re.IGNORECASE)
CREDENTIAL_WORD = (
    r"(?:api[_-]?key|secret|passcode|bearer|token|password|"
    r"[A-Za-z_][A-Za-z0-9_]*(?:api_?key|secret|passcode|bearer|token|password))"
)
CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?:\b{CREDENTIAL_WORD}\b\s*(?::[^=\n]+)?=|"
    rf"\b{CREDENTIAL_WORD}\b\s*:|"
    rf"[#]*[\"']{CREDENTIAL_WORD}[\"']\s*:)\s*[#]*[\"']",
    re.IGNORECASE,
)
LONG_LITERAL_ASSIGNMENT = re.compile(r"=\s*[#]*[\"'][A-Za-z0-9_-]{24,}[\"']")
HOST_ASSIGNMENT = re.compile(
    r"(?:"
    r"\b(?:host(?:name)?|endpoint|baseURL|gatewayURL|serverURL)\b\s*(?::[^=\n]+)?=|"
    r"\.(?:host|hostname)\s*=|"
    r"[#]*[\"'](?:host|hostname|endpoint)[\"']\s*:|"
    r"\b(?:host(?:name)?|endpoint|baseURL|gatewayURL|serverURL)\b\s*:"
    r")"
    r"\s*[#]*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"[#]*[\"']([^\"']+)[\"']")


def allowed_example_or_loopback(hostname: str | None) -> bool:
    if hostname is None:
        return False
    host = hostname.rstrip(".").lower()
    return host in {"localhost", "127.0.0.1", "example.com"} or host.endswith(
        ".example.com"
    )


def assigned_host(value: str) -> str | None:
    try:
        return urlsplit(value if "://" in value else "//" + value).hostname
    except ValueError:
        return None


def non_loopback_ip_literal(value: str) -> bool:
    candidate = value.strip("[]")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return not address.is_loopback


def scan(repository: Path) -> tuple[list[str], int]:
    findings: list[str] = []
    files_seen = 0
    for directory_name in SOURCE_DIRECTORIES:
        directory = repository / directory_name
        if not directory.is_dir():
            raise FileNotFoundError(directory_name)
        for path in sorted(directory.rglob("*.swift")):
            files_seen += 1
            relative = path.relative_to(repository).as_posix()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                for match in URL.finditer(line):
                    parsed = urlsplit(match.group(0))
                    if not allowed_example_or_loopback(parsed.hostname):
                        findings.append(
                            f"{relative}:{line_number}: hardcoded non-example host literal"
                        )
                for host_match in HOST_ASSIGNMENT.finditer(line):
                    if not allowed_example_or_loopback(
                        assigned_host(host_match.group(1))
                    ):
                        findings.append(
                            f"{relative}:{line_number}: hardcoded host assignment (value redacted)"
                        )
                for literal in STRING_LITERAL.finditer(line):
                    if non_loopback_ip_literal(literal.group(1)):
                        findings.append(
                            f"{relative}:{line_number}: non-loopback IP literal (value redacted)"
                        )
                if CREDENTIAL_ASSIGNMENT.search(line):
                    findings.append(
                        f"{relative}:{line_number}: credential-named literal assignment (value redacted)"
                    )
                elif LONG_LITERAL_ASSIGNMENT.search(line):
                    findings.append(
                        f"{relative}:{line_number}: token-shaped literal assignment (value redacted)"
                    )
    return findings, files_seen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=REPO)
    args = parser.parse_args()
    repository = args.repository.resolve()
    try:
        findings, files_seen = scan(repository)
    except (FileNotFoundError, OSError, UnicodeError) as error:
        print(f"G5 source scan blocked: {type(error).__name__}", file=sys.stderr)
        return 2
    if files_seen == 0:
        print("G5 source scan blocked: zero Swift files", file=sys.stderr)
        return 2
    if findings:
        print("G5 source-literal findings:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print(f"G5 source literals: PASS ({files_seen} Swift files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
