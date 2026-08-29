import copy
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import research_pipeline as pipeline  # noqa: E402


UTC = timezone.utc
PERIOD_DATE = date(2026, 8, 28)


def analysis(*, importance: int, marker: str = "") -> dict:
    japanese_suffix = f" {'あ' * len(marker)}" if marker else ""
    english_suffix = f" {marker}" if marker else ""
    return {
        "classification": "market_microstructure",
        "summary": f"日本語の要約です。{japanese_suffix}",
        "mainResult": f"主要な結果です。{japanese_suffix}",
        "practicalApplication": f"実務への応用です。{japanese_suffix}",
        "methodology": f"研究手法です。{japanese_suffix}",
        "limitations": f"限界です。{japanese_suffix}",
        "importance": importance,
        "recommended": True,
        "reason": f"読む理由です。{japanese_suffix}",
        "tags": ["市場マイクロストラクチャー"],
        "english": {
            "classification": "market_microstructure",
            "summary": f"English summary.{english_suffix}",
            "mainResult": f"Main result.{english_suffix}",
            "practicalApplication": f"Practical application.{english_suffix}",
            "methodology": f"Research methodology.{english_suffix}",
            "limitations": f"Study limitations.{english_suffix}",
            "reason": f"Reason to read.{english_suffix}",
            "tags": ["market microstructure"],
        },
    }


def paper(index: int, *, importance: int, marker: str = "") -> dict:
    return {
        "metadata": {
            "arxivId": f"2608.{index:05d}v1",
            "title": f"Paper {index}",
            "authors": ["Researcher One"],
            "submittedDate": PERIOD_DATE.isoformat(),
            "updatedDate": PERIOD_DATE.isoformat(),
            "categories": ["q-fin.TR"],
        },
        "finalAnalysis": analysis(importance=importance, marker=marker),
    }


def config(*, max_items: int, max_bytes: int) -> pipeline.PipelineConfig:
    return pipeline.PipelineConfig(
        categories=("q-fin.TR",),
        retries=0,
        synthesis_chunk_max_items=max_items,
        synthesis_chunk_max_bytes=max_bytes,
    )


def persist_daily(daily_dir: Path, papers: list[dict]) -> None:
    report = pipeline._report(
        report_kind=pipeline.DAILY,
        report_date=PERIOD_DATE,
        generated_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        status=pipeline.UPDATE_CONFIRMED,
        message="Daily review completed.",
        expected_batch_date=PERIOD_DATE,
        observed_batch_date=PERIOD_DATE,
        period_start=None,
        period_end=None,
        papers=papers,
    )
    pipeline.persist_report(report, daily_dir)


class RecordingAnalyzer:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def synthesize(self, papers, _report_kind, _period_start, _period_end):
        copied = copy.deepcopy(list(papers))
        self.calls.append(copied)
        return [
            {
                "arxivId": item["metadata"]["arxivId"],
                "finalAnalysis": copy.deepcopy(item["finalAnalysis"]),
            }
            for item in reversed(copied)
        ]


class RecordingResponses:
    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.outputs.pop(0)))


