#!/usr/bin/env python3
"""Collect and check deterministic launch-structure evidence.

The wall-clock G12 instrument is stood down.  This checker covers deterministic
launch inputs instead: pre-main dyld work, executable size, loaded-image closure,
and Mach-O static initializer sections.

Collection consumes exact ``xcodebuild -showBuildSettings -json`` output and ten
``DYLD_PRINT_STATISTICS`` logs.  It invokes ``xcrun otool -l`` for the Talaria
launcher executable and ``Talaria.debug.dylib``.  The resulting JSON deliberately
contains no filesystem paths, device IDs, UUIDs, timestamps, or runner identity.

Exit codes are stable: 0 = PASS/success, 1 = FAIL, 2 = BLOCKED evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 1
EXPECTED_TARGET = "Talaria"
EXPECTED_APP_EXECUTABLE = "Talaria"
EXPECTED_DEBUG_DYLIB = "Talaria.debug.dylib"
EXPECTED_APP_WRAPPER = "Talaria.app"
EXPECTED_SAMPLE_COUNT = 10
EXPECTED_PLATFORM = "iphonesimulator"
EXPECTED_CONFIGURATION = "Debug"
EXPECTED_ARCHITECTURE = "arm64"

DERIVATION_BINARY = "exact observed bytes"
DERIVATION_TIMING = (
    "max baseline observed sample + max(1 ms, 25% baseline median)"
)

_DYLIB_COMMAND = re.compile(r"^\s*cmd\s+(LC_[A-Z0-9_]*DYLIB)\s*$")
_DYLIB_NAME = re.compile(r"^\s*name\s+(.+?)\s+\(offset\s+[0-9]+\)\s*$")
_SECTION_NAME = re.compile(r"^\s*sectname\s+(\S+)\s*$")
_SECTION_SIZE = re.compile(r"^\s*size\s+(0x[0-9A-Fa-f]+|[0-9]+)\s*$")
_STATISTIC = re.compile(
    r"^\s*(?P<label>[^:]+):\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s+"
    r"(?P<unit>nanoseconds?|microseconds?|milliseconds?|seconds?)"
    r"(?:\s+\([^\r\n]*\))?\s*$",
    re.IGNORECASE,
)
_SAFE_SUFFIX = re.compile(r"[A-Za-z0-9_+.,@() -]+(?:/[A-Za-z0-9_+.,@() -]+)*\Z")


class EvidenceBlocked(RuntimeError):
    """Evidence is missing, ambiguous, malformed, or not pinned."""


class DuplicateJSONKeyError(ValueError):
    """A JSON object contained a duplicate key."""


@dataclass(frozen=True)
class ResolvedImage:
    label: str
    path: Path


@dataclass(frozen=True)
class Dependency:
    command: str
    namespace: str
    path: str

    def as_json(self) -> dict[str, str]:
        return {
            "command": self.command,
            "namespace": self.namespace,
            "path": self.path,
        }


@dataclass(frozen=True)
class MachOEvidence:
    dependencies: tuple[Dependency, ...]
    mod_init_func_bytes: int


@dataclass(frozen=True)
class PreMainSample:
    total_ms: Decimal
    rebase_binding_ms: Decimal
    initializer_ms: Decimal

    def as_json(self, index: int) -> dict[str, object]:
        return {
            "index": index,
            "initializer_ms": _decimal_text(self.initializer_ms),
            "rebase_binding_ms": _decimal_text(self.rebase_binding_ms),
            "total_ms": _decimal_text(self.total_ms),
        }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_float(token: str) -> object:
    raise EvidenceBlocked(
        f"JSON numeric token {token!r} is not permitted; decimal evidence must be a string"
    )


def _reject_nonfinite(token: str) -> object:
    raise EvidenceBlocked(f"JSON contains non-finite number {token!r}")


def parse_json_bytes(raw: bytes, label: str) -> object:
    if not isinstance(raw, bytes):
        raise EvidenceBlocked(f"{label} input must be bytes")
    if not raw:
        raise EvidenceBlocked(f"{label} is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked(f"{label} is not valid UTF-8") from error
    if not text.strip():
        raise EvidenceBlocked(f"{label} contains only whitespace")
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_reject_json_float,
            parse_constant=_reject_nonfinite,
        )
    except EvidenceBlocked:
        raise
    except DuplicateJSONKeyError as error:
        raise EvidenceBlocked(str(error)) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise EvidenceBlocked(f"{label} is invalid JSON") from error


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceBlocked(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(repr(item) for item in missing))
        if unexpected:
            details.append(
                "unexpected " + ", ".join(repr(item) for item in unexpected)
            )
        raise EvidenceBlocked(f"{label} keys are ambiguous: {'; '.join(details)}")
    return value


def _exact_string(value: object, expected: str, label: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise EvidenceBlocked(f"{label} must equal {expected!r}")
    return value


def _safe_positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvidenceBlocked(f"{label} must be a positive integer")
    return value


def _safe_nonnegative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceBlocked(f"{label} must be a non-negative integer")
    return value


def _exact_integer(value: object, expected: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise EvidenceBlocked(f"{label} must equal integer {expected}")
    return value


def _decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EvidenceBlocked(f"{label} must be a canonical decimal string")
    try:
        number = Decimal(value)
    except (InvalidOperation, DecimalException) as error:
        raise EvidenceBlocked(f"{label} is not a decimal") from error
    if not number.is_finite():
        raise EvidenceBlocked(f"{label} must be finite")
    if positive and number <= 0:
        raise EvidenceBlocked(f"{label} must be positive")
    if not positive and number < 0:
        raise EvidenceBlocked(f"{label} must be non-negative")
    if _decimal_text(number) != value:
        raise EvidenceBlocked(f"{label} is not a canonical decimal string")
    return number


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise EvidenceBlocked("cannot serialize a non-finite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def parse_build_settings(raw: bytes) -> tuple[ResolvedImage, ResolvedImage]:
    """Resolve exactly one Talaria target from showBuildSettings JSON."""

    document = parse_json_bytes(raw, "build-settings JSON")
    if not isinstance(document, list):
        raise EvidenceBlocked("build-settings JSON must be an array")

    matches: list[dict[str, Any]] = []
    for index, entry_value in enumerate(document):
        entry = _exact_keys(
            entry_value,
            {"action", "buildSettings", "target"},
            f"build-settings entry {index}",
        )
        target = entry["target"]
        if not isinstance(target, str) or not target:
            raise EvidenceBlocked(f"build-settings entry {index} target is invalid")
        if target == EXPECTED_TARGET:
            matches.append(entry)
    if len(matches) != 1:
        raise EvidenceBlocked(
            f"build-settings JSON must contain exactly one {EXPECTED_TARGET!r} target; "
            f"found {len(matches)}"
        )

    entry = matches[0]
    _exact_string(entry["action"], "build", "Talaria build action")
    settings = entry["buildSettings"]
    if not isinstance(settings, dict):
        raise EvidenceBlocked("Talaria buildSettings must be an object")

    required = {
        "CONFIGURATION": EXPECTED_CONFIGURATION,
        "EXECUTABLE_NAME": EXPECTED_APP_EXECUTABLE,
        "EXECUTABLE_PATH": f"{EXPECTED_APP_WRAPPER}/{EXPECTED_APP_EXECUTABLE}",
        "FULL_PRODUCT_NAME": EXPECTED_APP_WRAPPER,
        "PLATFORM_NAME": EXPECTED_PLATFORM,
        "PRODUCT_NAME": EXPECTED_TARGET,
        "WRAPPER_NAME": EXPECTED_APP_WRAPPER,
    }
    for key, expected in required.items():
        if key not in settings:
            raise EvidenceBlocked(f"Talaria buildSettings is missing {key!r}")
        _exact_string(settings[key], expected, f"Talaria build setting {key}")

    architectures = settings.get("ARCHS")
    if not isinstance(architectures, str) or architectures.split() != [
        EXPECTED_ARCHITECTURE
    ]:
        raise EvidenceBlocked(
            f"Talaria build setting ARCHS must contain only {EXPECTED_ARCHITECTURE!r}"
        )

    raw_build_dir = settings.get("TARGET_BUILD_DIR")
    if not isinstance(raw_build_dir, str) or not raw_build_dir:
        raise EvidenceBlocked("Talaria build setting TARGET_BUILD_DIR is missing")
    build_dir = Path(raw_build_dir)
    if not build_dir.is_absolute():
        raise EvidenceBlocked("Talaria TARGET_BUILD_DIR must be absolute")
    try:
        resolved_build_dir = build_dir.resolve(strict=True)
    except OSError as error:
        raise EvidenceBlocked("Talaria TARGET_BUILD_DIR cannot be resolved") from error
    if not resolved_build_dir.is_dir():
        raise EvidenceBlocked("Talaria TARGET_BUILD_DIR is not a directory")

    executable_relative = PurePosixPath(required["EXECUTABLE_PATH"])
    app_executable = resolved_build_dir.joinpath(*executable_relative.parts)
    debug_dylib = (
        resolved_build_dir / EXPECTED_APP_WRAPPER / EXPECTED_DEBUG_DYLIB
    )
    images = (
        ResolvedImage(EXPECTED_APP_EXECUTABLE, app_executable),
        ResolvedImage(EXPECTED_DEBUG_DYLIB, debug_dylib),
    )
    for image in images:
        if image.path.is_symlink():
            raise EvidenceBlocked(f"{image.label} must not be a symlink")
        try:
            resolved_image = image.path.resolve(strict=True)
        except OSError as error:
            raise EvidenceBlocked(f"{image.label} cannot be resolved") from error
        try:
            resolved_image.relative_to(resolved_build_dir)
        except ValueError as error:
            raise EvidenceBlocked(f"{image.label} resolves outside TARGET_BUILD_DIR") from error
        if not resolved_image.is_file():
            raise EvidenceBlocked(f"{image.label} is not a regular file")
    return images


def _sanitized_dependency(command: str, raw_name: str) -> Dependency:
    if not raw_name or raw_name != raw_name.strip():
        raise EvidenceBlocked("otool emitted an invalid dylib install name")
    namespaces = (
        ("/usr/lib/", "system_usr_lib"),
        ("/System/Library/", "system_library"),
        ("@rpath/", "rpath"),
        ("@loader_path/", "loader_path"),
        ("@executable_path/", "executable_path"),
    )
    for prefix, namespace in namespaces:
        if raw_name.startswith(prefix):
            suffix = raw_name[len(prefix) :]
            if not suffix or _SAFE_SUFFIX.fullmatch(suffix) is None:
                raise EvidenceBlocked("otool emitted an unsafe dylib install name")
            if ".." in PurePosixPath(suffix).parts:
                raise EvidenceBlocked("otool emitted a traversing dylib install name")
            return Dependency(command, namespace, suffix)
    raise EvidenceBlocked(
        "otool emitted a dylib install name outside approved sanitized namespaces"
    )


def parse_otool_output(raw: bytes) -> MachOEvidence:
    if not isinstance(raw, bytes) or not raw:
        raise EvidenceBlocked("otool output is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("otool output is not valid UTF-8") from error

    dependencies: list[Dependency] = []
    pending_command: str | None = None
    pending_mod_init = False
    mod_init_sizes: list[int] = []

    for line in text.splitlines():
        command_match = _DYLIB_COMMAND.match(line)
        if command_match:
            if pending_command is not None:
                raise EvidenceBlocked("otool dylib command has no install name")
            pending_command = command_match.group(1)
            continue

        if pending_command is not None:
            name_match = _DYLIB_NAME.match(line)
            if name_match:
                dependencies.append(
                    _sanitized_dependency(pending_command, name_match.group(1))
                )
                pending_command = None
                continue
            if line.lstrip().startswith(("cmd ", "Load command ", "Section")):
                raise EvidenceBlocked("otool dylib command has no install name")

        section_match = _SECTION_NAME.match(line)
        if section_match:
            if pending_mod_init:
                raise EvidenceBlocked("otool __mod_init_func section has no size")
            pending_mod_init = section_match.group(1) == "__mod_init_func"
            continue
        if pending_mod_init:
            size_match = _SECTION_SIZE.match(line)
            if size_match:
                try:
                    size = int(size_match.group(1), 0)
                except ValueError as error:
                    raise EvidenceBlocked("otool __mod_init_func size is invalid") from error
                mod_init_sizes.append(size)
                pending_mod_init = False
            elif line.lstrip().startswith(("sectname ", "Section", "Load command ")):
                raise EvidenceBlocked("otool __mod_init_func section has no size")

    if pending_command is not None:
        raise EvidenceBlocked("otool dylib command has no install name")
    if pending_mod_init:
        raise EvidenceBlocked("otool __mod_init_func section has no size")
    if not dependencies:
        raise EvidenceBlocked("otool output contains no LC_*DYLIB load commands")
    return MachOEvidence(tuple(dependencies), sum(mod_init_sizes))


def _milliseconds(value: Decimal, unit: str) -> Decimal:
    normalized = unit.lower()
    if normalized in {"nanosecond", "nanoseconds"}:
        return value / Decimal("1000000")
    if normalized in {"microsecond", "microseconds"}:
        return value / Decimal("1000")
    if normalized in {"millisecond", "milliseconds"}:
        return value
    if normalized in {"second", "seconds"}:
        return value * Decimal("1000")
    raise EvidenceBlocked("DYLD_PRINT_STATISTICS used an unsupported time unit")


def _statistic_kind(label: str) -> str | None:
    normalized = " ".join(label.lower().split())
    if normalized == "total pre-main time":
        return "total"
    if normalized in {"rebase/binding time", "total time in rebase/binding"}:
        return "rebase_binding"
    if normalized in {"initializer time", "total time in initializers"}:
        return "initializer"
    return None


def parse_dyld_statistics(raw: bytes) -> PreMainSample:
    if not isinstance(raw, bytes) or not raw:
        raise EvidenceBlocked("DYLD_PRINT_STATISTICS log is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("DYLD_PRINT_STATISTICS log is not valid UTF-8") from error
    values: dict[str, Decimal] = {}
    for line in text.splitlines():
        match = _STATISTIC.match(line)
        if not match:
            continue
        kind = _statistic_kind(match.group("label"))
        if kind is None:
            continue
        if kind in values:
            raise EvidenceBlocked(f"DYLD_PRINT_STATISTICS has ambiguous {kind} evidence")
        try:
            raw_value = Decimal(match.group("value"))
        except InvalidOperation as error:
            raise EvidenceBlocked("DYLD_PRINT_STATISTICS contains an invalid number") from error
        values[kind] = _milliseconds(raw_value, match.group("unit"))
    missing = [
        item for item in ("total", "rebase_binding", "initializer") if item not in values
    ]
    if missing:
        raise EvidenceBlocked(
            "DYLD_PRINT_STATISTICS is missing " + ", ".join(missing) + " evidence"
        )
    total = values["total"]
    rebase_binding = values["rebase_binding"]
    initializer = values["initializer"]
    if total <= 0:
        raise EvidenceBlocked("DYLD_PRINT_STATISTICS total time must be positive")
    if rebase_binding < 0 or initializer < 0:
        raise EvidenceBlocked("DYLD_PRINT_STATISTICS component time cannot be negative")
    if rebase_binding > total or initializer > total:
        raise EvidenceBlocked("DYLD_PRINT_STATISTICS component exceeds total time")
    return PreMainSample(total, rebase_binding, initializer)


def _run_otool(image: ResolvedImage) -> MachOEvidence:
    try:
        completed = subprocess.run(
            ["xcrun", "otool", "-l", str(image.path)],
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise EvidenceBlocked(f"xcrun otool could not inspect {image.label}") from error
    if completed.returncode != 0:
        raise EvidenceBlocked(f"xcrun otool failed for {image.label}")
    if completed.stderr:
        raise EvidenceBlocked(f"xcrun otool emitted stderr for {image.label}")
    return parse_otool_output(completed.stdout)


def collect_observation(
    build_settings_raw: bytes,
    statistics_logs: Sequence[bytes],
    *,
    otool: Callable[[ResolvedImage], MachOEvidence] = _run_otool,
) -> dict[str, object]:
    if len(statistics_logs) != EXPECTED_SAMPLE_COUNT:
        raise EvidenceBlocked(
            f"exactly {EXPECTED_SAMPLE_COUNT} DYLD_PRINT_STATISTICS logs are required; "
            f"found {len(statistics_logs)}"
        )
    images = parse_build_settings(build_settings_raw)
    samples = [parse_dyld_statistics(raw) for raw in statistics_logs]

    image_documents: list[dict[str, object]] = []
    for image in images:
        evidence = otool(image)
        try:
            size = image.path.stat().st_size
        except OSError as error:
            raise EvidenceBlocked(f"cannot measure {image.label} bytes") from error
        if size <= 0:
            raise EvidenceBlocked(f"{image.label} must contain at least one byte")
        image_documents.append(
            {
                "bytes": size,
                "dependencies": [item.as_json() for item in evidence.dependencies],
                "mod_init_func_bytes": evidence.mod_init_func_bytes,
                "name": image.label,
            }
        )

    component_values = {
        "initializer_ms": [sample.initializer_ms for sample in samples],
        "rebase_binding_ms": [sample.rebase_binding_ms for sample in samples],
        "total_ms": [sample.total_ms for sample in samples],
    }
    medians = {
        key: _decimal_text(statistics.median(values))
        for key, values in component_values.items()
    }
    return {
        "images": image_documents,
        "pre_main": {
            "medians": medians,
            "sample_count": EXPECTED_SAMPLE_COUNT,
            "samples": [
                sample.as_json(index)
                for index, sample in enumerate(samples, start=1)
            ],
            "unit": "ms",
        },
        "schema_version": SCHEMA_VERSION,
        "target": EXPECTED_TARGET,
    }


def _dependency_document(value: object, label: str) -> dict[str, str]:
    dependency = _exact_keys(value, {"command", "namespace", "path"}, label)
    command = dependency["command"]
    namespace = dependency["namespace"]
    path = dependency["path"]
    if not isinstance(command, str) or _DYLIB_COMMAND.fullmatch(f"cmd {command}") is None:
        raise EvidenceBlocked(f"{label} command is invalid")
    if namespace not in {
        "system_usr_lib",
        "system_library",
        "rpath",
        "loader_path",
        "executable_path",
    }:
        raise EvidenceBlocked(f"{label} namespace is invalid")
    if not isinstance(path, str) or not path or _SAFE_SUFFIX.fullmatch(path) is None:
        raise EvidenceBlocked(f"{label} path is invalid")
    if ".." in PurePosixPath(path).parts:
        raise EvidenceBlocked(f"{label} path traverses its namespace")
    return {"command": command, "namespace": namespace, "path": path}


def validate_observation(document: object) -> dict[str, Any]:
    root = _exact_keys(
        document, {"images", "pre_main", "schema_version", "target"}, "observation"
    )
    _exact_integer(
        root["schema_version"], SCHEMA_VERSION, "observation schema_version"
    )
    _exact_string(root["target"], EXPECTED_TARGET, "observation target")

    images = root["images"]
    if not isinstance(images, list) or len(images) != 2:
        raise EvidenceBlocked("observation images must contain exactly two entries")
    expected_names = [EXPECTED_APP_EXECUTABLE, EXPECTED_DEBUG_DYLIB]
    for index, (value, expected_name) in enumerate(zip(images, expected_names)):
        image = _exact_keys(
            value,
            {"bytes", "dependencies", "mod_init_func_bytes", "name"},
            f"observation image {index}",
        )
        _exact_string(image["name"], expected_name, f"observation image {index} name")
        _safe_positive_integer(image["bytes"], f"{expected_name} bytes")
        _safe_nonnegative_integer(
            image["mod_init_func_bytes"], f"{expected_name} mod_init_func_bytes"
        )
        dependencies = image["dependencies"]
        if not isinstance(dependencies, list) or not dependencies:
            raise EvidenceBlocked(f"{expected_name} dependencies must be non-empty")
        for dependency_index, dependency in enumerate(dependencies):
            _dependency_document(
                dependency, f"{expected_name} dependency {dependency_index}"
            )

    pre_main = _exact_keys(
        root["pre_main"], {"medians", "sample_count", "samples", "unit"}, "pre_main"
    )
    _exact_integer(
        pre_main["sample_count"], EXPECTED_SAMPLE_COUNT, "pre_main sample_count"
    )
    _exact_string(pre_main["unit"], "ms", "pre_main unit")
    samples = pre_main["samples"]
    if not isinstance(samples, list) or len(samples) != EXPECTED_SAMPLE_COUNT:
        raise EvidenceBlocked(
            f"pre_main samples must contain exactly {EXPECTED_SAMPLE_COUNT} entries"
        )
    component_values: dict[str, list[Decimal]] = {
        "initializer_ms": [],
        "rebase_binding_ms": [],
        "total_ms": [],
    }
    for offset, value in enumerate(samples, start=1):
        sample = _exact_keys(
            value,
            {"index", "initializer_ms", "rebase_binding_ms", "total_ms"},
            f"pre_main sample {offset}",
        )
        _exact_integer(sample["index"], offset, f"pre_main sample {offset} index")
        for component in component_values:
            component_values[component].append(
                _decimal(
                    sample[component],
                    f"pre_main sample {offset} {component}",
                    positive=component == "total_ms",
                )
            )
        if component_values["rebase_binding_ms"][-1] > component_values["total_ms"][-1]:
            raise EvidenceBlocked(f"pre_main sample {offset} rebase/binding exceeds total")
        if component_values["initializer_ms"][-1] > component_values["total_ms"][-1]:
            raise EvidenceBlocked(f"pre_main sample {offset} initializer exceeds total")

    medians = _exact_keys(
        pre_main["medians"], set(component_values), "pre_main medians"
    )
    for component, values in component_values.items():
        observed = _decimal(medians[component], f"pre_main median {component}")
        calculated = statistics.median(values)
        if observed != calculated:
            raise EvidenceBlocked(
                f"pre_main median {component} does not match its ten samples"
            )
    return root


def derive_baseline(observation: object) -> dict[str, object]:
    observed = validate_observation(observation)
    baseline_images: list[dict[str, object]] = []
    for image in observed["images"]:
        if image["mod_init_func_bytes"] != 0:
            raise EvidenceBlocked(
                f"cannot baseline nonzero __mod_init_func bytes in {image['name']}"
            )
        baseline_images.append(
            {
                "byte_ceiling": image["bytes"],
                "dependencies": image["dependencies"],
                "mod_init_func_bytes": 0,
                "name": image["name"],
            }
        )

    samples = observed["pre_main"]["samples"]
    medians = observed["pre_main"]["medians"]
    baseline_components: dict[str, object] = {}
    for component in ("initializer_ms", "rebase_binding_ms", "total_ms"):
        values = [_decimal(item[component], component) for item in samples]
        median = _decimal(medians[component], f"median {component}")
        allowance = max(Decimal("1"), median * Decimal("0.25"))
        ceiling = max(values) + allowance
        baseline_components[component] = {
            "baseline_median": _decimal_text(median),
            "baseline_samples": [_decimal_text(item) for item in values],
            "ceiling": _decimal_text(ceiling),
        }

    return {
        "derivation": {
            "binary_ceiling": DERIVATION_BINARY,
            "timing_ceiling": DERIVATION_TIMING,
        },
        "images": baseline_images,
        "pre_main": {
            "components": baseline_components,
            "sample_count": EXPECTED_SAMPLE_COUNT,
            "unit": "ms",
        },
        "schema_version": SCHEMA_VERSION,
        "status": "pinned",
        "target": EXPECTED_TARGET,
        "toolchain": "Xcode 26.6 / iOS Simulator / arm64",
    }


def validate_baseline(document: object) -> dict[str, Any]:
    if isinstance(document, dict) and document.get("status") == "placeholder":
        raise EvidenceBlocked("launch-structure baseline is still a placeholder")
    baseline = _exact_keys(
        document,
        {
            "derivation",
            "images",
            "pre_main",
            "schema_version",
            "status",
            "target",
            "toolchain",
        },
        "baseline",
    )
    _exact_integer(
        baseline["schema_version"], SCHEMA_VERSION, "baseline schema_version"
    )
    _exact_string(baseline["status"], "pinned", "baseline status")
    _exact_string(baseline["target"], EXPECTED_TARGET, "baseline target")
    _exact_string(
        baseline["toolchain"],
        "Xcode 26.6 / iOS Simulator / arm64",
        "baseline toolchain",
    )
    derivation = _exact_keys(
        baseline["derivation"], {"binary_ceiling", "timing_ceiling"}, "derivation"
    )
    _exact_string(
        derivation["binary_ceiling"], DERIVATION_BINARY, "binary derivation"
    )
    _exact_string(
        derivation["timing_ceiling"], DERIVATION_TIMING, "timing derivation"
    )

    images = baseline["images"]
    if not isinstance(images, list) or len(images) != 2:
        raise EvidenceBlocked("baseline images must contain exactly two entries")
    for index, expected_name in enumerate(
        (EXPECTED_APP_EXECUTABLE, EXPECTED_DEBUG_DYLIB)
    ):
        image = _exact_keys(
            images[index],
            {"byte_ceiling", "dependencies", "mod_init_func_bytes", "name"},
            f"baseline image {index}",
        )
        _exact_string(image["name"], expected_name, f"baseline image {index} name")
        _safe_positive_integer(image["byte_ceiling"], f"{expected_name} byte_ceiling")
        mod_init_bytes = _safe_nonnegative_integer(
            image["mod_init_func_bytes"],
            f"{expected_name} baseline mod_init_func_bytes",
        )
        if mod_init_bytes != 0:
            raise EvidenceBlocked(f"{expected_name} baseline must require zero mod-init bytes")
        dependencies = image["dependencies"]
        if not isinstance(dependencies, list) or not dependencies:
            raise EvidenceBlocked(f"{expected_name} baseline dependencies must be non-empty")
        for dependency_index, dependency in enumerate(dependencies):
            _dependency_document(
                dependency, f"{expected_name} baseline dependency {dependency_index}"
            )

    pre_main = _exact_keys(
        baseline["pre_main"], {"components", "sample_count", "unit"}, "baseline pre_main"
    )
    _exact_integer(
        pre_main["sample_count"],
        EXPECTED_SAMPLE_COUNT,
        "baseline sample_count",
    )
    _exact_string(pre_main["unit"], "ms", "baseline pre_main unit")
    components = _exact_keys(
        pre_main["components"],
        {"initializer_ms", "rebase_binding_ms", "total_ms"},
        "baseline components",
    )
    for name, value in components.items():
        component = _exact_keys(
            value,
            {"baseline_median", "baseline_samples", "ceiling"},
            f"baseline component {name}",
        )
        samples = component["baseline_samples"]
        if not isinstance(samples, list) or len(samples) != EXPECTED_SAMPLE_COUNT:
            raise EvidenceBlocked(
                f"baseline component {name} must contain ten samples"
            )
        numbers = [
            _decimal(item, f"baseline component {name} sample {index}", positive=name == "total_ms")
            for index, item in enumerate(samples, start=1)
        ]
        median = _decimal(component["baseline_median"], f"baseline component {name} median")
        ceiling = _decimal(component["ceiling"], f"baseline component {name} ceiling", positive=True)
        if median != statistics.median(numbers):
            raise EvidenceBlocked(f"baseline component {name} median is inconsistent")
        expected_ceiling = max(numbers) + max(Decimal("1"), median * Decimal("0.25"))
        if ceiling != expected_ceiling:
            raise EvidenceBlocked(f"baseline component {name} ceiling violates derivation")
    return baseline


def compare_observation(observation: object, baseline_document: object) -> list[str]:
    observed = validate_observation(observation)
    baseline = validate_baseline(baseline_document)
    failures: list[str] = []

    for observed_image, baseline_image in zip(
        observed["images"], baseline["images"]
    ):
        name = observed_image["name"]
        if observed_image["bytes"] > baseline_image["byte_ceiling"]:
            failures.append(
                f"{name} bytes {observed_image['bytes']} exceed ceiling "
                f"{baseline_image['byte_ceiling']}"
            )
        if observed_image["dependencies"] != baseline_image["dependencies"]:
            failures.append(f"{name} LC_*DYLIB closure changed")
        if observed_image["mod_init_func_bytes"] != 0:
            failures.append(
                f"{name} has {observed_image['mod_init_func_bytes']} nonzero "
                "__mod_init_func bytes"
            )

    for component in ("initializer_ms", "rebase_binding_ms", "total_ms"):
        current = _decimal(
            observed["pre_main"]["medians"][component],
            f"current median {component}",
        )
        ceiling = _decimal(
            baseline["pre_main"]["components"][component]["ceiling"],
            f"baseline ceiling {component}",
            positive=True,
        )
        if current > ceiling:
            failures.append(
                f"{component} median {_decimal_text(current)} ms exceeds ceiling "
                f"{_decimal_text(ceiling)} ms"
            )
    return failures


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        if not path.is_file():
            raise EvidenceBlocked(f"{label} file does not exist")
        return path.read_bytes()
    except EvidenceBlocked:
        raise
    except OSError as error:
        raise EvidenceBlocked(f"{label} file could not be read") from error


def _write_json(path: Path, document: object, label: str) -> None:
    try:
        if not path.parent.is_dir():
            raise EvidenceBlocked(f"{label} parent directory does not exist")
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except EvidenceBlocked:
        raise
    except OSError as error:
        raise EvidenceBlocked(f"{label} could not be written") from error


def _write_private_path(path: Path, value: Path) -> None:
    """Write a runner-local app path for shell use; never include it in evidence."""

    try:
        if not path.parent.is_dir():
            raise EvidenceBlocked("resolved-app path parent directory does not exist")
        path.write_text(str(value) + "\n", encoding="utf-8", newline="\n")
    except EvidenceBlocked:
        raise
    except OSError as error:
        raise EvidenceBlocked("resolved-app path file could not be written") from error


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve-app")
    resolve.add_argument("--build-settings-json", required=True, type=Path)
    resolve.add_argument("--output", required=True, type=Path)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--build-settings-json", required=True, type=Path)
    collect.add_argument(
        "--dyld-statistics-log", required=True, action="append", type=Path
    )
    collect.add_argument("--output", required=True, type=Path)

    derive = subparsers.add_parser("derive-baseline")
    derive.add_argument("--observed-json", required=True, type=Path)
    derive.add_argument("--output", required=True, type=Path)

    check = subparsers.add_parser("check")
    check.add_argument("--observed-json", required=True, type=Path)
    check.add_argument("--baseline", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if arguments.command == "resolve-app":
            images = parse_build_settings(
                _read_bytes(arguments.build_settings_json, "build-settings JSON")
            )
            _write_private_path(arguments.output, images[0].path.parent)
            print("G12 LAUNCH-STRUCTURE APP RESOLVED")
            return 0

        if arguments.command == "collect":
            observation = collect_observation(
                _read_bytes(arguments.build_settings_json, "build-settings JSON"),
                [
                    _read_bytes(path, f"DYLD_PRINT_STATISTICS log {index}")
                    for index, path in enumerate(
                        arguments.dyld_statistics_log, start=1
                    )
                ],
            )
            _write_json(arguments.output, observation, "observed evidence")
            print(
                "G12 LAUNCH-STRUCTURE COLLECTED: exact images and ten pre-main samples"
            )
            return 0

        observation = parse_json_bytes(
            _read_bytes(arguments.observed_json, "observed evidence"),
            "observed evidence",
        )
        if arguments.command == "derive-baseline":
            baseline = derive_baseline(observation)
            _write_json(arguments.output, baseline, "baseline")
            print("G12 LAUNCH-STRUCTURE BASELINE DERIVED")
            return 0

        baseline_document = parse_json_bytes(
            _read_bytes(arguments.baseline, "baseline"), "baseline"
        )
        failures = compare_observation(observation, baseline_document)
    except EvidenceBlocked as error:
        print(f"G12 LAUNCH-STRUCTURE BLOCKED: {error}", file=sys.stderr)
        return 2

    if failures:
        for failure in failures:
            print(f"G12 LAUNCH-STRUCTURE FAIL: {failure}", file=sys.stderr)
        return 1
    print(
        "G12 LAUNCH-STRUCTURE PASS: byte ceilings, exact LC_*DYLIB closure, "
        "zero static initializers, and ten-sample pre-main medians verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
