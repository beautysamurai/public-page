from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import import_scheduler_history as importer  # noqa: E402
import research_publication as publication  # noqa: E402
import validate_public_bundle as bundle  # noqa: E402


def base_source_edition() -> dict:
    return {
        "editionId": "2026-08-28-daily-chatgpt-01",
        "editionDate": "2026-08-28",
        "editionKind": "daily",
        "sourceKind": "chatgpt-scheduled-task",
        "sourceLabel": "Reviewed public archive",
        "importedAt": "2026-08-28T01:00:00Z",
        "status": "NO_RELEVANT_PAPERS",
        "message": "更新を確認しましたが、推薦論文はありませんでした。",
        "expectedBatchDate": "2026-08-27",
        "observedBatchDate": "2026-08-27",
        "periodStart": None,
        "periodEnd": None,
        "sourceText": "## 日次レビュー\n\n推薦論文はありませんでした。",
        "papers": [],
    }


def base_english_edition() -> dict:
    return {
        "editionId": "2026-08-28-daily-chatgpt-01",
        "message": "The update was confirmed, with no recommended papers.",
        "sourceText": "## Daily review\n\nNo papers were recommended.",
        "papers": [],
    }


def completed_report(
    *,
    kind: str = "daily",
    report_date: str = "2026-08-29",
) -> dict:
    aggregate = kind in {"weekly", "monthly"}
    return {
        "schemaVersion": 2,
        "reportKind": kind,
        "reportDate": report_date,
        "generatedAt": f"{report_date}T02:03:04Z",
        "status": "UPDATE_CONFIRMED",
        "message": "The automated research report completed successfully.",
        "expectedBatchDate": None if aggregate else "2026-08-28",
        "observedBatchDate": None if aggregate else "2026-08-28",
        "periodStart": "2026-08-01" if aggregate else None,
        "periodEnd": report_date if aggregate else None,
        "papers": [
            {
                "metadata": {
                    "arxivId": "2608.27076v1",
                    "title": "Robust Signals *Across* Regimes",
                    "authors": ["A. Researcher", "B. Author"],
                    "submittedDate": "2026-08-27",
                    "updatedDate": "2026-08-28",
                    "categories": ["q-fin.TR", "cs.LG"],
                },
                "finalAnalysis": {
                    "classification": "mixed",
                    "summary": "複数regimeで壊れにくいsignal selectionを評価します。",
                    "mainResult": "複数期間を使う選択が単一期間より安定しました。",
                    "practicalApplication": "quote parameterの頑健性評価に応用できます。",
                    "methodology": "cross-regime validationと比較実験を使います。",
                    "limitations": "取引費用とsurvivorship biasに注意が必要です。",
                    "importance": 4,
                    "recommended": True,
                    "reason": "regimeを跨ぐmodel selectionが実務に直結します。",
                    "tags": ["電子取引", "market microstructure"],
                    "english": {
                        "classification": "Electronic trading and market microstructure",
                        "summary": "Evaluates signal selection designed to remain robust across regimes.",
                        "mainResult": "Selection across several periods was more stable than a single-period choice.",
                        "practicalApplication": "The design can test the robustness of quote parameters.",
                        "methodology": "Uses cross-regime validation and comparative experiments.",
                        "limitations": "Transaction costs and survivorship bias require caution.",
                        "reason": "Cross-regime model selection connects directly to practical controls.",
                        "tags": ["Electronic trading", "Market microstructure"],
                    },
                },
            }
        ],
    }


def empty_report(status: str) -> dict:
    value = completed_report()
    value["status"] = status
    value["papers"] = []
    return value


