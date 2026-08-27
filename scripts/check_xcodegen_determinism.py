#!/usr/bin/env python3
"""Generate every XcodeGen artifact twice and compare the resulting byte trees."""

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
GENERATED_PLIST_DIRECTORY = Path(".gauntlet/generated")
GENERATED_PLISTS = (
    GENERATED_PLIST_DIRECTORY / "TalariaWatchWidgets-Info.plist",
    GENERATED_PLIST_DIRECTORY / "TalariaWidgets-Info.plist",
)
EXPECTED_OUTPUTS = (Path(PROJECT_NAME),) + GENERATED_PLISTS
EXPECTED_OUTPUT_DIRECTORIES = (Path(".gauntlet"), GENERATED_PLIST_DIRECTORY)
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


def tree_manifest(repository: Path, roots: Iterable[Path]) -> Tuple[str, Dict[str, str]]:
    """Hash named output roots, their entry types, symlinks, and file bytes."""
    digest = hashlib.sha256()
    entries: Dict[str, str] = {}
    content_by_path: Dict[str, Tuple[str, bytes]] = {}

    def add_path(path: Path) -> None:
        relative = path.relative_to(repository).as_posix()
        if path.is_symlink():
            marker = "L"
            content = os.readlink(path).encode("utf-8")
        elif path.is_dir():
            marker = "D"
            content = b""
        else:
            marker = "F"
            content = path.read_bytes()
        content_by_path[relative] = (marker, content)

    for relative_root in roots:
        root = repository / relative_root
        add_path(root)
        if root.is_symlink() or not root.is_dir():
            continue

        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            directory_names.sort()
            file_names.sort()
            current = Path(directory)
            for name in list(directory_names):
                path = current / name
                add_path(path)
                if path.is_symlink():
                    directory_names.remove(name)
            for name in file_names:
                add_path(current / name)

    for relative in sorted(content_by_path):
        marker, content = content_by_path[relative]
        if marker == "D":
            entries[relative] = marker
        else:
            entries[relative] = "{} {}".format(
                marker, hashlib.sha256(content).hexdigest()
            )
        digest.update(marker.encode("ascii"))
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")

    return digest.hexdigest(), entries


def validate_generated_outputs(checkout: Path, ordinal: int) -> bool:
    """Require the complete, exact XcodeGen output set using relative diagnostics."""
    generated_projects = sorted(
        path.relative_to(checkout).as_posix()
        for path in checkout.rglob("*.xcodeproj")
    )
    project = checkout / PROJECT_NAME
    if (
        generated_projects != [PROJECT_NAME]
        or not project.is_dir()
        or project.is_symlink()
    ):
        print(
            "ERROR: generation {} created an unexpected project set: {}".format(
                ordinal, generated_projects
            ),
            file=sys.stderr,
        )
        return False

    generated_directory = checkout / GENERATED_PLIST_DIRECTORY
    if not generated_directory.is_dir() or generated_directory.is_symlink():
        print(
            "ERROR: generation {} did not create output directory {}".format(
                ordinal, GENERATED_PLIST_DIRECTORY.as_posix()
            ),
            file=sys.stderr,
        )
        return False

    expected = {path.as_posix() for path in GENERATED_PLISTS}
    observed = {
        path.relative_to(checkout).as_posix()
        for path in generated_directory.rglob("*")
    }
    invalid_types = sorted(
        path.as_posix()
        for path in GENERATED_PLISTS
        if (checkout / path).is_symlink() or not (checkout / path).is_file()
    )
    if observed != expected or invalid_types:
        print(
            "ERROR: generation {} created an unexpected generated plist set".format(
                ordinal
            ),
            file=sys.stderr,
        )
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        if missing:
            print("  missing: {}".format(", ".join(missing)), file=sys.stderr)
        if unexpected:
            print(
                "  unexpected: {}".format(", ".join(unexpected)),
                file=sys.stderr,
            )
        if invalid_types:
            print(
                "  not regular files: {}".format(", ".join(invalid_types)),
                file=sys.stderr,
            )
        return False

    return True


def validate_no_unexpected_changes(
    before: Dict[str, str], after: Dict[str, str], ordinal: int
) -> bool:
    """Reject every checkout change outside the exact generated output paths."""
    exact_paths = {
        path.as_posix() for path in EXPECTED_OUTPUTS + EXPECTED_OUTPUT_DIRECTORIES
    }

    def is_expected_output(relative: str) -> bool:
        return relative in exact_paths or relative.startswith(PROJECT_NAME + "/")

    changed = sorted(
        relative
        for relative in set(before) | set(after)
        if before.get(relative) != after.get(relative)
        and not is_expected_output(relative)
    )
    if not changed:
        return True

    print(
        "ERROR: generation {} changed paths outside the exact output set".format(
            ordinal
        ),
        file=sys.stderr,
    )
    for relative in changed:
        if relative not in before:
            detail = "added"
        elif relative not in after:
            detail = "removed"
        else:
            detail = "content or entry type changed"
        print("  {}: {}".format(relative, detail), file=sys.stderr)
    return False


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
        description="Verify that XcodeGen emits an identical complete output set twice."
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

            generated_checkouts = []
            for ordinal, checkout in enumerate(checkouts, start=1):
                _, before_generation = tree_manifest(checkout, (Path("."),))
                run_xcodegen(xcodegen, checkout, ordinal)
                if not validate_generated_outputs(checkout, ordinal):
                    return 1
                _, after_generation = tree_manifest(checkout, (Path("."),))
                if not validate_no_unexpected_changes(
                    before_generation, after_generation, ordinal
                ):
                    return 1
                generated_checkouts.append(checkout)

            first_hash, first_manifest = tree_manifest(
                generated_checkouts[0], EXPECTED_OUTPUTS
            )
            second_hash, second_manifest = tree_manifest(
                generated_checkouts[1], EXPECTED_OUTPUTS
            )
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

    print(
        "XcodeGen deterministic: {} plus {} generated plists ({})".format(
            PROJECT_NAME, len(GENERATED_PLISTS), first_hash
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
