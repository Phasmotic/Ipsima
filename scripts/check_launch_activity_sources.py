#!/usr/bin/env python3
"""Fail closed when Talaria launch-sensitive resources bypass their audit."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys


FACTORY_PATH = Path("App/Talaria/AuditedLaunchResourceFactory.swift")
SOURCE_ROOT = Path("App")


class SourceAuditBlocked(RuntimeError):
    """Raised when the checker cannot establish complete source evidence."""


@dataclass(frozen=True)
class ConstructionRule:
    name: str
    expression: re.Pattern[str]
    audit_case: str
    required_in_factory: bool = True


RULES = (
    ConstructionRule(
        "URLSession constructor",
        re.compile(r"\bURLSession\s*(?:\.\s*init)?\s*\("),
        "urlSession",
    ),
    ConstructionRule(
        "URLSession.shared",
        re.compile(r"\bURLSession\s*\.\s*shared\b"),
        "urlSession",
    ),
    ConstructionRule(
        "WebSocketHermesTransport constructor",
        re.compile(r"\bWebSocketHermesTransport\s*(?:\.\s*init)?\s*\("),
        "webSocketTransport",
    ),
    ConstructionRule(
        "NWPathMonitor constructor",
        re.compile(r"\bNWPathMonitor\s*(?:\.\s*init)?\s*\("),
        "networkPathMonitor",
    ),
    ConstructionRule(
        "SCNetworkReachabilityCreateWithName",
        re.compile(r"\bSCNetworkReachabilityCreateWithName\s*\("),
        "reachability",
    ),
    ConstructionRule(
        "SCNetworkReachabilityCreateWithAddress",
        re.compile(r"\bSCNetworkReachabilityCreateWithAddress\s*\("),
        "reachability",
        required_in_factory=False,
    ),
    ConstructionRule(
        "Timer constructor",
        re.compile(r"\bTimer\s*(?:\.\s*init)?\s*\("),
        "timer",
    ),
    ConstructionRule(
        "Timer.scheduledTimer",
        re.compile(r"\bTimer\s*\.\s*scheduledTimer\s*\("),
        "scheduledTimer",
    ),
    ConstructionRule(
        "DispatchSource timer constructor",
        re.compile(r"\bDispatchSource\s*\.\s*makeTimerSource\s*\("),
        "dispatchTimer",
    ),
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: {self.message}"


def _blank(output: list[str], source: str, start: int, end: int) -> None:
    for index in range(start, end):
        if source[index] != "\n":
            output[index] = " "


def _string_opener(source: str, index: int) -> tuple[int, bool] | None:
    match = re.match(r"(?P<hashes>#+)?(?P<quote>\"\"\"|\")", source[index:])
    if match is None:
        return None
    return len(match.group("hashes") or ""), match.group("quote") == '"""'


def swift_code_only(source: str) -> str:
    """Blank comments/literals while retaining executable string interpolations."""

    output = list(source)
    # Entries are dictionaries so interpolation depth can be updated in place.
    stack: list[dict[str, int | bool | str | None]] = [
        {"kind": "code", "depth": None}
    ]
    index = 0

    while index < len(source):
        mode = stack[-1]
        kind = mode["kind"]

        if kind == "line-comment":
            if source[index] == "\n":
                stack.pop()
                index += 1
            else:
                _blank(output, source, index, index + 1)
                index += 1
            continue

        if kind == "block-comment":
            if source.startswith("/*", index):
                _blank(output, source, index, index + 2)
                mode["depth"] = int(mode["depth"]) + 1
                index += 2
            elif source.startswith("*/", index):
                _blank(output, source, index, index + 2)
                mode["depth"] = int(mode["depth"]) - 1
                index += 2
                if mode["depth"] == 0:
                    stack.pop()
            else:
                _blank(output, source, index, index + 1)
                index += 1
            continue

        if kind == "string":
            hash_count = int(mode["hashes"])
            triple = bool(mode["triple"])
            hashes = "#" * hash_count
            terminator = ('"""' if triple else '"') + hashes
            interpolation = "\\" + hashes + "("
            escape = "\\" + hashes

            if source.startswith(interpolation, index):
                _blank(output, source, index, index + len(interpolation))
                index += len(interpolation)
                stack.append({"kind": "code", "depth": 1})
            elif source.startswith(terminator, index):
                _blank(output, source, index, index + len(terminator))
                index += len(terminator)
                stack.pop()
            elif source.startswith(escape, index) and index + len(escape) < len(source):
                end = index + len(escape) + 1
                _blank(output, source, index, end)
                index = end
            else:
                _blank(output, source, index, index + 1)
                index += 1
            continue

        # Code, either top level or inside a string interpolation.
        depth = mode["depth"]
        if depth is not None and source[index] == ")":
            _blank(output, source, index, index + 1)
            depth = int(depth) - 1
            mode["depth"] = depth
            index += 1
            if depth == 0:
                stack.pop()
            continue
        if depth is not None and source[index] == "(":
            mode["depth"] = int(depth) + 1
            index += 1
            continue
        if source.startswith("//", index):
            _blank(output, source, index, index + 2)
            index += 2
            stack.append({"kind": "line-comment", "depth": None})
            continue
        if source.startswith("/*", index):
            _blank(output, source, index, index + 2)
            index += 2
            stack.append({"kind": "block-comment", "depth": 1})
            continue

        opener = _string_opener(source, index)
        if opener is not None:
            hash_count, triple = opener
            opener_length = hash_count + (3 if triple else 1)
            _blank(output, source, index, index + opener_length)
            index += opener_length
            stack.append(
                {
                    "kind": "string",
                    "depth": None,
                    "hashes": hash_count,
                    "triple": triple,
                }
            )
            continue
        index += 1

    if stack[-1]["kind"] == "line-comment":
        stack.pop()
    if len(stack) != 1 or stack[0]["kind"] != "code" or stack[0]["depth"] is not None:
        raise SourceAuditBlocked("a Swift source contains an unterminated comment or literal")
    return "".join(output)


