#!/usr/bin/env python3
"""Generate the Xcode project twice and compare the resulting byte trees."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Iterable, List, Tuple


PROJECT_NAME = "Talaria.xcodeproj"
EXCLUDED_DIRECTORIES = {
    ".build",
    ".gauntlet",
    ".git",
    "DerivedData",
    "__pycache__",
}


class GenerationFailed(Exception):
    """XcodeGen ran but rejected the project or could not generate it."""


class PrerequisiteBlocked(Exception):
    """The checker could not establish a deterministic result."""


def copy_ignore(_directory: str, names: List[str]) -> Iterable[str]:
    ignored = []
    for name in names:
        if name in EXCLUDED_DIRECTORIES or name.endswith(".xcodeproj"):
            ignored.append(name)
        elif name.endswith(".pyc"):
            ignored.append(name)
    return ignored


def run_xcodegen(xcodegen: str, checkout: Path, ordinal: int) -> None:
    command = [
        xcodegen,
        "generate",
        "--spec",
        "project.yml",
        "--project",
        ".",
        "--quiet",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=checkout,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        print(
            "ERROR: could not launch XcodeGen generation {}".format(ordinal),
            file=sys.stderr,
        )
        raise PrerequisiteBlocked("XcodeGen launch failed")
    if result.returncode == 0:
        return

    print(
        "ERROR: XcodeGen generation {} failed with exit code {}".format(
            ordinal, result.returncode
        ),
        file=sys.stderr,
    )
    for stream in (result.stdout, result.stderr):
        sanitized = stream
        for temporary_path in {str(checkout), str(checkout.resolve())}:
            sanitized = sanitized.replace(temporary_path, "<temporary-checkout>")
        sanitized = sanitized.strip()
        if sanitized:
            print(sanitized, file=sys.stderr)
    raise GenerationFailed("XcodeGen generation failed")


def tree_manifest(root: Path) -> Tuple[str, Dict[str, str]]:
    """Hash relative paths, entry types, symlink targets, and file bytes."""
    digest = hashlib.sha256()
    entries: Dict[str, str] = {}

    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort()
        file_names.sort()
        current = Path(directory)

        for name in list(directory_names):
            path = current / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                item_digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
                marker = "L"
                entries[relative] = "{} {}".format(marker, item_digest)
                digest.update(marker.encode("ascii"))
                digest.update(b"\0")
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                digest.update(target.encode("utf-8"))
                digest.update(b"\0")
                directory_names.remove(name)
            else:
                entries[relative] = "D"
                digest.update(b"D\0")
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")

        for name in file_names:
            path = current / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                item_digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
                marker = "L"
                content = target.encode("utf-8")
            else:
                content = path.read_bytes()
                item_digest = hashlib.sha256(content).hexdigest()
                marker = "F"

            entries[relative] = "{} {}".format(marker, item_digest)
            digest.update(marker.encode("ascii"))
            digest.update(b"\0")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")

    return digest.hexdigest(), entries


def describe_difference(first: Dict[str, str], second: Dict[str, str]) -> None:
    for relative in sorted(set(first) | set(second)):
        first_value = first.get(relative)
        second_value = second.get(relative)
        if first_value == second_value:
            continue
        if first_value is None:
            detail = "only in generation 2"
        elif second_value is None:
            detail = "only in generation 1"
        else:
            detail = "content or entry type differs"
        print("  {}: {}".format(relative, detail), file=sys.stderr)


def resolve_executable(value: str) -> str:
    candidate = shutil.which(value)
    if candidate is None:
        raise ValueError("XcodeGen executable is unavailable")
    return str(Path(candidate).resolve())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that XcodeGen emits an identical Talaria.xcodeproj twice."
    )
    parser.add_argument(
        "--xcodegen",
        default="xcodegen",
        help="XcodeGen executable path (default: xcodegen from PATH)",
    )
    args = parser.parse_args()

    repository = Path.cwd()
    if not (repository / "project.yml").is_file():
        print("ERROR: run from the repository root containing project.yml", file=sys.stderr)
        return 2

    try:
        xcodegen = resolve_executable(args.xcodegen)
    except ValueError as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 2

    try:
        with tempfile.TemporaryDirectory(prefix="talaria-xcodegen-") as temporary:
            temporary_root = Path(temporary)
            checkouts = [
                temporary_root / "generation-1",
                temporary_root / "generation-2",
            ]
            for checkout in checkouts:
                shutil.copytree(
                    repository,
                    checkout,
                    ignore=copy_ignore,
                    symlinks=True,
                )

            projects = []
            for ordinal, checkout in enumerate(checkouts, start=1):
                run_xcodegen(xcodegen, checkout, ordinal)
                project = checkout / PROJECT_NAME
                generated_projects = sorted(
                    path.relative_to(checkout).as_posix()
                    for path in checkout.rglob("*.xcodeproj")
                    if path.is_dir()
                )
                if generated_projects != [PROJECT_NAME] or not project.is_dir():
                    print(
                        "ERROR: generation {} created an unexpected project set: {}".format(
                            ordinal, generated_projects
                        ),
                        file=sys.stderr,
                    )
                    return 1
                projects.append(project)

            first_hash, first_manifest = tree_manifest(projects[0])
            second_hash, second_manifest = tree_manifest(projects[1])
    except GenerationFailed:
        return 1
    except PrerequisiteBlocked:
        return 2
    except OSError:
        print("ERROR: could not prepare isolated temporary checkouts", file=sys.stderr)
        return 2

    print("generation 1 sha256: {}".format(first_hash))
    print("generation 2 sha256: {}".format(second_hash))
    if first_hash != second_hash:
        print("ERROR: XcodeGen output is not deterministic", file=sys.stderr)
        describe_difference(first_manifest, second_manifest)
        return 1

    print("XcodeGen deterministic: {} ({})".format(PROJECT_NAME, first_hash))
    return 0


if __name__ == "__main__":
    sys.exit(main())
