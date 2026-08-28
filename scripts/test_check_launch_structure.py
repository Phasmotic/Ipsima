from __future__ import annotations

import copy
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import check_launch_structure as checker


TEST_TEMP_ROOT = Path(__file__).resolve().parent.parent / ".gauntlet"


OTOOL_EXECUTABLE = b"""\
/runner/build/Talaria.app/Talaria:
Load command 0
          cmd LC_LOAD_DYLIB
      cmdsize 56
         name /usr/lib/libSystem.B.dylib (offset 24)
Load command 1
          cmd LC_LOAD_DYLIB
      cmdsize 72
         name @rpath/Talaria.debug.dylib (offset 24)
Section
  sectname __text
   segname __TEXT
      size 0x0000000000000100
Section
  sectname __mod_init_func
   segname __DATA
      addr 0x0000000000000000
      size 0x0000000000000000
"""

OTOOL_DYLIB = b"""\
/runner/build/Talaria.app/Talaria.debug.dylib:
Load command 0
          cmd LC_ID_DYLIB
      cmdsize 64
         name @rpath/Talaria.debug.dylib (offset 24)
Load command 1
          cmd LC_LOAD_WEAK_DYLIB
      cmdsize 88
         name /System/Library/Frameworks/SwiftUI.framework/SwiftUI (offset 24)
Load command 2
          cmd LC_REEXPORT_DYLIB
      cmdsize 72
         name @loader_path/Support.dylib (offset 24)
"""


def statistics_log(
    total: str = "10",
    rebase_binding: str = "1",
    initializer: str = "2",
) -> bytes:
    return f"""\
Total pre-main time: {total} milliseconds (100.0%)
         dylib loading time: 6 milliseconds (60.0%)
        rebase/binding time: {rebase_binding} milliseconds (10.0%)
            ObjC setup time: 1 milliseconds (10.0%)
           initializer time: {initializer} milliseconds (20.0%)
""".encode()


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.build = root / "Build" / "Products" / "Debug-iphonesimulator"
        self.app = self.build / "Talaria.app"
        self.app.mkdir(parents=True)
        self.executable = self.app / "Talaria"
        self.debug_dylib = self.app / "Talaria.debug.dylib"
        self.executable.write_bytes(b"launcher")
        self.debug_dylib.write_bytes(b"debug-dylib")

    def build_settings_document(self) -> list[object]:
        return [
            {
                "action": "build",
                "buildSettings": {
                    "ARCHS": "arm64",
                    "CONFIGURATION": "Debug",
                    "EXECUTABLE_NAME": "Talaria",
                    "EXECUTABLE_PATH": "Talaria.app/Talaria",
                    "FULL_PRODUCT_NAME": "Talaria.app",
                    "PLATFORM_NAME": "iphonesimulator",
                    "PRODUCT_NAME": "Talaria",
                    "TARGET_BUILD_DIR": str(self.build),
                    "WRAPPER_NAME": "Talaria.app",
                },
                "target": "Talaria",
            }
        ]

    def build_settings_bytes(self, document: object | None = None) -> bytes:
        return json.dumps(
            self.build_settings_document() if document is None else document
        ).encode()


def fake_otool(image: checker.ResolvedImage) -> checker.MachOEvidence:
    raw = OTOOL_EXECUTABLE if image.label == "Talaria" else OTOOL_DYLIB
    return checker.parse_otool_output(raw)


def observation(fixture: Fixture) -> dict[str, object]:
    logs = [
        statistics_log(str(10 + index / 10), "1", "2")
        for index in range(checker.EXPECTED_SAMPLE_COUNT)
    ]
    return checker.collect_observation(
        fixture.build_settings_bytes(), logs, otool=fake_otool
    )


def set_component(
    document: dict[str, object], component: str, value: str
) -> None:
    pre_main = document["pre_main"]
    assert isinstance(pre_main, dict)
    samples = pre_main["samples"]
    assert isinstance(samples, list)
    for sample in samples:
        assert isinstance(sample, dict)
        sample[component] = value
    medians = pre_main["medians"]
    assert isinstance(medians, dict)
    medians[component] = value