def _source_files(repository: Path) -> list[tuple[Path, Path]]:
    source_root = repository / SOURCE_ROOT
    if source_root.is_symlink() or not source_root.is_dir():
        raise SourceAuditBlocked("the Talaria app source directory is missing")

    source_entries = sorted(source_root.rglob("*"))
    if any(path.is_symlink() for path in source_entries):
        raise SourceAuditBlocked("the Talaria app source set contains an unsafe path")

    files: list[tuple[Path, Path]] = []
    for path in source_entries:
        if path.suffix != ".swift":
            continue
        if not path.is_file():
            raise SourceAuditBlocked("the Talaria app source set contains an unsafe path")
        try:
            relative = path.relative_to(repository)
        except ValueError as error:
            raise SourceAuditBlocked("a Talaria app source escaped the repository") from error
        files.append((relative, path))
    if not files:
        raise SourceAuditBlocked("the Talaria app source set is empty")
    if FACTORY_PATH not in {relative for relative, _ in files}:
        raise SourceAuditBlocked("the audited launch resource factory is missing")
    return files


def _read_source(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SourceAuditBlocked("a Talaria app source could not be read") from error
    if b"\0" in raw:
        raise SourceAuditBlocked("a Talaria app source contains a NUL byte")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceAuditBlocked("a Talaria app source is not UTF-8") from error


def _preceding_code_line(code: str, line: int) -> str | None:
    lines = code.splitlines()
    for index in range(line - 2, -1, -1):
        candidate = lines[index].strip()
        if candidate:
            return candidate
    return None


def check_repository(repository: Path) -> list[Finding]:
    repository = repository.resolve()
    factory_seen = {rule.name: 0 for rule in RULES}
    findings: list[Finding] = []

    for relative, path in _source_files(repository):
        source = _read_source(path)
        code = swift_code_only(source)
        for rule in RULES:
            for match in rule.expression.finditer(code):
                line = code.count("\n", 0, match.start()) + 1
                if relative != FACTORY_PATH:
                    findings.append(
                        Finding(
                            relative,
                            line,
                            f"{rule.name} bypasses AuditedLaunchResourceFactory",
                        )
                    )
                    continue

                factory_seen[rule.name] += 1
                expected = (
                    "LaunchActivityAudit.shared.recordTalariaOwnedConstruction"
                    f"(.{rule.audit_case})"
                )
                if _preceding_code_line(code, line) != expected:
                    findings.append(
                        Finding(
                            relative,
                            line,
                            f"{rule.name} is not immediately preceded by {expected}",
                        )
                    )

    for rule in RULES:
        if rule.required_in_factory and factory_seen[rule.name] == 0:
            findings.append(
                Finding(
                    FACTORY_PATH,
                    1,
                    f"required audited wrapper for {rule.name} is missing",
                )
            )
    return findings


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        findings = check_repository(arguments.repository)
    except SourceAuditBlocked as error:
        print(f"LAUNCH ACTIVITY SOURCES BLOCKED: {error}", file=sys.stderr)
        return 2

    if findings:
        print("LAUNCH ACTIVITY SOURCES FAIL:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.render()}", file=sys.stderr)
        return 1

    print("LAUNCH ACTIVITY SOURCES PASS: Talaria-owned launch resources are audited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
