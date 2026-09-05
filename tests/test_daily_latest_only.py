from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import research_pipeline as pipeline
from tests.test_research_pipeline import config, entry, listing_html, FakeAnalyzer
from tests import test_research_pipeline_resilience as resilience


class LatestOnlyDailyTests(unittest.TestCase):
    def run_latest(self, root, analyzer, **kwargs):
        return pipeline.run_daily(
            config(), state_path=root / "state.json", output_dir=root / "daily",
            checked_at=datetime(2026, 9, 5, 1, tzinfo=timezone.utc),
            list_fetcher=lambda _: listing_html("Friday, 4 September 2026", new=("2609.03115",)),
            history_fetcher=lambda _: self.fail("scheduled daily must not fetch old batches"),
            metadata_fetcher=lambda ids: {key: entry(key) for key in ids},
            analyzer=analyzer, sleep_fn=lambda _: None, **kwargs,
        )

    def test_old_pending_batch_does_not_block_latest_or_change_old_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = pipeline._default_state() | {
                "lastCompletedBatchDate": "2026-08-31", "pendingBatchDate": "2026-09-01",
                "lastStatus": pipeline.UPDATE_NOT_CONFIRMED, "retryCount": 8,
            }
            pipeline.save_state(root / "state.json", state)
            old = pipeline._report(
                report_kind=pipeline.DAILY, report_date=date(2026, 9, 1),
                generated_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
                status=pipeline.UPDATE_NOT_CONFIRMED, message="Pending historical batch.",
                expected_batch_date=date(2026, 9, 1), observed_batch_date=date(2026, 9, 1),
                period_start=None, period_end=None, papers=[],
            )
            pipeline.persist_report(old, root / "daily")
            before = {p.name: p.read_bytes() for p in (root / "daily").iterdir()}
            analyzer = FakeAnalyzer()
            report = self.run_latest(root, analyzer)
            self.assertEqual(report["reportDate"], "2026-09-04")
            self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(analyzer.screened, ["2609.03115"])
            self.assertEqual(pipeline.load_state(root / "state.json")["lastCompletedBatchDate"], "2026-09-04")
            for name, original in before.items():
                self.assertEqual((root / "daily" / name).read_bytes(), original)
            self.assertEqual(pipeline.load_daily_reports(root / "daily", date(2026, 9, 1), date(2026, 9, 4))[0]["status"], pipeline.UPDATE_NOT_CONFIRMED)

    def test_completed_report_repairs_stale_state_before_network_or_api_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.run_latest(root, FakeAnalyzer())
            before = (root / "daily" / "2026-09-04.json").read_bytes()
            pipeline.save_state(root / "state.json", pipeline._default_state())
            with mock.patch.object(pipeline, "ResponsesAnalyzer", side_effect=AssertionError("no API")):
                report = pipeline.run_daily(
                    config(), state_path=root / "state.json", output_dir=root / "daily",
                    checked_at=datetime(2026, 9, 4, 15, tzinfo=timezone.utc),
                    list_fetcher=lambda _: self.fail("no fetch for completed report"),
                )
            self.assertEqual(report, first)
            self.assertEqual((root / "daily" / "2026-09-04.json").read_bytes(), before)

    def test_published_legacy_paper_is_skipped_without_metadata_or_api_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.json"
            history.write_text("{}", encoding="utf-8")
            validated_history = {"editions": [{"papers": [{"arxivId": "2609.03115v1"}]}]}
            with mock.patch.object(pipeline, "load_history", return_value=validated_history):
                analyzer = FakeAnalyzer()
                report = self.run_latest(root, analyzer, published_history=history)
            self.assertEqual(analyzer.screened, [])
            self.assertEqual(analyzer.pdfs, [])
            self.assertEqual(report["status"], pipeline.NO_RELEVANT_PAPERS)
            self.assertIn("Skipped 1", report["message"])

    def test_legacy_awaiting_pdf_is_not_restarted_by_a_later_daily_run(self):
        from tests.test_research_pipeline_resilience import analysis
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = {
                "schemaVersion": 1, "batchDate": "2026-09-01", "fingerprint": "a" * 64,
                "results": {"2609.03115": {
                    "status": "awaiting_pdf", "screenAnalysis": analysis(importance=4),
                    "finalAnalysis": None,
                }},
            }
            path = root / "checkpoints" / "2026-09-01.json"
            pipeline.atomic_write_json(path, checkpoint)
            original = path.read_bytes()
            analyzer = FakeAnalyzer()
            self.run_latest(root, analyzer)
            self.assertEqual(analyzer.screened, [])
            self.assertEqual(analyzer.pdfs, [])
            self.assertEqual(path.read_bytes(), original)

    def test_candidate_addition_reuses_finished_work(self):
        from tests.test_research_pipeline_resilience import FakeAnalyzer as Analyzer
        ids = ("2608.10001", "2608.10002")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resilience.DailyResilienceTests._run_daily(
                root, ids, Analyzer(), run_config=resilience.config(daily_time_budget=5),
                monotonic_fn=resilience.SequenceClock((0, 0, 0, 0, 5)),
            )
            resumed = Analyzer()
            report = resilience.DailyResilienceTests._run_daily(
                root, (*ids, "2608.10003"), resumed,
                run_config=resilience.config(), monotonic_fn=lambda: 0,
            )
            self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(resumed.screened, ["2608.10002", "2608.10003"])

    def test_changed_analyzed_paper_is_preserved_without_reanalysis(self):
        from tests.test_research_pipeline_resilience import FakeAnalyzer as Analyzer, entry as source_entry
        from dataclasses import replace
        ids = ("2608.10001", "2608.10002")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resilience.DailyResilienceTests._run_daily(
                root, ids, Analyzer(), run_config=resilience.config(daily_time_budget=5),
                monotonic_fn=resilience.SequenceClock((0, 0, 0, 0, 5)),
            )
            path = root / "checkpoints" / "2026-08-28.json"
            original = path.read_bytes()
            resumed = Analyzer()
            report = resilience.DailyResilienceTests._run_daily(
                root, ids, resumed, run_config=resilience.config(), monotonic_fn=lambda: 0,
                entries={ids[0]: replace(source_entry(ids[0]), abstract="Changed source abstract."), ids[1]: source_entry(ids[1])},
            )
            self.assertEqual(report["status"], pipeline.UPDATE_NOT_CONFIRMED)
            self.assertEqual(resumed.screened, [])
            self.assertEqual(path.read_bytes(), original)

    def test_daily_cli_does_not_enable_historical_recovery_by_default(self):
        parser = pipeline.build_argument_parser()
        self.assertFalse(parser.parse_args(["daily"]).recover_pending)
        self.assertTrue(parser.parse_args(["daily", "--recover-pending"]).recover_pending)


if __name__ == "__main__":
    unittest.main()
