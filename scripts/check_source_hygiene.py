#!/usr/bin/env python3
"""G5 repository-literal checks with fail-closed Git enumeration."""

from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from urllib.parse import urlsplit


REPO = Path(__file__).resolve().parent.parent
URL_LITERAL = re.compile(
    r"(?P<scheme>https?|wss?)://[^\s\"'\\`<>(),]+", re.IGNORECASE
)
NETWORK_PREFIX = re.compile(r"(?P<scheme>https?|wss?)" + "://", re.IGNORECASE)
BINARY_URL_LITERAL = re.compile(
    rb"(?P<scheme>https?|wss?)" + rb"://" + rb"[^\x00-\x20\x7f\"'\\`<>(),]+",
    re.IGNORECASE,
)
BINARY_NETWORK_PREFIX = re.compile(
    rb"(?P<scheme>https?|wss?)" + rb"://", re.IGNORECASE
)
CREDENTIAL_WORD = (
    r"(?:api[_-]?key|secret|passcode|bearer|token|password|"
    r"[A-Za-z_][A-Za-z0-9_]*(?:api_?key|secret|passcode|bearer|token|password))"
)
CREDENTIAL_ASSIGNMENT_START = re.compile(
    rf"(?:\b{CREDENTIAL_WORD}\b\s*(?::[^=\n]+)?=|"
    rf"\b{CREDENTIAL_WORD}\b\s*:|"
    rf"[#]*[\"']{CREDENTIAL_WORD}[\"']\s*:)\s*[#]*(?P<quote>[\"'])",
    re.IGNORECASE,
)
LONG_LITERAL_ASSIGNMENT = re.compile(
    r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]+)?="
    r"\s*[#]*[\"'](?P<value>[A-Za-z0-9_-]{24,})[\"']"
)
INTEGRITY_NAME = re.compile(
    r"(?:sha(?:256|384|512)?|checksum|digest|commit|revision|ref)$", re.IGNORECASE
)
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
INDEX_ENTRY = re.compile(
    rb"(?P<mode>[0-7]{6}) (?P<object_id>[0-9a-f]+) "
    rb"(?P<stage>[0-3])\t(?P<path>.*)",
    re.DOTALL,
)
VALID_INDEX_MODES = frozenset({"100644", "100755", "120000", "160000"})


def _secure_url(value: str) -> str:
    # Keep policy examples from becoming occurrences in this checker itself.
    return "https" + "://" + value


# Public dependency/documentation URLs are intentional only at these exact paths.
# An allowed value copied into app code or another script remains a finding.
PUBLIC_HTTPS_ALLOWANCES = {
    "docs/BRIEF.md": frozenset(
        {
            _secure_url(
                "github.com/Phasmotic/Ipsima/issues/2"
                "#issuecomment-5457754926"
            )
        }
    ),
    "docs/GOVERNANCE.md": frozenset(
        {
            _secure_url(
                "github.com/Phasmotic/Ipsima/issues/2"
                "#issuecomment-5457754926"
            )
        }
    ),
    "README.md": frozenset(
        {
            _secure_url("github.com/NousResearch/hermes-agent"),
            _secure_url("tailscale.com"),
        }
    ),
    "protocol/methods.json": frozenset(
        {_secure_url("github.com/NousResearch/hermes-agent.git")}
    ),
    "protocol/methods-26350357.json": frozenset(
        {_secure_url("github.com/NousResearch/hermes-agent.git")}
    ),
    "scripts/derive_protocol.py": frozenset(
        {_secure_url("github.com/NousResearch/hermes-agent.git")}
    ),
    "scripts/test_derive_protocol.py": frozenset(
        {_secure_url("github.com/NousResearch/hermes-agent.git")}
    ),
    "scripts/gauntlet.sh": frozenset(
        {
            _secure_url(
                "github.com/nicklockwood/SwiftFormat/releases/download/0.62.1/"
                "swiftformat_linux.zip"
            ),
            _secure_url(
                "github.com/realm/SwiftLint/releases/download/0.65.0/"
                "swiftlint_linux_amd64.zip"
            ),
            _secure_url(
                "github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
                "gitleaks_8.30.1_linux_x64.tar.gz"
            ),
            _secure_url(
                "github.com/Phasmotic/Ipsima/actions/runs/$run_id"
            ),
        }
    ),
    "scripts/install_xcodegen.sh": frozenset(
        {
            _secure_url(
                "github.com/yonaskolb/XcodeGen/releases/download/"
                "${XCODEGEN_VERSION}/xcodegen.zip"
            )
        }
    ),
    "scripts/install_swiftlint_macos.sh": frozenset(
        {
            _secure_url(
                "github.com/realm/SwiftLint/releases/download/"
                "${SWIFTLINT_VERSION}/SwiftLintBinary.artifactbundle.zip"
            )
        }
    ),
    "scripts/gauntlet_status.sh": frozenset(
        {
            _secure_url("github.com/$expected_repository"),
            _secure_url("github.com/$expected_repository.git"),
        }
    ),
    "scripts/test_gauntlet_status.py": frozenset(
        {
            _secure_url("github.com/Phasmotic/Ipsima.git"),
            _secure_url("github.com/Phasmotic/ipsima.git"),
            _secure_url("github.com/example/Talaria.git"),
        }
    ),
}


