from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
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


if __name__ == "__main__":
    unittest.main()
