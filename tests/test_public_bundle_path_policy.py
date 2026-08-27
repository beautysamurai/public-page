from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TRANSLATIONS_PATH = ROOT / "site" / "data" / "i18n" / "en.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_public_bundle as bundle  # noqa: E402


class PublicBundlePathPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_translation = json.loads(
            TRANSLATIONS_PATH.read_text(encoding="utf-8")
        )

    def load_with_message(self, message: str) -> dict:
        value = json.loads(json.dumps(self.base_translation))
        value["editions"][0]["message"] = message
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "en.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return bundle.load_translation(path)

    def test_committed_translation_with_recent_route_is_valid(self):
        source = TRANSLATIONS_PATH.read_text(encoding="utf-8")
        self.assertIn("q-fin.TR /recent", source)
        loaded = bundle.load_translation(TRANSLATIONS_PATH)
        self.assertEqual(loaded, self.base_translation)

    def test_rejects_absolute_posix_paths_after_common_delimiters(self):
        unsafe_messages = (
            "Saved as path=/home/example/review.json",
            "See [/root/example/review.json]",
            "Config={/etc/project/secrets.json}",
            "Cache: /opt/company/cache.json",
            "State,/var/lib/app/state.json",
            "/usr/local/bin/tool was invoked.",
        )
        for message in unsafe_messages:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    bundle.PublicBundleError,
                    "absolute local path",
                ):
                    self.load_with_message(message)

    def test_rejects_local_uris_and_relative_paths(self):
        unsafe_messages = (
            "Open file:///home/example/review.json",
            "Open vscode:///home/example/review.json",
            "Saved under ../../Users/example/review.json",
            "Saved under ../private/review.json",
            r"Saved under .\Users\example\review.json",
            "Saved as path=../../Users/example/review.json",
            "See [../private/review.json]",
            r"Config={.\local\config.json}",
        )
        for message in unsafe_messages:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    bundle.PublicBundleError,
                    "local file or editor URI|relative local path",
                ):
                    self.load_with_message(message)

    def test_allows_urls_ratios_and_single_segment_route_labels(self):
        safe_messages = (
            "The q-fin.TR /recent listing was stale.",
            "Compare q-fin.TR /recent with q-fin.MF /new.",
            "See https://arxiv.org/abs/2608.24206 for the paper.",
            "Compare buy/sell flow and a rating of 8/10.",
            "Use /recent as a route label in this public explanation.",
            "Version 1.2.3 was reviewed.",
        )
        for message in safe_messages:
            with self.subTest(message=message):
                loaded = self.load_with_message(message)
                self.assertEqual(loaded["editions"][0]["message"], message)


if __name__ == "__main__":
    unittest.main()