def write_base_bundle(root: Path) -> tuple[Path, Path]:
    history_path = root / "content" / "chatgpt_scheduler_history.json"
    translation_path = root / "site" / "data" / "i18n" / "en.json"
    history_path.parent.mkdir(parents=True)
    translation_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps(
            {"schemaVersion": 2, "editions": [base_source_edition()]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    translation_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "language": "en",
                "editions": [base_english_edition()],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return history_path, translation_path


class ResearchReportAdapterTests(unittest.TestCase):
    def test_maps_daily_report_to_exact_public_schemas(self):
        adapted = publication.adapt_research_report(completed_report())
        source = adapted.source_edition
        english = adapted.english_edition

        self.assertEqual(source["editionId"], "2026-08-29-daily-openai-01")
        self.assertEqual(source["sourceKind"], "openai-responses-api")
        self.assertEqual(source["editionKind"], "daily")
        self.assertEqual(source["status"], "UPDATE_CONFIRMED")
        self.assertEqual(source["papers"][0]["schedulerRank"], 1)
        self.assertEqual(source["papers"][0]["schedulerRating"], 4)
        self.assertEqual(
            source["papers"][0]["absUrl"],
            "https://arxiv.org/abs/2608.27076v1",
        )
        self.assertEqual(
            source["papers"][0]["pdfUrl"],
            "https://arxiv.org/pdf/2608.27076v1",
        )
        self.assertEqual(
            source["papers"][0]["topics"],
            [
                "q-fin.TR",
                "cs.LG",
                "Electronic trading",
                "Market microstructure",
            ],
        )
        for heading in ("### 要約", "### 主な結果", "### 実務への応用", "### 限界"):
            self.assertIn(heading, source["sourceText"])
        self.assertIn("**重要度: 4/5 — 推奨**", source["sourceText"])
        self.assertIn("Robust Signals \\*Across\\* Regimes", source["sourceText"])
        self.assertIn(
            "[arXiv:2608.27076v1](https://arxiv.org/abs/2608.27076v1)",
            source["sourceText"],
        )
        for heading in (
            "### Summary",
            "### Main result",
            "### Practical application",
            "### Limitations",
        ):
            self.assertIn(heading, english["sourceText"])
        self.assertEqual(
            english["papers"][0]["schedulerSummary"],
            completed_report()["papers"][0]["finalAnalysis"]["english"][
                "summary"
            ],
        )

    def test_monthly_report_is_supported_without_kind_based_ordering(self):
        report = completed_report(kind="monthly")
        adapted = publication.adapt_research_report(report)
        source = adapted.source_edition
        self.assertEqual(source["editionKind"], "monthly")
        self.assertEqual(source["status"], "MONTHLY_REVIEW")
        self.assertEqual(source["periodStart"], "2026-08-01")
        artifacts = importer.generate_artifacts(
            {
                "schemaVersion": 2,
                "editions": [
                    source,
                    base_source_edition()
                    | {
                        "editionId": "2026-08-29-daily-chatgpt-01",
                        "editionDate": "2026-08-29",
                        "importedAt": "2026-08-29T03:03:04Z",
                    },
                ],
            }
        )
        latest = json.loads(artifacts.latest)
        index = json.loads(artifacts.index)
        self.assertEqual(latest["editionId"], "2026-08-29-daily-chatgpt-01")
        monthly_index = next(
            item for item in index["editions"] if item["kind"] == "monthly"
        )
        self.assertEqual(monthly_index["title"], "Monthly research review")

    def test_incomplete_weekly_report_can_reuse_completed_papers(self):
        report = completed_report(kind="weekly")
        report["status"] = "UPDATE_NOT_CONFIRMED"
        adapted = publication.adapt_research_report(report)

        self.assertEqual(adapted.source_edition["editionKind"], "weekly")
        self.assertEqual(adapted.source_edition["status"], "UPDATE_NOT_CONFIRMED")
        self.assertEqual(len(adapted.source_edition["papers"]), 1)
        self.assertIn("完了扱いにせず", adapted.source_edition["sourceText"])

    def test_deferred_status_stays_incomplete_and_contains_no_papers(self):
        adapted = publication.adapt_research_report(
            empty_report("UPDATE_NOT_CONFIRMED")
        )
        self.assertEqual(adapted.source_edition["status"], "UPDATE_NOT_CONFIRMED")
        self.assertEqual(adapted.source_edition["papers"], [])
        self.assertIn("完了扱いにせず", adapted.source_edition["sourceText"])
        self.assertIn("remains incomplete", adapted.english_edition["sourceText"])

    def test_rejects_unknown_keys_at_every_model_boundary(self):
        locations = (
            (),
            ("papers", 0),
            ("papers", 0, "metadata"),
            ("papers", 0, "finalAnalysis"),
            ("papers", 0, "finalAnalysis", "english"),
        )
        for location in locations:
            with self.subTest(location=location):
                report = completed_report()
                target = report
                for component in location:
                    target = target[component]
                target["privateNotes"] = "must not be published"
                with self.assertRaisesRegex(
                    publication.ResearchReportSchemaError, "unknown"
                ):
                    publication.adapt_research_report(report)

    def test_rejects_unsafe_text_invalid_types_and_status_paper_mismatch(self):
        cases: list[tuple[str, dict]] = []

        unsafe = completed_report()
        unsafe["papers"][0]["finalAnalysis"]["summary"] = "turn12search4"
        cases.append(("internal reference", unsafe))

        html = completed_report()
        html["papers"][0]["finalAnalysis"]["limitations"] = "<script>x</script>"
        cases.append(("HTML", html))

        japanese_english = completed_report()
        japanese_english["papers"][0]["finalAnalysis"]["english"][
            "summary"
        ] = "英語ではありません"
        cases.append(("English text", japanese_english))

        boolean_importance = completed_report()
        boolean_importance["papers"][0]["finalAnalysis"]["importance"] = True
        cases.append(("importance", boolean_importance))

        mismatch = completed_report()
        mismatch["status"] = "NO_RELEVANT_PAPERS"
        cases.append(("cannot contain papers", mismatch))

        for label, report in cases:
            with self.subTest(label=label):
                with self.assertRaises(publication.ResearchReportSchemaError):
                    publication.adapt_research_report(report)


class ResearchPublicationPersistenceTests(unittest.TestCase):
    def test_append_is_valid_prefix_preserving_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history_path, translation_path = write_base_bundle(root)
            old_history = importer.load_history(history_path)
            old_translation = bundle.load_translation(translation_path)

            first = publication.publish_research_report(
                completed_report(), history_path, translation_path
            )
            self.assertTrue(first.changed)
            new_history = importer.load_history(history_path)
            new_translation = bundle.load_translation(translation_path)
            self.assertEqual(new_history["editions"][:-1], old_history["editions"])
            self.assertEqual(
                new_translation["editions"][:-1], old_translation["editions"]
            )
            self.assertEqual(
                [item["editionId"] for item in new_history["editions"]],
                [item["editionId"] for item in new_translation["editions"]],
            )
            history_bytes = history_path.read_bytes()
            translation_bytes = translation_path.read_bytes()

            second = publication.publish_research_report(
                completed_report(), history_path, translation_path
            )
            self.assertFalse(second.changed)
            self.assertEqual(history_path.read_bytes(), history_bytes)
            self.assertEqual(translation_path.read_bytes(), translation_bytes)

    def test_conflicting_immutable_id_refuses_all_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history_path, translation_path = write_base_bundle(root)
            report = completed_report()
            publication.publish_research_report(
                report, history_path, translation_path
            )
            history_before = history_path.read_bytes()
            translation_before = translation_path.read_bytes()
            changed = deepcopy(report)
            changed["papers"][0]["finalAnalysis"]["summary"] = (
                "異なるimmutable editionです。"
            )

            with self.assertRaises(publication.PublicationConflictError):
                publication.publish_research_report(
                    changed, history_path, translation_path
                )
            self.assertEqual(history_path.read_bytes(), history_before)
            self.assertEqual(translation_path.read_bytes(), translation_before)

    def test_partial_existing_edition_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history_path, translation_path = write_base_bundle(root)
            adapted = publication.adapt_research_report(completed_report())
            history = importer.load_history(history_path)
            history["editions"].append(adapted.source_edition)
            history_path.write_text(
                json.dumps(history, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = history_path.read_bytes(), translation_path.read_bytes()

            with self.assertRaises(publication.PublicationConflictError):
                publication.publish_research_report(
                    completed_report(), history_path, translation_path
                )
            self.assertEqual(
                (history_path.read_bytes(), translation_path.read_bytes()), before
            )

    def test_second_replace_failure_rolls_back_first_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history_path, translation_path = write_base_bundle(root)
            history_before = history_path.read_bytes()
            translation_before = translation_path.read_bytes()
            real_replace = os.replace
            failed = False

            def replace_with_one_failure(source, destination):
                nonlocal failed
                if Path(destination) == translation_path and not failed:
                    failed = True
                    raise OSError("simulated translation commit failure")
                return real_replace(source, destination)

            with mock.patch.object(
                publication.os, "replace", side_effect=replace_with_one_failure
            ):
                with self.assertRaisesRegex(OSError, "simulated"):
                    publication.publish_research_report(
                        completed_report(), history_path, translation_path
                    )
            self.assertEqual(history_path.read_bytes(), history_before)
            self.assertEqual(translation_path.read_bytes(), translation_before)

    def test_optional_site_regeneration_uses_existing_artifact_validator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history_path, translation_path = write_base_bundle(root)
            latest = root / "site" / "data" / "latest.json"
            archive = root / "site" / "data" / "archive"
            result = publication.publish_research_report(
                completed_report(),
                history_path,
                translation_path,
                regenerate_site=True,
                latest_path=latest,
                archive_dir=archive,
            )
            self.assertTrue(result.changed)
            self.assertTrue(latest.is_file())
            self.assertTrue((archive / "index.json").is_file())
            self.assertTrue(
                (archive / "2026-08-29-daily-openai-01.json").is_file()
            )
            self.assertEqual(
                importer.check_artifacts(
                    importer.generate_artifacts(importer.load_history(history_path)),
                    latest,
                    archive,
                ),
                [],
            )

            latest.unlink()
            retry = publication.publish_research_report(
                completed_report(),
                history_path,
                translation_path,
                regenerate_site=True,
                latest_path=latest,
                archive_dir=archive,
            )
            self.assertFalse(retry.changed)
            self.assertTrue(latest.is_file())
            self.assertIn(latest, retry.generated_paths)

    def test_incomplete_report_is_never_committed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history_path, translation_path = write_base_bundle(root)
            before = history_path.read_bytes(), translation_path.read_bytes()

            with self.assertRaisesRegex(
                publication.ResearchPublicationError,
                "incomplete",
            ):
                publication.publish_research_report(
                    empty_report("UPDATE_NOT_CONFIRMED"),
                    history_path,
                    translation_path,
                )

            self.assertEqual(
                (history_path.read_bytes(), translation_path.read_bytes()),
                before,
            )


class ResearchReportLoaderTests(unittest.TestCase):
    def test_loader_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_text(
                '{"schemaVersion":2,"schemaVersion":2}', encoding="utf-8"
            )
            with self.assertRaisesRegex(
                publication.ResearchReportSchemaError, "duplicate key"
            ):
                publication.load_research_report(path)


if __name__ == "__main__":
    unittest.main()
