from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import import_scheduler_history as importer


def paper(
    arxiv_id: str = "2608.13096v1",
    *,
    rank: int | None = 1,
    rating: int | float = 5,
) -> dict:
    return {
        "arxivId": arxiv_id,
        "title": "FlowLOB: Efficient and Controllable Limit Order Book Generation",
        "authors": ["Researcher One", "Researcher Two"],
        "submittedDate": "2026-08-13",
        "updatedDate": "2026-08-14",
        "topics": ["electronic trading", "limit order book"],
        "absUrl": f"https://arxiv.org/abs/{arxiv_id}",
        "pdfUrl": f"https://arxiv.org/pdf/{arxiv_id}",
        "schedulerRank": rank,
        "schedulerRating": rating,
        "schedulerRatingScale": 5,
        "schedulerLabel": "Electronic trading",
        "schedulerSummary": (
            "A deterministic scheduler summary with numerical results and "
            "a clear practical limitation."
        ),
        "ratings": [
            {"label": "Research novelty", "value": 5, "scale": 5},
            {"label": "Practical relevance", "value": 4, "scale": 5},
        ],
    }


def edition(
    edition_id: str = "2026-08-15-daily",
    *,
    edition_date: str = "2026-08-15",
    edition_kind: str = "daily",
    imported_at: str = "2026-08-15T07:32:01+09:00",
    status: str = "UPDATE_CONFIRMED",
    papers: list[dict] | None = None,
    source_text: str = (
        "今朝の確認結果です。\r\n"
        "Canonical paper link: https://arxiv.org/abs/2608.13096v1"
    ),
) -> dict:
    is_weekly = edition_kind == "weekly"
    return {
        "editionId": edition_id,
        "editionDate": edition_date,
        "editionKind": edition_kind,
        "sourceKind": "chatgpt-scheduled-task",
        "sourceLabel": (
            "Weekly arXiv reassessment"
            if is_weekly
            else "Daily arXiv scheduler result"
        ),
        "importedAt": imported_at,
        "status": status,
        "message": "A validated public scheduler edition.",
        "expectedBatchDate": None if is_weekly else edition_date,
        "observedBatchDate": None if is_weekly else "2026-08-14",
        "periodStart": "2026-08-10" if is_weekly else None,
        "periodEnd": edition_date if is_weekly else None,
        "sourceText": source_text,
        "papers": [paper()] if papers is None else papers,
    }


def history(*editions: dict) -> dict:
    return {
        "schemaVersion": 2,
        "editions": list(editions) or [edition()],
    }


def decode_json(content: bytes) -> dict:
    return json.loads(content.decode("utf-8"))