class BoundedSynthesisChunkingTests(unittest.TestCase):
    def run_aggregate(self, root: Path, papers: list[dict], analyzer, cfg):
        daily_dir = root / "daily"
        persist_daily(daily_dir, papers)
        return pipeline.run_aggregate(
            cfg,
            report_kind=pipeline.WEEKLY,
            period_start=PERIOD_DATE,
            period_end=PERIOD_DATE,
            daily_dir=daily_dir,
            output_dir=root / "reviews",
            generated_at=datetime(2026, 8, 28, 13, 0, tzinfo=UTC),
            analyzer=analyzer,
            sleep_fn=lambda _delay: None,
        )

    def test_item_limit_covers_every_id_once_and_merge_order_is_deterministic(self):
        source = [
            paper(5, importance=2),
            paper(1, importance=5),
            paper(4, importance=5),
            paper(2, importance=3),
            paper(3, importance=1),
        ]
        max_bytes = (
            pipeline._synthesis_prompt_bytes(
                source, pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
            )
            + 1
        )
        analyzer = RecordingAnalyzer()

        with tempfile.TemporaryDirectory() as directory:
            report = self.run_aggregate(
                Path(directory),
                source,
                analyzer,
                config(max_items=2, max_bytes=max_bytes),
            )

        self.assertEqual([len(items) for items in analyzer.calls], [2, 2, 1])
        called_ids = [
            item["metadata"]["arxivId"]
            for items in analyzer.calls
            for item in items
        ]
        expected_ids = [f"2608.{index:05d}v1" for index in range(1, 6)]
        self.assertEqual(called_ids, expected_ids)
        self.assertEqual(len(called_ids), len(set(called_ids)))
        self.assertEqual(
            [item["metadata"]["arxivId"] for item in report["papers"]],
            ["2608.00001v1", "2608.00004v1", "2608.00002v1", "2608.00005v1", "2608.00003v1"],
        )

    def test_byte_limit_chunks_every_input_and_single_oversize_fails_before_call(self):
        source = [
            paper(1, importance=3, marker="a" * 50),
            paper(2, importance=3, marker="b" * 100),
            paper(3, importance=3, marker="c" * 150),
        ]
        single_sizes = [
            pipeline._synthesis_prompt_bytes(
                [item], pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
            )
            for item in source
        ]
        byte_budget = max(single_sizes)
        analyzer = RecordingAnalyzer()

        with tempfile.TemporaryDirectory() as directory:
            self.run_aggregate(
                Path(directory),
                source,
                analyzer,
                config(max_items=10, max_bytes=byte_budget),
            )

        self.assertEqual([len(items) for items in analyzer.calls], [1, 1, 1])
        self.assertEqual(
            [
                item["metadata"]["arxivId"]
                for items in analyzer.calls
                for item in items
            ],
            ["2608.00001v1", "2608.00002v1", "2608.00003v1"],
        )
        for items in analyzer.calls:
            self.assertLessEqual(
                pipeline._synthesis_prompt_bytes(
                    items, pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
                ),
                byte_budget,
            )

        oversized = paper(9, importance=4, marker="oversized")
        oversized_bytes = pipeline._synthesis_prompt_bytes(
            [oversized], pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
        )
        never_called = RecordingAnalyzer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                pipeline.StructuredOutputError,
                "2608.00009v1 exceeds synthesisChunkMaxBytes",
            ):
                self.run_aggregate(
                    root,
                    [oversized],
                    never_called,
                    config(max_items=10, max_bytes=oversized_bytes - 1),
                )
            self.assertEqual(never_called.calls, [])
            self.assertFalse(
                (root / "reviews" / f"{PERIOD_DATE.isoformat()}.json").exists()
            )
            self.assertFalse(
                (root / "reviews" / f"{PERIOD_DATE.isoformat()}.md").exists()
            )

    def test_responses_schema_max_items_matches_each_chunk_length(self):
        responses = RecordingResponses([{"papers": []}, {"papers": []}])
        adapter = pipeline.ResponsesAnalyzer(
            config(max_items=20, max_bytes=200_000),
            SimpleNamespace(responses=responses),
        )
        source = [paper(1, importance=3), paper(2, importance=4)]

        adapter.synthesize(
            source, pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
        )
        adapter.synthesize(
            source[:1], pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
        )

        max_items = [
            call["text"]["format"]["schema"]["properties"]["papers"]["maxItems"]
            for call in responses.calls
        ]
        self.assertEqual(max_items, [2, 1])

    def test_run_aggregate_rejects_an_id_from_a_different_chunk(self):
        source = [paper(1, importance=3), paper(2, importance=4)]

        class OutsideChunkAnalyzer:
            def __init__(self):
                self.calls = []

            def synthesize(self, papers, _kind, _start, _end):
                self.calls.append(copy.deepcopy(list(papers)))
                return [
                    {
                        "arxivId": source[1]["metadata"]["arxivId"],
                        "finalAnalysis": copy.deepcopy(source[1]["finalAnalysis"]),
                    }
                ]

        analyzer = OutsideChunkAnalyzer()
        max_bytes = (
            pipeline._synthesis_prompt_bytes(
                source, pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
            )
            + 1
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                pipeline.StructuredOutputError, "outside its chunk"
            ):
                self.run_aggregate(
                    Path(directory),
                    source,
                    analyzer,
                    config(max_items=1, max_bytes=max_bytes),
                )

        self.assertEqual(len(analyzer.calls), 1)
        self.assertEqual(
            analyzer.calls[0][0]["metadata"]["arxivId"], "2608.00001v1"
        )

    def test_responses_adapter_rejects_a_different_version_of_a_chunk_id(self):
        source = [paper(1, importance=4)]
        responses = RecordingResponses(
            [
                {
                    "papers": [
                        {
                            "arxivId": "2608.00001v2",
                            "finalAnalysis": copy.deepcopy(
                                source[0]["finalAnalysis"]
                            ),
                        }
                    ]
                }
            ]
        )
        adapter = pipeline.ResponsesAnalyzer(
            config(max_items=20, max_bytes=200_000),
            SimpleNamespace(responses=responses),
        )

        with self.assertRaisesRegex(
            pipeline.StructuredOutputError, "outside its chunk"
        ):
            adapter.synthesize(
                source, pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
            )

    def test_all_empty_chunk_selections_report_what_was_processed(self):
        source = [paper(1, importance=4), paper(2, importance=3)]

        class EmptySelectionAnalyzer:
            def __init__(self):
                self.calls = []

            def synthesize(self, papers, _kind, _start, _end):
                self.calls.append(copy.deepcopy(list(papers)))
                return []

        analyzer = EmptySelectionAnalyzer()
        max_bytes = (
            pipeline._synthesis_prompt_bytes(
                source, pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
            )
            + 1
        )

        with tempfile.TemporaryDirectory() as directory:
            report = self.run_aggregate(
                Path(directory),
                source,
                analyzer,
                config(max_items=1, max_bytes=max_bytes),
            )

        self.assertEqual([len(items) for items in analyzer.calls], [1, 1])
        self.assertEqual(report["status"], pipeline.NO_RELEVANT_PAPERS)
        self.assertEqual(
            report["message"],
            "The period synthesis selected no papers from 2 stored paper(s) "
            "across 2 bounded request(s).",
        )

    def test_single_oversize_is_rejected_before_default_analyzer_initialization(self):
        oversized = paper(9, importance=4, marker="oversized")
        oversized_bytes = pipeline._synthesis_prompt_bytes(
            [oversized], pipeline.WEEKLY, PERIOD_DATE, PERIOD_DATE
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_dir = root / "daily"
            persist_daily(daily_dir, [oversized])
            with patch.object(pipeline, "ResponsesAnalyzer") as analyzer_factory:
                with self.assertRaisesRegex(
                    pipeline.StructuredOutputError,
                    "2608.00009v1 exceeds synthesisChunkMaxBytes",
                ):
                    pipeline.run_aggregate(
                        config(
                            max_items=10,
                            max_bytes=oversized_bytes - 1,
                        ),
                        report_kind=pipeline.WEEKLY,
                        period_start=PERIOD_DATE,
                        period_end=PERIOD_DATE,
                        daily_dir=daily_dir,
                        output_dir=root / "reviews",
                        generated_at=datetime(
                            2026, 8, 28, 13, 0, tzinfo=UTC
                        ),
                        analyzer=None,
                        sleep_fn=lambda _delay: None,
                    )
                analyzer_factory.assert_not_called()

    def test_direct_pipeline_config_rejects_zero_synthesis_chunk_items(self):
        with self.assertRaisesRegex(
            pipeline.ConfigurationError, "synthesis_chunk_max_items"
        ):
            pipeline.PipelineConfig(
                categories=("q-fin.TR",),
                synthesis_chunk_max_items=0,
                synthesis_chunk_max_bytes=200_000,
            )

        for changes, field in (
            (
                {"synthesis_chunk_max_items": 101},
                "synthesis_chunk_max_items",
            ),
            (
                {"synthesis_chunk_max_bytes": 750_001},
                "synthesis_chunk_max_bytes",
            ),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(pipeline.ConfigurationError, field):
                    pipeline.PipelineConfig(categories=("q-fin.TR",), **changes)

    def test_synthesis_call_bounds_output_and_disables_input_truncation(self):
        responses = RecordingResponses([{"papers": []}])
        adapter = pipeline.ResponsesAnalyzer(
            config(max_items=20, max_bytes=200_000),
            SimpleNamespace(responses=responses),
        )

        adapter.synthesize(
            [paper(1, importance=4)],
            pipeline.WEEKLY,
            PERIOD_DATE,
            PERIOD_DATE,
        )

        call = responses.calls[0]
        self.assertEqual(call["truncation"], "disabled")
        max_output_tokens = call["max_output_tokens"]
        self.assertIsInstance(max_output_tokens, int)
        self.assertNotIsInstance(max_output_tokens, bool)
        self.assertGreater(max_output_tokens, 0)
        self.assertLessEqual(max_output_tokens, 128_000)

    def test_incomplete_response_is_rejected_even_when_output_text_is_valid_json(self):
        response = SimpleNamespace(
            status="incomplete",
            incomplete_details={"reason": "max_output_tokens"},
            output_text='{"papers": []}',
        )

        class IncompleteResponses:
            def create(self, **_kwargs):
                return response

        adapter = pipeline.ResponsesAnalyzer(
            config(max_items=20, max_bytes=200_000),
            SimpleNamespace(responses=IncompleteResponses()),
        )

        with self.assertRaisesRegex(
            pipeline.StructuredOutputError,
            "did not return a completed response",
        ):
            adapter.synthesize(
                [paper(1, importance=4)],
                pipeline.WEEKLY,
                PERIOD_DATE,
                PERIOD_DATE,
            )


if __name__ == "__main__":
    unittest.main()
