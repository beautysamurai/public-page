from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_bundle_handoff as handoff  # noqa: E402


class BundleHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / handoff.HISTORY_NAME).write_bytes(b'{"schemaVersion": 2}\n')
        (self.root / handoff.TRANSLATION_NAME).write_bytes(
            b'{"schemaVersion": 1, "language": "en"}\n'
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_and_validate_complete_handoff(self):
        manifest = handoff.create_manifest(self.root)
        loaded = handoff.validate_handoff(self.root)
        self.assertEqual(loaded, manifest)
        self.assertEqual(
            {item.name for item in self.root.iterdir()},
            handoff.EXPECTED_ENTRIES,
        )

    def test_rejects_file_changed_after_manifest_creation(self):
        handoff.create_manifest(self.root)
        (self.root / handoff.TRANSLATION_NAME).write_text(
            '{"schemaVersion": 1, "language": "en", "changed": true}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(handoff.BundleHandoffError, "mismatch"):
            handoff.validate_handoff(self.root)

    def test_rejects_missing_completion_manifest(self):
        with self.assertRaisesRegex(handoff.BundleHandoffError, "missing"):
            handoff.validate_handoff(self.root)

    def test_rejects_boolean_schema_version(self):
        handoff.create_manifest(self.root)
        manifest_path = self.root / handoff.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schemaVersion"] = True
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            handoff.BundleHandoffError,
            "schemaVersion must be integer 1",
        ):
            handoff.validate_handoff(self.root)

    def test_rejects_unknown_top_level_file(self):
        handoff.create_manifest(self.root)
        (self.root / "notes.txt").write_text("private", encoding="utf-8")
        with self.assertRaisesRegex(handoff.BundleHandoffError, "unknown"):
            handoff.validate_handoff(self.root)

    def test_create_refuses_a_preexisting_or_partial_manifest(self):
        (self.root / handoff.MANIFEST_NAME).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
            handoff.BundleHandoffError,
            "exactly the two reviewed JSON files",
        ):
            handoff.create_manifest(self.root)


if __name__ == "__main__":
    unittest.main()
