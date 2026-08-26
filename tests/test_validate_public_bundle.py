from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SYNC_SCRIPT = SCRIPTS / "sync_on_startup.ps1"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_public_bundle as bundle  # noqa: E402


def source_edition(edition_id: str, edition_date: str) -> dict:
    return {
        "editionId": edition_id,
        "editionDate": edition_date,
        "editionKind": "daily",
        "sourceKind": "chatgpt-scheduled-task",
        "sourceLabel": "Reviewed public archive",
        "importedAt": f"{edition_date}T01:00:00Z",
        "status": "NO_RELEVANT_PAPERS",
        "message": "Freshness was confirmed and no papers qualified.",
        "expectedBatchDate": edition_date,
        "observedBatchDate": edition_date,
        "periodStart": None,
        "periodEnd": None,
        "sourceText": "## Daily review\n\nNo relevant papers were selected.",
        "papers": [],
    }


def english_edition(edition_id: str) -> dict:
    return {
        "editionId": edition_id,
        "message": "Freshness was confirmed and no papers qualified.",
        "sourceText": "## Daily review\n\nNo relevant papers were selected.",
        "papers": [],
    }


def history(*editions: dict) -> dict:
    return {"schemaVersion": 2, "editions": list(editions)}


def translations(*editions: dict) -> dict:
    return {
        "schemaVersion": 1,
        "language": "en",
        "editions": list(editions),
    }


class PublicBundleValidationTests(unittest.TestCase):
    def setUp(self):
        self.old_source = source_edition("2026-08-20-daily-01", "2026-08-20")
        self.new_source = source_edition("2026-08-21-daily-01", "2026-08-21")
        self.old_english = english_edition(self.old_source["editionId"])
        self.new_english = english_edition(self.new_source["editionId"])

    def test_accepts_aligned_append_only_bundle(self):
        result = bundle.validate_bundle(
            history(self.old_source),
            translations(self.old_english),
            history(self.old_source, self.new_source),
            translations(self.old_english, self.new_english),
        )
        self.assertEqual(result, (1, 0, 0))

    def test_rejects_removing_a_public_edition(self):
        with self.assertRaisesRegex(bundle.PublicBundleError, "removes existing"):
            bundle.validate_bundle(
                history(self.old_source),
                translations(self.old_english),
                history(self.new_source),
                translations(self.new_english),
            )

    def test_rejects_bundle_that_omits_an_edition_from_fetched_base(self):
        remote_source = source_edition("2026-08-22-daily-01", "2026-08-22")
        remote_english = english_edition(remote_source["editionId"])
        incoming_source = source_edition("2026-08-23-daily-01", "2026-08-23")
        incoming_english = english_edition(incoming_source["editionId"])

        with self.assertRaisesRegex(bundle.PublicBundleError, "removes existing"):
            bundle.validate_bundle(
                history(self.old_source, remote_source),
                translations(self.old_english, remote_english),
                history(self.old_source, incoming_source),
                translations(self.old_english, incoming_english),
            )

    def test_rejects_modifying_an_immutable_public_edition(self):
        changed = deepcopy(self.old_source)
        changed["message"] = "Rewritten old public copy."
        with self.assertRaisesRegex(bundle.PublicBundleError, "modifies immutable"):
            bundle.validate_bundle(
                history(self.old_source),
                translations(self.old_english),
                history(changed, self.new_source),
                translations(self.old_english, self.new_english),
            )

    def test_rejects_modifying_an_immutable_translation(self):
        changed = deepcopy(self.old_english)
        changed["message"] = "Rewritten old English copy."
        with self.assertRaisesRegex(bundle.PublicBundleError, "modifies immutable"):
            bundle.validate_bundle(
                history(self.old_source),
                translations(self.old_english),
                history(self.old_source, self.new_source),
                translations(changed, self.new_english),
            )

    def test_rejects_source_translation_identity_mismatch(self):
        wrong = english_edition("2026-08-22-daily-01")
        with self.assertRaisesRegex(bundle.PublicBundleError, "exactly match"):
            bundle.validate_bundle(
                history(self.old_source),
                translations(self.old_english),
                history(self.old_source, self.new_source),
                translations(self.old_english, wrong),
            )

    def test_rejects_bundle_without_a_new_edition(self):
        with self.assertRaisesRegex(bundle.PublicBundleError, "no new editions"):
            bundle.validate_bundle(
                history(self.old_source),
                translations(self.old_english),
                history(self.old_source),
                translations(self.old_english),
            )

    def test_translation_loader_rejects_private_or_untranslated_text(self):
        unsafe_cases = (
            "Contact reviewer@example.com",
            "Saved under C:\\Users\\example\\review.json",
            r"Saved under \\WORKSTATION\Users\alice\review.json",
            r"Saved under \\SERVER\share\private.txt",
            r"Saved under \\192.168.1.10\share\file.json",
            "内部メモ",
            "turn12search4",
            "<script>alert(1)</script>",
        )
        for unsafe in unsafe_cases:
            with self.subTest(unsafe=unsafe):
                value = translations(self.old_english)
                value["editions"][0]["message"] = unsafe
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "en.json"
                    path.write_text(
                        json.dumps(value, ensure_ascii=False), encoding="utf-8"
                    )
                    with self.assertRaises(bundle.PublicBundleError):
                        bundle.load_translation(path)

    def test_translation_loader_rejects_unknown_fields(self):
        value = translations(self.old_english)
        value["privateNotes"] = "not public"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "en.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(bundle.PublicBundleError, "unknown"):
                bundle.load_translation(path)


class StartupSyncScriptContractTests(unittest.TestCase):
    def test_validation_uses_fetched_worktree_before_incoming_files_are_copied(self):
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        worktree_marker = '"worktree", "add", "-b", $branchName'
        validator_marker = '"scripts/validate_public_bundle.py"'
        copy_marker = "Copy-Item -LiteralPath $historyInbox"

        self.assertEqual(source.count(validator_marker), 1)
        self.assertLess(source.index(worktree_marker), source.index(validator_marker))
        self.assertLess(source.index(validator_marker), source.index(copy_marker))
        self.assertIn('"--current-history", $currentHistory', source)
        self.assertIn('"--current-translation", $currentTranslation', source)
        self.assertIn('$worktreeDirectory, "FETCH_HEAD"', source)


if __name__ == "__main__":
    unittest.main()