class ContractAndRoundTripTests(unittest.TestCase):
    def test_exact_v2_round_trip_preserves_source_text_and_scheduler_values(self):
        original = history(edition())
        validated = importer.validate_history(original)
        artifacts = importer.generate_artifacts(validated)
        latest = decode_json(artifacts.latest)

        self.assertEqual(latest["schemaVersion"], 2)
        self.assertEqual(
            set(latest),
            {"schemaVersion", *importer.EDITION_FIELDS},
        )
        self.assertEqual(
            latest["sourceText"],
            original["editions"][0]["sourceText"],
        )
        output_paper = latest["papers"][0]
        self.assertEqual(set(output_paper), set(importer.PAPER_FIELDS))
        self.assertEqual(output_paper["schedulerRating"], 5)
        self.assertEqual(
            output_paper["schedulerSummary"],
            original["editions"][0]["papers"][0]["schedulerSummary"],
        )
        self.assertNotIn("abstract", output_paper)
        self.assertNotIn("score", output_paper)
        self.assertNotIn("scoreReasons", output_paper)

    def test_extra_private_and_missing_fields_are_rejected_at_every_level(self):
        mutations = []
        extra_source = history(edition())
        extra_source["extra"] = True
        mutations.append(extra_source)

        private_edition = history(edition())
        private_edition["editions"][0]["_threadId"] = "hidden"
        mutations.append(private_edition)

        missing_paper = history(edition())
        del missing_paper["editions"][0]["papers"][0]["schedulerSummary"]
        mutations.append(missing_paper)

        extra_rating = history(edition())
        extra_rating["editions"][0]["papers"][0]["ratings"][0]["comment"] = "no"
        mutations.append(extra_rating)

        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(importer.HistorySchemaError):
                    importer.validate_history(value)

    def test_nullable_edition_fields_must_remain_present(self):
        value = history(edition())
        del value["editions"][0]["periodStart"]
        with self.assertRaises(importer.HistorySchemaError):
            importer.validate_history(value)

    def test_nullable_scheduler_ranks_round_trip_without_inference(self):
        weekly = edition(
            "2026-08-23-weekly-unranked",
            edition_date="2026-08-23",
            edition_kind="weekly",
            imported_at="2026-08-23T10:00:00+09:00",
            status="WEEKLY_REVIEW",
            papers=[
                paper("2608.19389v1", rank=None),
                paper("2608.07690v1", rank=None),
            ],
        )

        artifacts = importer.generate_artifacts(history(weekly))
        latest = decode_json(artifacts.latest)

        self.assertEqual(
            [item["schedulerRank"] for item in latest["papers"]],
            [None, None],
        )
        self.assertIsNone(
            decode_json(artifacts.archives[0][1])["papers"][0][
                "schedulerRank"
            ]
        )

    def test_duplicate_non_null_scheduler_ranks_are_rejected(self):
        value = history(
            edition(
                papers=[
                    paper("2608.19389v1", rank=1),
                    paper("2608.07690v1", rank=1),
                    paper("2608.13096v1", rank=None),
                ]
            )
        )

        with self.assertRaisesRegex(
            importer.HistorySchemaError,
            "duplicate assigned scheduler ranks",
        ):
            importer.validate_history(value)

    def test_canonical_links_are_derived_from_and_must_match_arxiv_id(self):
        for field, bad_url in (
            ("absUrl", "https://arxiv.org/abs/2608.99999v1"),
            ("pdfUrl", "https://arxiv.org/pdf/2608.13096v1.pdf"),
            ("absUrl", "http://arxiv.org/abs/2608.13096v1"),
        ):
            value = history(edition())
            value["editions"][0]["papers"][0][field] = bad_url
            with self.subTest(field=field, bad_url=bad_url):
                with self.assertRaises(importer.UnsafePublicContentError):
                    importer.validate_history(value)

    def test_duplicate_json_keys_are_rejected_by_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "history.json"
            source.write_text(
                '{"schemaVersion":2,"schemaVersion":2,"editions":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(importer.HistorySchemaError):
                importer.load_history(source)


class DeterministicOrderingTests(unittest.TestCase):
    def test_out_of_order_input_uses_date_instant_and_id_not_kind(self):
        older = edition(
            "2026-08-22-daily",
            edition_date="2026-08-22",
            imported_at="2026-08-23T18:00:00+09:00",
        )
        weekly_early = edition(
            "2026-08-23-weekly",
            edition_date="2026-08-23",
            edition_kind="weekly",
            imported_at="2026-08-23T09:00:00+09:00",
            status="WEEKLY_REVIEW",
        )
        daily_later = edition(
            "2026-08-23-daily",
            edition_date="2026-08-23",
            imported_at="2026-08-23T00:30:01Z",
        )
        first = importer.generate_artifacts(
            history(weekly_early, older, daily_later)
        )
        second = importer.generate_artifacts(
            history(daily_later, weekly_early, older)
        )

        self.assertEqual(first, second)
        self.assertEqual(
            decode_json(first.latest)["editionId"],
            "2026-08-23-daily",
            "daily wins because 00:30:01Z is 09:30:01 JST, not by kind",
        )
        index_ids = [
            item["editionId"]
            for item in decode_json(first.index)["editions"]
        ]
        self.assertEqual(
            index_ids,
            [
                "2026-08-23-daily",
                "2026-08-23-weekly",
                "2026-08-22-daily",
            ],
        )

    def test_same_instant_uses_edition_id_as_only_tie_breaker(self):
        daily = edition(
            "2026-08-23-a-daily",
            edition_date="2026-08-23",
            imported_at="2026-08-23T10:00:00+09:00",
        )
        weekly = edition(
            "2026-08-23-z-weekly",
            edition_date="2026-08-23",
            edition_kind="weekly",
            imported_at="2026-08-23T01:00:00Z",
            status="WEEKLY_REVIEW",
        )
        artifacts = importer.generate_artifacts(history(daily, weekly))
        self.assertEqual(
            decode_json(artifacts.latest)["editionId"],
            "2026-08-23-z-weekly",
        )

    def test_duplicate_and_unsafe_edition_ids_are_rejected(self):
        duplicate = history(edition(), edition())
        with self.assertRaises(importer.HistorySchemaError):
            importer.validate_history(duplicate)

        for edition_id in (
            "../escape",
            "UPPERCASE",
            "index",
            "6a7f9734-03bc-83e8-af16-b0f8195b1ba5",
        ):
            with self.subTest(edition_id=edition_id):
                with self.assertRaises(importer.HistorySchemaError):
                    importer.validate_history(
                        history(edition(edition_id))
                    )


class PublicSanitizerTests(unittest.TestCase):
    def test_forbidden_public_content_is_rejected_without_rewriting(self):
        forbidden = (
            "\ue200cite\ue202turn123search4\ue201",
            "https://arxiv.org/abs/2608.13096v1?utm_source=chatgpt.com",
            "https://example.com/research",
            "<script>alert('x')</script>",
            "Contact private.person@example.com",
            r"C:\Users\someone\private.txt",
            "/home/someone/private.txt",
            "../private/history.json",
            "javascript:alert(1)",
            "ftp://example.com/research",
            "chatgpt.com/private-thread",
            "threadId: 6a7f9734-03bc-83e8-af16-b0f8195b1ba5",
            "https://chatgpt.com/c/6a7f9734-03bc-83e8-af16-b0f8195b1ba5",
        )
        for source_text in forbidden:
            value = history(edition(source_text=source_text))
            with self.subTest(source_text=source_text):
                with self.assertRaises(
                    importer.UnsafePublicContentError
                ):
                    importer.validate_history(value)

    def test_sanitizer_applies_to_nested_scheduler_fields(self):
        value = history(edition())
        value["editions"][0]["papers"][0]["schedulerSummary"] = (
            "See https://example.invalid/private"
        )
        with self.assertRaises(importer.UnsafePublicContentError):
            importer.validate_history(value)

    def test_canonical_arxiv_markdown_links_and_plain_text_are_preserved(self):
        source_text = (
            "結果をそのまま保持します。\n\n"
            "[Abstract](https://arxiv.org/abs/2608.13096v1)\n"
            "[PDF](https://arxiv.org/pdf/2608.13096v1)"
        )
        value = history(edition(source_text=source_text))
        validated = importer.validate_history(value)
        self.assertEqual(
            validated["editions"][0]["sourceText"],
            source_text,
        )

    def test_invalid_dates_timestamps_ratings_and_rank_are_rejected(self):
        mutations = []
        naive_time = history(edition())
        naive_time["editions"][0]["importedAt"] = "2026-08-15T07:32:01"
        mutations.append(naive_time)

        bad_date = history(edition())
        bad_date["editions"][0]["editionDate"] = "2026-02-30"
        mutations.append(bad_date)

        bad_rating = history(edition())
        bad_rating["editions"][0]["papers"][0]["schedulerRating"] = 6
        mutations.append(bad_rating)

        bool_rank = history(edition())
        bool_rank["editions"][0]["papers"][0]["schedulerRank"] = True
        mutations.append(bool_rank)

        float_schema = history(edition())
        float_schema["schemaVersion"] = 2.0
        mutations.append(float_schema)

        list_status = history(edition())
        list_status["editions"][0]["status"] = ["UPDATE_CONFIRMED"]
        mutations.append(list_status)

        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(importer.HistorySchemaError):
                    importer.validate_history(value)


class PersistenceAndCheckTests(unittest.TestCase):
    def _artifacts(self) -> importer.GeneratedArtifacts:
        daily = edition(
            "2026-08-23-daily-0930",
            edition_date="2026-08-23",
            imported_at="2026-08-23T09:30:00+09:00",
        )
        weekly = edition(
            "2026-08-23-weekly-1000",
            edition_date="2026-08-23",
            edition_kind="weekly",
            imported_at="2026-08-23T10:00:00+09:00",
            status="WEEKLY_REVIEW",
            papers=[
                paper("2608.13096v1", rank=1),
                paper("2608.12016v1", rank=2, rating=4),
            ],
        )
        return importer.generate_artifacts(history(daily, weekly))

    def test_same_day_editions_get_unique_archives_and_exact_index(self):
        artifacts = self._artifacts()
        self.assertEqual(
            [name for name, _content in artifacts.archives],
            [
                "2026-08-23-weekly-1000.json",
                "2026-08-23-daily-0930.json",
            ],
        )
        index = decode_json(artifacts.index)
        self.assertEqual(
            index,
            {
                "schemaVersion": 2,
                "editions": [
                    {
                        "editionId": "2026-08-23-weekly-1000",
                        "date": "2026-08-23",
                        "kind": "weekly",
                        "path": "2026-08-23-weekly-1000.json",
                        "status": "WEEKLY_REVIEW",
                        "paperCount": 2,
                        "sourceKind": "chatgpt-scheduled-task",
                        "title": "Weekly research review",
                    },
                    {
                        "editionId": "2026-08-23-daily-0930",
                        "date": "2026-08-23",
                        "kind": "daily",
                        "path": "2026-08-23-daily-0930.json",
                        "status": "UPDATE_CONFIRMED",
                        "paperCount": 1,
                        "sourceKind": "chatgpt-scheduled-task",
                        "title": "Daily research screen",
                    },
                ],
            },
        )
        self.assertEqual(
            decode_json(artifacts.latest)["editionId"],
            "2026-08-23-weekly-1000",
        )
        self.assertEqual(
            decode_json(artifacts.latest)["sourceLabel"],
            "Weekly arXiv reassessment",
        )

    def test_persist_is_idempotent_and_never_clobbers_archive_history(self):
        artifacts = self._artifacts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest = root / "site" / "data" / "latest.json"
            archive = root / "site" / "data" / "archive"

            changed = importer.persist_artifacts(
                artifacts,
                latest,
                archive,
            )
            self.assertEqual(len(changed), 4)
            self.assertEqual(
                importer.persist_artifacts(
                    artifacts,
                    latest,
                    archive,
                ),
                [],
            )
            self.assertEqual(
                importer.check_artifacts(
                    artifacts,
                    latest,
                    archive,
                ),
                [],
            )

            immutable = archive / "2026-08-23-daily-0930.json"
            immutable.write_bytes(b"different historical bytes\n")
            latest_before = latest.read_bytes()
            index_before = (archive / "index.json").read_bytes()
            with self.assertRaises(importer.ArchiveConflictError):
                importer.persist_artifacts(
                    artifacts,
                    latest,
                    archive,
                )
            self.assertEqual(latest.read_bytes(), latest_before)
            self.assertEqual(
                (archive / "index.json").read_bytes(),
                index_before,
            )
            self.assertEqual(
                immutable.read_bytes(),
                b"different historical bytes\n",
            )

    def test_check_mode_compares_bytes_and_performs_no_writes(self):
        artifacts = self._artifacts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "content" / "history.json"
            latest = root / "site" / "data" / "latest.json"
            archive = root / "site" / "data" / "archive"
            source.parent.mkdir(parents=True)
            source.write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "editions": [
                            decode_json(content)
                            | {}
                            for _name, content in reversed(
                                artifacts.archives
                            )
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            # Archive documents include schemaVersion; source editions do not.
            loaded = json.loads(source.read_text(encoding="utf-8"))
            loaded["editions"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key != "schemaVersion"
                }
                for item in loaded["editions"]
            ]
            source.write_text(
                json.dumps(loaded, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual(
                importer.main(
                    [
                        "--source",
                        str(source),
                        "--output",
                        str(latest),
                        "--archive-dir",
                        str(archive),
                    ]
                ),
                0,
            )
            self.assertEqual(
                importer.main(
                    [
                        "--source",
                        str(source),
                        "--output",
                        str(latest),
                        "--archive-dir",
                        str(archive),
                        "--check",
                    ]
                ),
                0,
            )
            latest.write_bytes(b"stale bytes\n")
            before = latest.read_bytes()
            self.assertEqual(
                importer.main(
                    [
                        "--source",
                        str(source),
                        "--output",
                        str(latest),
                        "--archive-dir",
                        str(archive),
                        "--check",
                    ]
                ),
                1,
            )
            self.assertEqual(latest.read_bytes(), before)

    def test_check_on_missing_targets_does_not_create_directories(self):
        artifacts = self._artifacts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest = root / "missing" / "latest.json"
            archive = root / "missing" / "archive"
            mismatches = importer.check_artifacts(
                artifacts,
                latest,
                archive,
            )
            self.assertEqual(len(mismatches), 4)
            self.assertFalse((root / "missing").exists())


if __name__ == "__main__":
    unittest.main()