class ScanBlocked(RuntimeError):
    """Raised when repository evidence cannot be enumerated or read exactly."""


def allowed_example_or_loopback(hostname: str | None) -> bool:
    if hostname is None:
        return False
    host = hostname.rstrip(".").lower()
    if host == "localhost" or host == "example.com" or host.endswith(".example.com"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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


def _run_git(repository: Path, arguments: list[str]) -> bytes:
    try:
        process = subprocess.run(
            ["git", "-C", os.fspath(repository), *arguments],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ScanBlocked("Git enumeration could not execute") from error
    if process.returncode != 0 or process.stderr:
        raise ScanBlocked("Git enumeration did not return clean evidence")
    return process.stdout


def _portable_git_path(relative: str, evidence_kind: str) -> PurePosixPath:
    portable = PurePosixPath(relative)
    if (
        not relative
        or portable.is_absolute()
        or any(part in {"", ".", ".."} for part in portable.parts)
        or "\\" in relative
        or any(ord(character) < 32 or ord(character) == 127 for character in relative)
    ):
        raise ScanBlocked(f"Git returned an unsafe {evidence_kind} path")
    return portable


def parse_tracked_shell_modes(raw_index: bytes) -> dict[str, str]:
    if raw_index and not raw_index.endswith(b"\0"):
        raise ScanBlocked("Git index evidence was not NUL terminated")
    entries = raw_index[:-1].split(b"\0") if raw_index else []
    shell_modes: dict[str, str] = {}
    for entry in entries:
        match = INDEX_ENTRY.fullmatch(entry)
        if match is None:
            raise ScanBlocked("Git returned malformed index evidence")

        object_id = match.group("object_id")
        if len(object_id) not in {40, 64}:
            raise ScanBlocked("Git returned an invalid index object ID")
        try:
            relative = match.group("path").decode("utf-8")
        except UnicodeDecodeError as error:
            raise ScanBlocked("Git returned a non-UTF-8 index path") from error
        _portable_git_path(relative, "index")

        mode = match.group("mode").decode("ascii")
        if mode not in VALID_INDEX_MODES:
            raise ScanBlocked("Git returned an invalid index mode")
        if not relative.endswith(".sh"):
            continue
        if match.group("stage") != b"0":
            raise ScanBlocked("a tracked shell script has unmerged index evidence")
        if object_id == b"0" * len(object_id):
            raise ScanBlocked("a tracked shell script has indeterminate index evidence")
        if relative in shell_modes:
            raise ScanBlocked("Git returned duplicate shell-script index evidence")
        if mode not in {"100644", "100755"}:
            raise ScanBlocked("a tracked shell script has a non-regular index mode")
        shell_modes[relative] = mode
    return shell_modes


def tracked_shell_modes(repository: Path) -> dict[str, str]:
    return parse_tracked_shell_modes(
        _run_git(repository, ["ls-files", "--stage", "-z"])
    )


def repository_files(repository: Path) -> list[tuple[str, Path]]:
    root_output = _run_git(repository, ["rev-parse", "--show-toplevel"])
    try:
        root_text = root_output.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ScanBlocked("Git returned a non-UTF-8 repository root") from error
    if not root_text or Path(root_text).resolve() != repository:
        raise ScanBlocked("the requested repository is not its Git worktree root")

    raw_paths = _run_git(
        repository,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    )
    if raw_paths and not raw_paths.endswith(b"\0"):
        raise ScanBlocked("Git path evidence was not NUL terminated")
    entries = raw_paths[:-1].split(b"\0") if raw_paths else []
    try:
        relative_paths = [entry.decode("utf-8") for entry in entries]
    except UnicodeDecodeError as error:
        raise ScanBlocked("Git returned a non-UTF-8 repository path") from error
    if len(relative_paths) != len(set(relative_paths)):
        raise ScanBlocked("Git returned duplicate repository paths")

    files: list[tuple[str, Path]] = []
    for relative in sorted(relative_paths):
        portable = _portable_git_path(relative, "repository")
        path = repository.joinpath(*portable.parts)
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            raise ScanBlocked("a Git-enumerated repository path is unavailable") from error
        if stat.S_ISLNK(mode):
            raise ScanBlocked("a Git-enumerated repository path is a symlink")
        if not stat.S_ISREG(mode):
            raise ScanBlocked("a Git-enumerated repository path is not a regular file")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(repository)
        except (OSError, ValueError) as error:
            raise ScanBlocked("a repository path resolves outside the worktree") from error
        files.append((relative, path))
    return files


def _valid_parsed_url(value: str):
    try:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed numeric/bracket syntax.
        parsed.port
    except ValueError:
        # Source templates may defer a numeric loopback port to interpolation.
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        netloc_without_user = parsed.netloc.rsplit("@", 1)[-1]
        if not re.search(r":\{[A-Za-z_][A-Za-z0-9_]*\}$", netloc_without_user):
            return None
    if parsed.hostname is None or parsed.username is not None or parsed.password is not None:
        return None
    return parsed


def url_finding(relative: str, value: str) -> str | None:
    parsed = _valid_parsed_url(value)
    scheme = value.split(":", 1)[0].lower()
    if scheme == "http":
        return "insecure HTTP literal (value redacted)"
    if parsed is None:
        return "malformed network URL literal (value redacted)"
    if scheme == "ws":
        if allowed_example_or_loopback(parsed.hostname) and not (
            parsed.hostname == "example.com"
            or parsed.hostname.endswith(".example.com")
        ):
            return None
        return "non-loopback insecure WebSocket literal (value redacted)"
    if allowed_example_or_loopback(parsed.hostname):
        return None
    if scheme == "https" and value in PUBLIC_HTTPS_ALLOWANCES.get(relative, ()):
        return None
    return "hardcoded non-example host literal (value redacted)"


def _integrity_identifier(match: re.Match[str]) -> bool:
    value = match.group("value")
    return bool(INTEGRITY_NAME.search(match.group("name"))) and bool(
        re.fullmatch(
            r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}|[0-9a-fA-F]{96}|[0-9a-fA-F]{128}",
            value,
        )
    )


def _known_noncredential_long_literal(match: re.Match[str]) -> bool:
    name = match.group("name")
    value = match.group("value")
    if _integrity_identifier(match) or value.startswith("PLACEHOLDER_"):
        return True
    if name.lower() == "prefix":
        return True
    if name.lower().endswith("udid") and re.fullmatch(
        r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", value
    ):
        return True
    return False


def _has_credential_literal_assignment(line: str) -> bool:
    for match in CREDENTIAL_ASSIGNMENT_START.finditer(line):
        quote = match.group("quote")
        end = line.find(quote, match.end())
        if end < 0:
            continue
        value = line[match.end() : end]
        remainder = line[end + 1 :].lstrip()
        if (
            not value
            or "\"" in value
            or "'" in value
            or value.startswith(("$", "\\(", "{"))
            or remainder.startswith("+")
        ):
            continue
        return True
    return False


def scan_text(relative: str, text: str, findings: list[str]) -> None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        handled_network_offsets: set[int] = set()
        for match in URL_LITERAL.finditer(line):
            value = match.group(0)
            handled_network_offsets.add(match.start())
            problem = url_finding(relative, value)
            if problem is not None:
                findings.append(f"{relative}:{line_number}: {problem}")
        for match in NETWORK_PREFIX.finditer(line):
            if match.start() not in handled_network_offsets:
                if match.group("scheme").lower() == "http":
                    problem = "insecure HTTP literal (value redacted)"
                else:
                    problem = "malformed network URL literal (value redacted)"
                findings.append(
                    f"{relative}:{line_number}: {problem}"
                )
        for host_match in HOST_ASSIGNMENT.finditer(line):
            value = host_match.group(1)
            if value in PUBLIC_HTTPS_ALLOWANCES.get(relative, ()):
                continue
            if not allowed_example_or_loopback(assigned_host(value)):
                findings.append(
                    f"{relative}:{line_number}: hardcoded host assignment (value redacted)"
                )
        for literal in STRING_LITERAL.finditer(line):
            if non_loopback_ip_literal(literal.group(1)):
                findings.append(
                    f"{relative}:{line_number}: non-loopback IP literal (value redacted)"
                )
        if _has_credential_literal_assignment(line):
            findings.append(
                f"{relative}:{line_number}: credential-named literal assignment (value redacted)"
            )
        else:
            for match in LONG_LITERAL_ASSIGNMENT.finditer(line):
                if not _known_noncredential_long_literal(match):
                    findings.append(
                        f"{relative}:{line_number}: token-shaped literal assignment (value redacted)"
                    )


def scan_binary(relative: str, payload: bytes, findings: list[str]) -> None:
    """Apply the network-prefix policy without interpreting binary structure."""
    handled_network_offsets: set[int] = set()
    for match in BINARY_URL_LITERAL.finditer(payload):
        handled_network_offsets.add(match.start())
        value = match.group(0).decode("ascii", errors="replace")
        problem = url_finding(relative, value)
        if problem is not None:
            findings.append(f"{relative}:binary: {problem}")
    for match in BINARY_NETWORK_PREFIX.finditer(payload):
        if match.start() not in handled_network_offsets:
            if match.group("scheme").lower() == b"http":
                problem = "insecure HTTP literal (value redacted)"
            else:
                problem = "malformed network URL literal (value redacted)"
            findings.append(f"{relative}:binary: {problem}")


def scan(repository: Path) -> tuple[list[str], int, int]:
    findings: list[str] = []
    text_files = 0
    binary_files = 0
    files = repository_files(repository)
    for relative, mode in sorted(tracked_shell_modes(repository).items()):
        if mode == "100644":
            findings.append(
                f"{relative}: tracked shell script has Git index mode 100644; "
                "expected 100755"
            )
    for relative, path in files:
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise ScanBlocked("a repository file became unreadable") from error
        if b"\0" in payload:
            binary_files += 1
            scan_binary(relative, payload, findings)
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ScanBlocked("a non-binary repository file is not valid UTF-8") from error
        text_files += 1
        scan_text(relative, text, findings)
    return findings, text_files, binary_files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=REPO)
    args = parser.parse_args()
    try:
        repository = args.repository.resolve(strict=True)
        findings, text_files, binary_files = scan(repository)
    except (OSError, ScanBlocked) as error:
        reason = str(error) if isinstance(error, ScanBlocked) else "repository unavailable"
        print(f"G5 source scan blocked: {reason}", file=sys.stderr)
        return 2
    if text_files + binary_files == 0:
        print("G5 source scan blocked: Git enumerated zero files", file=sys.stderr)
        return 2
    if findings:
        print("G5 source-literal findings:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print(
        "G5 source literals: PASS "
        f"({text_files} UTF-8 text files; {binary_files} NUL-classified binary files)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
