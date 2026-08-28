#!/usr/bin/env python3
"""Verify the exact bundle graph embedded in a Talaria xcarchive.

The expected product names and bundle identifiers are read from project.yml.
Every expected identifier must occur exactly once and at its required nesting.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


TARGET_HEADER = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$")
TARGET_PROPERTY = re.compile(r"^    (type|platform|productName):\s*(.*?)\s*$")
BUNDLE_IDENTIFIER = re.compile(
    r"^\s+PRODUCT_BUNDLE_IDENTIFIER:\s*(.*?)\s*$"
)
PRODUCT_NAME = re.compile(r"^\s+PRODUCT_NAME:\s*(.*?)\s*$")


class ConfigurationError(RuntimeError):
    """The project declaration cannot produce a decidable archive check."""


@dataclass
class Target:
    name: str
    product_type: str | None = None
    platform: str | None = None
    product_name: str | None = None
    bundle_identifier: str | None = None


@dataclass(frozen=True)
class Role:
    label: str
    target_name: str
    product_type: str
    platform: str


@dataclass(frozen=True)
class ExpectedBundle:
    label: str
    bundle_identifier: str
    relative_path: PurePosixPath
    package_type: str


@dataclass(frozen=True)
class Bundle:
    relative_path: PurePosixPath
    bundle_identifier: str
    package_type: str | None


def _scalar(value: str) -> str:
    value = value.strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def load_targets(project_path: Path) -> dict[str, Target]:
    try:
        lines = project_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigurationError(f"cannot read project declaration: {error}") from error

    targets: dict[str, Target] = {}
    in_targets = False
    current: Target | None = None

    for line in lines:
        if not in_targets:
            if line.strip() == "targets:" and not line.startswith((" ", "\t")):
                in_targets = True
            continue

        if line and not line.startswith((" ", "\t", "#")):
            break

        header = TARGET_HEADER.match(line)
        if header:
            name = header.group(1)
            current = Target(name=name)
            targets[name] = current
            continue

        if current is None:
            continue

        property_match = TARGET_PROPERTY.match(line)
        if property_match:
            key, raw_value = property_match.groups()
            value = _scalar(raw_value)
            if key == "type":
                current.product_type = value
            elif key == "platform":
                current.platform = value
            elif key == "productName":
                current.product_name = value
            continue

        bundle_match = BUNDLE_IDENTIFIER.match(line)
        if bundle_match:
            current.bundle_identifier = _scalar(bundle_match.group(1))
            continue

        product_name_match = PRODUCT_NAME.match(line)
        if product_name_match:
            current.product_name = _scalar(product_name_match.group(1))

    if not targets:
        raise ConfigurationError("project declaration contains no targets")
    return targets


def _validated_target(targets: dict[str, Target], role: Role) -> Target:
    target = targets.get(role.target_name)
    if target is None:
        raise ConfigurationError(
            f"{role.label} target {role.target_name!r} is absent from project.yml"
        )
    if target.product_type != role.product_type:
        raise ConfigurationError(
            f"{role.label} target {target.name!r} has type "
            f"{target.product_type!r}, expected {role.product_type!r}"
        )
    if target.platform != role.platform:
        raise ConfigurationError(
            f"{role.label} target {target.name!r} has platform "
            f"{target.platform!r}, expected {role.platform!r}"
        )
    if not target.bundle_identifier:
        raise ConfigurationError(
            f"{role.label} target {target.name!r} has no PRODUCT_BUNDLE_IDENTIFIER"
        )
    product_name = target.product_name or target.name
    if not product_name or "/" in product_name or "\\" in product_name:
        raise ConfigurationError(
            f"{role.label} target {target.name!r} has unsafe product name {product_name!r}"
        )
    if "$" in product_name:
        raise ConfigurationError(
            f"{role.label} target {target.name!r} has unresolved product name {product_name!r}"
        )
    target.product_name = product_name
    return target


def expected_bundles(project_path: Path, roles: list[Role]) -> list[ExpectedBundle]:
    targets = load_targets(project_path)
    selected = [_validated_target(targets, role) for role in roles]
    identifiers = [target.bundle_identifier for target in selected]
    if len(set(identifiers)) != len(identifiers):
        raise ConfigurationError("expected targets do not have unique bundle identifiers")

    ios_app, ios_widget, watch_app, watch_widget = selected
    ios_path = PurePosixPath(
        "Products", "Applications", f"{ios_app.product_name}.app"
    )
    watch_path = ios_path / "Watch" / f"{watch_app.product_name}.app"
    return [
        ExpectedBundle(
            "iOS app", ios_app.bundle_identifier or "", ios_path, "APPL"
        ),
        ExpectedBundle(
            "iOS widget extension",
            ios_widget.bundle_identifier or "",
            ios_path / "PlugIns" / f"{ios_widget.product_name}.appex",
            "XPC!",
        ),
        ExpectedBundle(
            "watch app", watch_app.bundle_identifier or "", watch_path, "APPL"
        ),
        ExpectedBundle(
            "watch widget extension",
            watch_widget.bundle_identifier or "",
            watch_path / "PlugIns" / f"{watch_widget.product_name}.appex",
            "XPC!",
        ),
    ]


def discover_bundles(archive_path: Path) -> tuple[list[Bundle], list[str]]:
    bundles: list[Bundle] = []
    errors: list[str] = []
    for root, directories, _files in os.walk(archive_path, followlinks=False):
        directories.sort()
        root_path = Path(root)
        for directory in directories:
            if not directory.endswith((".app", ".appex")):
                continue
            bundle_path = root_path / directory
            relative = PurePosixPath(bundle_path.relative_to(archive_path).as_posix())
            if bundle_path.is_symlink():
                errors.append(f"bundle is a symlink: {relative}")
                continue
            info_path = bundle_path / "Info.plist"
            try:
                with info_path.open("rb") as stream:
                    info = plistlib.load(stream)
            except (OSError, plistlib.InvalidFileException) as error:
                errors.append(f"cannot read bundle metadata at {relative}: {error}")
                continue
            if not isinstance(info, dict):
                errors.append(f"bundle metadata is not a dictionary: {relative}")
                continue
            identifier = info.get("CFBundleIdentifier")
            if not isinstance(identifier, str) or not identifier:
                errors.append(f"bundle has no CFBundleIdentifier: {relative}")
                continue
            package_type = info.get("CFBundlePackageType")
            bundles.append(
                Bundle(
                    relative_path=relative,
                    bundle_identifier=identifier,
                    package_type=package_type if isinstance(package_type, str) else None,
                )
            )
    return bundles, errors


def verify_archive(
    archive_path: Path, expected: list[ExpectedBundle]
) -> tuple[list[str], list[str]]:
    if not archive_path.is_dir():
        return [], ["archive directory does not exist"]

    bundles, errors = discover_bundles(archive_path)
    successes: list[str] = []
    expected_identities = {
        (item.relative_path, item.bundle_identifier) for item in expected
    }
    for bundle in bundles:
        identity = (bundle.relative_path, bundle.bundle_identifier)
        if identity not in expected_identities:
            errors.append(
                "unexpected app or extension bundle: "
                f"{bundle.relative_path} [{bundle.bundle_identifier}]"
            )
    for item in expected:
        matches = [
            bundle
            for bundle in bundles
            if bundle.bundle_identifier == item.bundle_identifier
        ]
        if not matches:
            errors.append(
                f"{item.label} {item.bundle_identifier!r} is missing; "
                f"expected {item.relative_path}"
            )
            continue
        if len(matches) > 1:
            paths = ", ".join(str(match.relative_path) for match in matches)
            errors.append(
                f"{item.label} {item.bundle_identifier!r} is ambiguous: {paths}"
            )
            continue

        match = matches[0]
        if match.relative_path != item.relative_path:
            errors.append(
                f"{item.label} has wrong nesting: found {match.relative_path}; "
                f"expected {item.relative_path}"
            )
            continue
        if match.package_type != item.package_type:
            errors.append(
                f"{item.label} at {match.relative_path} has CFBundlePackageType "
                f"{match.package_type!r}; expected {item.package_type!r}"
            )
            continue
        successes.append(
            f"{item.label}: {match.relative_path} [{match.bundle_identifier}]"
        )
    return successes, errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--ios-app-target", required=True)
    parser.add_argument("--ios-widget-target", required=True)
    parser.add_argument("--watch-app-target", required=True)
    parser.add_argument("--watch-widget-target", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv or sys.argv[1:])
    roles = [
        Role("iOS app", arguments.ios_app_target, "application", "iOS"),
        Role(
            "iOS widget extension",
            arguments.ios_widget_target,
            "app-extension",
            "iOS",
        ),
        Role("watch app", arguments.watch_app_target, "application", "watchOS"),
        Role(
            "watch widget extension",
            arguments.watch_widget_target,
            "app-extension",
            "watchOS",
        ),
    ]
    try:
        expected = expected_bundles(arguments.project, roles)
    except ConfigurationError as error:
        print(f"G13 BLOCKED: {error}", file=sys.stderr)
        return 2

    successes, errors = verify_archive(arguments.archive, expected)
    for success in successes:
        print(success)
    if errors:
        for error in errors:
            print(f"G13 FAIL: {error}", file=sys.stderr)
        return 1
    print("G13 PASS: exact archive embed graph verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