class StrictJSONTests(unittest.TestCase):
    def test_duplicate_invalid_utf8_empty_and_float_json_block(self) -> None:
        cases = (
            b'{"a":1,"a":2}',
            b"\xff",
            b"",
            b" \r\n",
            b'{"measurement":1.5}',
            b'{"measurement":NaN}',
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_json_bytes(raw, "fixture")

    def test_json_integer_and_string_documents_are_allowed(self) -> None:
        self.assertEqual(
            checker.parse_json_bytes(b'{"count":10,"value":"1.5"}', "fixture"),
            {"count": 10, "value": "1.5"},
        )


class BuildSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Fixture(Path(self.temporary.name))

    def test_exact_target_resolves_both_required_images(self) -> None:
        images = checker.parse_build_settings(self.fixture.build_settings_bytes())
        self.assertEqual([image.label for image in images], ["Talaria", "Talaria.debug.dylib"])
        self.assertEqual(images[0].path, self.fixture.executable)
        self.assertEqual(images[1].path, self.fixture.debug_dylib)

    def test_unrelated_well_formed_target_is_ignored(self) -> None:
        document = self.fixture.build_settings_document()
        other = copy.deepcopy(document[0])
        assert isinstance(other, dict)
        other["target"] = "TalariaWidgets"
        document.insert(0, other)
        images = checker.parse_build_settings(self.fixture.build_settings_bytes(document))
        self.assertEqual(images[0].label, "Talaria")

    def test_missing_or_duplicate_talaria_target_blocks(self) -> None:
        original = self.fixture.build_settings_document()[0]
        cases = ([], [original, copy.deepcopy(original)])
        for document in cases:
            with self.subTest(count=len(document)), self.assertRaisesRegex(
                checker.EvidenceBlocked, "exactly one"
            ):
                checker.parse_build_settings(self.fixture.build_settings_bytes(document))

    def test_wrong_action_or_contract_setting_blocks(self) -> None:
        mutations = (
            ("entry", "action", "test"),
            ("settings", "ARCHS", "arm64 x86_64"),
            ("settings", "CONFIGURATION", "Release"),
            ("settings", "PLATFORM_NAME", "iphoneos"),
            ("settings", "EXECUTABLE_PATH", "Elsewhere/Talaria"),
            ("settings", "PRODUCT_NAME", "Other"),
        )
        for location, key, value in mutations:
            document = self.fixture.build_settings_document()
            entry = document[0]
            assert isinstance(entry, dict)
            target = entry if location == "entry" else entry["buildSettings"]
            assert isinstance(target, dict)
            target[key] = value
            with self.subTest(key=key), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_build_settings(self.fixture.build_settings_bytes(document))

    def test_missing_required_image_blocks(self) -> None:
        for path in (self.fixture.executable, self.fixture.debug_dylib):
            original = path.read_bytes()
            path.unlink()
            with self.subTest(path=path.name), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_build_settings(self.fixture.build_settings_bytes())
            path.write_bytes(original)

    def test_relative_target_build_directory_blocks(self) -> None:
        document = self.fixture.build_settings_document()
        settings = document[0]["buildSettings"]  # type: ignore[index]
        settings["TARGET_BUILD_DIR"] = "relative/build"  # type: ignore[index]
        with self.assertRaisesRegex(checker.EvidenceBlocked, "must be absolute"):
            checker.parse_build_settings(self.fixture.build_settings_bytes(document))

    def test_extra_top_level_entry_key_blocks(self) -> None:
        document = self.fixture.build_settings_document()
        document[0]["unexpected"] = True  # type: ignore[index]
        with self.assertRaisesRegex(checker.EvidenceBlocked, "unexpected"):
            checker.parse_build_settings(self.fixture.build_settings_bytes(document))


class OtoolParserTests(unittest.TestCase):
    def test_dependencies_are_exact_but_absolute_system_paths_are_namespaced(self) -> None:
        executable = checker.parse_otool_output(OTOOL_EXECUTABLE)
        dylib = checker.parse_otool_output(OTOOL_DYLIB)
        self.assertEqual(
            [item.as_json() for item in executable.dependencies],
            [
                {
                    "command": "LC_LOAD_DYLIB",
                    "namespace": "system_usr_lib",
                    "path": "libSystem.B.dylib",
                },
                {
                    "command": "LC_LOAD_DYLIB",
                    "namespace": "rpath",
                    "path": "Talaria.debug.dylib",
                },
            ],
        )
        self.assertEqual(dylib.dependencies[0].command, "LC_ID_DYLIB")
        self.assertEqual(dylib.dependencies[1].namespace, "system_library")
        self.assertEqual(dylib.dependencies[2].namespace, "loader_path")
        self.assertEqual(executable.mod_init_func_bytes, 0)

    def test_nonzero_mod_init_sizes_are_summed(self) -> None:
        raw = OTOOL_EXECUTABLE.replace(
            b"size 0x0000000000000000", b"size 0x0000000000000010"
        ) + b"""\
Section
  sectname __mod_init_func
      size 8
"""
        evidence = checker.parse_otool_output(raw)
        self.assertEqual(evidence.mod_init_func_bytes, 24)

    def test_unapproved_absolute_or_relative_install_name_blocks(self) -> None:
        for name in (b"/runner/private/Secret.dylib", b"relative/Secret.dylib"):
            raw = OTOOL_EXECUTABLE.replace(
                b"/usr/lib/libSystem.B.dylib", name, 1
            )
            with self.subTest(name=name), self.assertRaisesRegex(
                checker.EvidenceBlocked, "approved sanitized namespaces"
            ):
                checker.parse_otool_output(raw)

    def test_traversing_or_unsafe_install_name_blocks(self) -> None:
        for name in (b"@rpath/../Secret.dylib", b"@rpath/Secret\\Name.dylib"):
            raw = OTOOL_EXECUTABLE.replace(b"@rpath/Talaria.debug.dylib", name)
            with self.subTest(name=name), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_otool_output(raw)

    def test_missing_name_section_size_or_dependency_blocks(self) -> None:
        cases = (
            OTOOL_EXECUTABLE.replace(
                b"         name /usr/lib/libSystem.B.dylib (offset 24)\n", b""
            ),
            OTOOL_EXECUTABLE.replace(
                b"      size 0x0000000000000000\n", b"", 1
            ),
            b"Load command 0\n          cmd LC_UUID\n",
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_otool_output(raw)

    def test_empty_and_invalid_utf8_block(self) -> None:
        for raw in (b"", b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_otool_output(raw)


class DyldStatisticsTests(unittest.TestCase):
    def test_pinned_pre_main_format_parses_to_milliseconds(self) -> None:
        sample = checker.parse_dyld_statistics(statistics_log("10.5", "1.25", "2.5"))
        self.assertEqual(sample.total_ms, Decimal("10.5"))
        self.assertEqual(sample.rebase_binding_ms, Decimal("1.25"))
        self.assertEqual(sample.initializer_ms, Decimal("2.5"))

    def test_new_labels_and_all_supported_units_convert_exactly(self) -> None:
        raw = b"""\
Total pre-main time: 0.01 seconds (100%)
total time in rebase/binding: 1000 microseconds (10%)
total time in initializers: 2000000 nanoseconds (20%)
"""
        sample = checker.parse_dyld_statistics(raw)
        self.assertEqual(sample, checker.PreMainSample(Decimal("10"), Decimal("1"), Decimal("2")))

    def test_generic_total_time_cannot_masquerade_as_pre_main_time(self) -> None:
        raw = statistics_log().replace(b"Total pre-main time", b"total time")
        with self.assertRaisesRegex(checker.EvidenceBlocked, "missing total"):
            checker.parse_dyld_statistics(raw)

    def test_missing_duplicate_or_impossible_component_blocks(self) -> None:
        cases = (
            b"total time: 10 milliseconds\ninitializer time: 2 milliseconds\n",
            statistics_log() + b"total pre-main time: 10 milliseconds\n",
            statistics_log("1", "2", "0"),
            statistics_log("1", "0", "2"),
            statistics_log("0", "0", "0"),
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_dyld_statistics(raw)

    def test_unrelated_lines_and_component_zero_are_allowed(self) -> None:
        sample = checker.parse_dyld_statistics(
            b"path and device diagnostics are ignored\n" + statistics_log("3", "0", "0")
        )
        self.assertEqual(sample.rebase_binding_ms, 0)
        self.assertEqual(sample.initializer_ms, 0)


class CollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Fixture(Path(self.temporary.name))

    def test_collection_is_deterministic_sanitized_and_has_ten_samples(self) -> None:
        observed = observation(self.fixture)
        first = json.dumps(observed, sort_keys=True)
        second = json.dumps(observation(self.fixture), sort_keys=True)
        self.assertEqual(first, second)
        self.assertNotIn(str(self.fixture.root), first)
        self.assertNotIn("/usr/lib/", first)
        self.assertNotIn("/System/Library/", first)
        self.assertEqual(observed["pre_main"]["sample_count"], 10)  # type: ignore[index]
        self.assertEqual(observed["pre_main"]["medians"]["total_ms"], "10.45")  # type: ignore[index]

    def test_collection_requires_exactly_ten_logs(self) -> None:
        for count in (0, 9, 11):
            with self.subTest(count=count), self.assertRaisesRegex(
                checker.EvidenceBlocked, "exactly 10"
            ):
                checker.collect_observation(
                    self.fixture.build_settings_bytes(),
                    [statistics_log()] * count,
                    otool=fake_otool,
                )

    def test_collection_preserves_nonzero_static_initializer_for_a_fail_verdict(self) -> None:
        def nonzero(image: checker.ResolvedImage) -> checker.MachOEvidence:
            evidence = fake_otool(image)
            if image.label == "Talaria.debug.dylib":
                return checker.MachOEvidence(evidence.dependencies, 8)
            return evidence

        observed = checker.collect_observation(
            self.fixture.build_settings_bytes(),
            [statistics_log()] * 10,
            otool=nonzero,
        )
        self.assertEqual(observed["images"][1]["mod_init_func_bytes"], 8)  # type: ignore[index]


class BaselineAndComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Fixture(Path(self.temporary.name))
        self.observed = observation(self.fixture)
        self.baseline = checker.derive_baseline(self.observed)

    def test_derivation_pins_exact_bytes_closure_and_formula(self) -> None:
        self.assertEqual(
            self.baseline["derivation"],
            {
                "binary_ceiling": "exact observed bytes",
                "timing_ceiling": "max baseline observed sample + max(1 ms, 25% baseline median)",
            },
        )
        self.assertEqual(
            self.baseline["images"][0]["byte_ceiling"],  # type: ignore[index]
            len(b"launcher"),
        )
        total = self.baseline["pre_main"]["components"]["total_ms"]  # type: ignore[index]
        self.assertEqual(total["baseline_median"], "10.45")
        self.assertEqual(total["ceiling"], "13.5125")
        self.assertEqual(checker.compare_observation(self.observed, self.baseline), [])

    def test_placeholder_baseline_is_blocked(self) -> None:
        placeholder = {
            "schema_version": 1,
            "status": "placeholder",
        }
        with self.assertRaisesRegex(checker.EvidenceBlocked, "placeholder"):
            checker.compare_observation(self.observed, placeholder)

    def test_nonzero_static_initializer_cannot_be_baselined(self) -> None:
        broken = copy.deepcopy(self.observed)
        broken["images"][0]["mod_init_func_bytes"] = 8  # type: ignore[index]
        with self.assertRaisesRegex(checker.EvidenceBlocked, "cannot baseline"):
            checker.derive_baseline(broken)

    def test_tampered_median_derivation_or_ceiling_blocks(self) -> None:
        observed = copy.deepcopy(self.observed)
        observed["pre_main"]["medians"]["total_ms"] = "99"  # type: ignore[index]
        with self.assertRaisesRegex(checker.EvidenceBlocked, "does not match"):
            checker.validate_observation(observed)

        derivation = copy.deepcopy(self.baseline)
        derivation["derivation"]["timing_ceiling"] = "other"  # type: ignore[index]
        with self.assertRaises(checker.EvidenceBlocked):
            checker.validate_baseline(derivation)

        ceiling = copy.deepcopy(self.baseline)
        ceiling["pre_main"]["components"]["total_ms"]["ceiling"] = "999"  # type: ignore[index]
        with self.assertRaisesRegex(checker.EvidenceBlocked, "violates derivation"):
            checker.validate_baseline(ceiling)

    def test_noncanonical_decimal_and_extra_keys_block(self) -> None:
        for value in ("1.0", "01", " 1", "NaN"):
            observed = copy.deepcopy(self.observed)
            observed["pre_main"]["samples"][0]["initializer_ms"] = value  # type: ignore[index]
            with self.subTest(value=value), self.assertRaises(checker.EvidenceBlocked):
                checker.validate_observation(observed)

        observed = copy.deepcopy(self.observed)
        observed["machine_id"] = "must never be accepted"
        with self.assertRaisesRegex(checker.EvidenceBlocked, "unexpected"):
            checker.validate_observation(observed)

    def test_boolean_integer_fields_block(self) -> None:
        mutations = (
            ("schema",),
            ("index",),
            ("baseline_mod_init",),
        )
        for (kind,) in mutations:
            observed = copy.deepcopy(self.observed)
            baseline = copy.deepcopy(self.baseline)
            if kind == "schema":
                observed["schema_version"] = True
                callable_ = lambda: checker.validate_observation(observed)
            elif kind == "index":
                observed["pre_main"]["samples"][0]["index"] = True  # type: ignore[index]
                callable_ = lambda: checker.validate_observation(observed)
            else:
                baseline["images"][0]["mod_init_func_bytes"] = False  # type: ignore[index]
                callable_ = lambda: checker.validate_baseline(baseline)
            with self.subTest(kind=kind), self.assertRaises(checker.EvidenceBlocked):
                callable_()

    def test_size_dependency_and_mod_init_changes_each_fail(self) -> None:
        mutations = []
        size = copy.deepcopy(self.observed)
        size["images"][0]["bytes"] += 1  # type: ignore[index,operator]
        mutations.append((size, "bytes"))
        dependency = copy.deepcopy(self.observed)
        dependency["images"][1]["dependencies"][0]["path"] = "Changed.dylib"  # type: ignore[index]
        mutations.append((dependency, "closure changed"))
        initializer = copy.deepcopy(self.observed)
        initializer["images"][1]["mod_init_func_bytes"] = 8  # type: ignore[index]
        mutations.append((initializer, "__mod_init_func"))

        for current, marker in mutations:
            with self.subTest(marker=marker):
                failures = checker.compare_observation(current, self.baseline)
                self.assertTrue(any(marker in failure for failure in failures), failures)

    def test_each_pre_main_component_median_can_fail_independently(self) -> None:
        values = {
            "initializer_ms": "4",
            "rebase_binding_ms": "4",
            "total_ms": "14",
        }
        for component, value in values.items():
            current = copy.deepcopy(self.observed)
            set_component(current, component, value)
            if component != "total_ms":
                # Current total remains around 10 ms, so component validity holds.
                pass
            failures = checker.compare_observation(current, self.baseline)
            with self.subTest(component=component):
                self.assertTrue(any(component in failure for failure in failures), failures)

    def test_value_equal_to_ceiling_passes_strictly(self) -> None:
        current = copy.deepcopy(self.observed)
        ceiling = self.baseline["pre_main"]["components"]["initializer_ms"]["ceiling"]  # type: ignore[index]
        set_component(current, "initializer_ms", ceiling)
        self.assertEqual(checker.compare_observation(current, self.baseline), [])


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)
        self.settings = self.root / "settings.json"
        self.settings.write_bytes(self.fixture.build_settings_bytes())
        self.logs: list[Path] = []
        for index in range(10):
            path = self.root / f"dyld-{index + 1}.log"
            path.write_bytes(statistics_log())
            self.logs.append(path)

    def run_main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = checker.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_resolve_app_writes_private_file_without_logging_path(self) -> None:
        output = self.root / "app-path.txt"
        status, stdout, stderr = self.run_main(
            [
                "resolve-app",
                "--build-settings-json",
                str(self.settings),
                "--output",
                str(output),
            ]
        )
        self.assertEqual(status, 0)
        self.assertEqual(output.read_text().strip(), str(self.fixture.app))
        self.assertNotIn(str(self.fixture.root), stdout)
        self.assertEqual(stderr, "")

    def test_collect_uses_otool_and_succeeds_without_a_baseline(self) -> None:
        output = self.root / "observed.json"
        arguments = [
            "collect",
            "--build-settings-json",
            str(self.settings),
            "--output",
            str(output),
        ]
        for path in self.logs:
            arguments.extend(["--dyld-statistics-log", str(path)])

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            raw = OTOOL_EXECUTABLE if command[-1].endswith("Talaria") else OTOOL_DYLIB
            return subprocess.CompletedProcess(command, 0, raw, b"")

        with patch.object(checker.subprocess, "run", side_effect=run) as mocked:
            status, stdout, stderr = self.run_main(arguments)
        self.assertEqual(status, 0)
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(output.is_file())
        self.assertIn("COLLECTED", stdout)
        self.assertEqual(stderr, "")

    def test_placeholder_check_returns_blocked_after_observation_exists(self) -> None:
        observed_path = self.root / "observed.json"
        observed_path.write_text(json.dumps(observation(self.fixture)))
        baseline_path = self.root / "baseline.json"
        baseline_path.write_text('{"status":"placeholder"}\n')
        status, stdout, stderr = self.run_main(
            [
                "check",
                "--observed-json",
                str(observed_path),
                "--baseline",
                str(baseline_path),
            ]
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("BLOCKED", stderr)
        self.assertIn("placeholder", stderr)

    def test_derive_then_check_passes_and_tampered_observation_fails(self) -> None:
        observed = observation(self.fixture)
        observed_path = self.root / "observed.json"
        observed_path.write_text(json.dumps(observed))
        baseline_path = self.root / "baseline.json"

        status, stdout, stderr = self.run_main(
            [
                "derive-baseline",
                "--observed-json",
                str(observed_path),
                "--output",
                str(baseline_path),
            ]
        )
        self.assertEqual(status, 0)
        self.assertIn("BASELINE DERIVED", stdout)
        self.assertEqual(stderr, "")

        status, stdout, stderr = self.run_main(
            ["check", "--observed-json", str(observed_path), "--baseline", str(baseline_path)]
        )
        self.assertEqual(status, 0)
        self.assertIn("PASS", stdout)
        self.assertEqual(stderr, "")

        observed["images"][0]["bytes"] += 1  # type: ignore[index,operator]
        observed_path.write_text(json.dumps(observed))
        status, stdout, stderr = self.run_main(
            ["check", "--observed-json", str(observed_path), "--baseline", str(baseline_path)]
        )
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("FAIL", stderr)

    def test_otool_error_and_missing_input_return_blocked_without_leaking_paths(self) -> None:
        output = self.root / "observed.json"
        arguments = [
            "collect",
            "--build-settings-json",
            str(self.settings),
            "--output",
            str(output),
        ]
        for path in self.logs:
            arguments.extend(["--dyld-statistics-log", str(path)])
        completed = subprocess.CompletedProcess(["xcrun", "otool"], 1, b"", b"private path")
        with patch.object(checker.subprocess, "run", return_value=completed):
            status, stdout, stderr = self.run_main(arguments)
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("BLOCKED", stderr)
        self.assertNotIn(str(self.fixture.root), stderr)
        self.assertNotIn("private path", stderr)


if __name__ == "__main__":
    unittest.main()
