from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from scripts.check_archive_embeds import (
    ExpectedBundle,
    Role,
    expected_bundles,
    verify_archive,
)


PROJECT = """\
name: Talaria
targets:
  Talaria:
    type: application
    platform: iOS
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: dev.example.app
  TalariaWidgets:
    type: app-extension
    platform: iOS
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: dev.example.app.widgets
  TalariaWatch:
    type: application
    platform: watchOS
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: dev.example.watch
  TalariaWatchWidgets:
    type: app-extension
    platform: watchOS
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: dev.example.watch.widgets
schemes: {}
"""


ROLES = [
    Role("iOS app", "Talaria", "application", "iOS"),
    Role("iOS widget extension", "TalariaWidgets", "app-extension", "iOS"),
    Role("watch app", "TalariaWatch", "application", "watchOS"),
    Role(
        "watch widget extension",
        "TalariaWatchWidgets",
        "app-extension",
        "watchOS",
    ),
]


class ArchiveEmbedCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project.yml"
        self.project.write_text(PROJECT, encoding="utf-8", newline="\n")
        self.archive = self.root / "Talaria.xcarchive"
        self.archive.mkdir()
        self.expected = expected_bundles(self.project, ROLES)
        for item in self.expected:
            self._write_bundle(
                item.relative_path, item.bundle_identifier, item.package_type
            )

    def _write_bundle(
        self,
        relative_path: PurePosixPath,
        bundle_identifier: str,
        package_type: str,
    ) -> None:
        bundle = self.archive.joinpath(*relative_path.parts)
        bundle.mkdir(parents=True, exist_ok=True)
        with (bundle / "Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": bundle_identifier,
                    "CFBundlePackageType": package_type,
                },
                stream,
            )

    def _remove_bundle(self, item: ExpectedBundle) -> None:
        info = self.archive.joinpath(*item.relative_path.parts, "Info.plist")
        info.unlink()

    def test_exact_graph_passes(self) -> None:
        successes, errors = verify_archive(self.archive, self.expected)
        self.assertFalse(errors)
        self.assertEqual(len(successes), 4)

    def test_each_missing_expected_bundle_fails(self) -> None:
        for item in self.expected:
            with self.subTest(item=item.label):
                self._remove_bundle(item)
                _successes, errors = verify_archive(self.archive, self.expected)
                self.assertTrue(any("is missing" in error for error in errors), errors)
                self._write_bundle(
                    item.relative_path, item.bundle_identifier, item.package_type
                )

    def test_decoy_widget_and_watch_bundles_do_not_pass(self) -> None:
        for item in self.expected[1:]:
            self._remove_bundle(item)
        self._write_bundle(
            PurePosixPath("Products/Applications/Talaria.app/PlugIns/Decoy.appex"),
            "dev.example.decoy.widgets",
            "XPC!",
        )
        self._write_bundle(
            PurePosixPath("Products/Applications/Talaria.app/Watch/DecoyWatch.app"),
            "dev.example.decoy.watch",
            "APPL",
        )
        _successes, errors = verify_archive(self.archive, self.expected)
        self.assertEqual(sum("is missing" in error for error in errors), 3, errors)

    def test_extra_bundle_fails_an_otherwise_complete_graph(self) -> None:
        self._write_bundle(
            PurePosixPath("Products/Applications/Talaria.app/PlugIns/Decoy.appex"),
            "dev.example.decoy.widgets",
            "XPC!",
        )
        _successes, errors = verify_archive(self.archive, self.expected)
        self.assertTrue(
            any("unexpected app or extension bundle" in error for error in errors),
            errors,
        )

    def test_ios_widget_at_wrong_nesting_fails(self) -> None:
        item = self.expected[1]
        self._remove_bundle(item)
        self._write_bundle(
            PurePosixPath("Products/Applications/Talaria.app/Elsewhere")
            / item.relative_path.name,
            item.bundle_identifier,
            item.package_type,
        )
        _successes, errors = verify_archive(self.archive, self.expected)
        self.assertTrue(any("wrong nesting" in error for error in errors), errors)

    def test_watch_app_at_wrong_nesting_fails(self) -> None:
        watch_app = self.expected[2]
        watch_widget = self.expected[3]
        self._remove_bundle(watch_widget)
        self._remove_bundle(watch_app)
        wrong_watch_path = PurePosixPath("Products/Applications") / watch_app.relative_path.name
        self._write_bundle(
            wrong_watch_path, watch_app.bundle_identifier, watch_app.package_type
        )
        self._write_bundle(
            wrong_watch_path / "PlugIns" / watch_widget.relative_path.name,
            watch_widget.bundle_identifier,
            watch_widget.package_type,
        )
        _successes, errors = verify_archive(self.archive, self.expected)
        self.assertEqual(sum("wrong nesting" in error for error in errors), 2, errors)

    def test_duplicate_bundle_identifier_is_ambiguous(self) -> None:
        item = self.expected[1]
        self._write_bundle(
            PurePosixPath("Products/Applications/Talaria.app/Other.appex"),
            item.bundle_identifier,
            item.package_type,
        )
        _successes, errors = verify_archive(self.archive, self.expected)
        self.assertTrue(any("is ambiguous" in error for error in errors), errors)

    def test_wrong_package_type_fails(self) -> None:
        item = self.expected[3]
        self._write_bundle(item.relative_path, item.bundle_identifier, "APPL")
        _successes, errors = verify_archive(self.archive, self.expected)
        self.assertTrue(
            any("CFBundlePackageType" in error for error in errors), errors
        )

    def test_non_dictionary_info_plist_fails_without_crashing(self) -> None:
        item = self.expected[1]
        info_path = self.archive.joinpath(*item.relative_path.parts, "Info.plist")
        with info_path.open("wb") as stream:
            plistlib.dump([], stream)

        _successes, errors = verify_archive(self.archive, self.expected)

        self.assertTrue(any("not a dictionary" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
