import copy
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_pipeline as p
import research_recovery as recovery
import research_publication as publication
from test_research_pipeline import listing_html, entry, FakeAnalyzer, config
from test_research_pipeline_chunking import paper, analysis, RecordingResponses
from test_research_publication import completed_report, write_base_bundle

NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
CFG = config(no_announcement_dates=(date(2026, 9, 7),))


def daily(day, status=p.NO_RELEVANT_PAPERS, papers=None, expected=None):
    return p._report(report_kind="daily", report_date=day, generated_at=NOW,
        status=status, message="Stored daily status.", expected_batch_date=expected or day,
        observed_batch_date=expected or day, period_start=None, period_end=None, papers=papers or [])


def marker(root, kind, end):
    path = root / f"research/pending-periods/{kind}/{end}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schemaVersion": 1, "reportKind": kind, "periodEnd": end}), encoding="utf-8")
    return path


def ready_week(root):
    marker(root, "weekly", "2026-09-04")
    for offset in range(5):
        p.persist_report(daily(date(2026, 8, 31) + timedelta(days=offset)), root / "research/daily")


class CalendarRecoveryTests(unittest.TestCase):
    def test_eastern_holiday_does_not_skip_prior_evening_batch(self):
        for day, expected in [(5, 4), (6, 4), (7, 7), (8, 7), (9, 9)]:
            self.assertEqual(p.expected_batch_date(NOW.replace(day=day), CFG.no_announcement_dates), date(2026, 9, expected))
        self.assertEqual(p.expected_batch_date(datetime(2027, 1, 1, 12, tzinfo=timezone.utc), [date(2026, 12, 31)]), date(2026, 12, 31))

    def test_winter_announcement_is_not_due_before_0100_utc(self):
        self.assertEqual(p.expected_batch_date(datetime(2026, 11, 10, 0, 30, tzinfo=timezone.utc)), date(2026, 11, 9))
        self.assertEqual(p.expected_batch_date(datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc)), date(2026, 11, 10))

    def test_real_legacy_state_recovers_once_and_preserves_completed_json(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_path, daily_dir = root / "state.json", root / "daily"
                state = p._default_state()
                state.update(lastCompletedBatchDate="2026-09-04", pendingBatchDate="2026-09-08", retryCount=1)
                p.save_state(state_path, state)
                p.persist_report(daily(date(2026, 9, 7), p.NO_NEW_BATCH_EXPECTED, expected=date(2026, 9, 4)), daily_dir)
                fetcher = (lambda _: (_ for _ in ()).throw(p.ListingParseError("offline"))) if fail else lambda _: listing_html("Monday, 7 September 2026", new=("2609.04917",))
                result = p.run_daily(CFG, state_path=state_path, output_dir=daily_dir, checked_at=NOW,
                    recover_pending=True, list_fetcher=fetcher, metadata_fetcher=lambda _: {"2609.04917": entry("2609.04917")},
                    analyzer=FakeAnalyzer(), sleep_fn=lambda _: None)
                self.assertEqual(result["reportDate"], "2026-09-07")
                state = p.load_state(state_path)
                if fail:
                    self.assertEqual(state["lastCompletedBatchDate"], "2026-09-04")
                    self.assertEqual(state["pendingBatchDate"], "2026-09-07")
                    self.assertIn(result["status"], {p.UPDATER_OFFLINE, p.UPDATE_NOT_CONFIRMED})
                else:
                    self.assertEqual(result["status"], p.UPDATE_CONFIRMED)
                    self.assertEqual(state["lastCompletedBatchDate"], "2026-09-07")
                    self.assertIsNone(state["pendingBatchDate"])
                    original = (daily_dir / "2026-09-07.json").read_bytes()
                    with patch.object(p, "fetch_listing_page", side_effect=AssertionError("must reuse")), patch.object(p, "ResponsesAnalyzer", side_effect=AssertionError("must reuse")):
                        p.run_daily(CFG, state_path=state_path, output_dir=daily_dir, checked_at=NOW, recover_pending=True)
                    self.assertEqual(original, (daily_dir / "2026-09-07.json").read_bytes())

    def test_false_empty_is_not_coverage_and_suppressed_day_is_not_required(self):
        reports = [daily(date(2026, 9, 7), p.NO_NEW_BATCH_EXPECTED, expected=date(2026, 9, 4)), daily(date(2026, 9, 8), p.UPDATE_NOT_CONFIRMED)]
        self.assertEqual(p.missing_batch_dates(CFG, reports, date(2026, 9, 7), date(2026, 9, 8)), [date(2026, 9, 7)])
        reports[0] = daily(date(2026, 9, 7))
        self.assertEqual(p.missing_batch_dates(CFG, reports, date(2026, 9, 7), date(2026, 9, 8)), [])

    def test_missing_coverage_does_not_construct_paid_client(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, "ResponsesAnalyzer", side_effect=AssertionError("no paid request")):
            root = Path(tmp)
            p.persist_report(daily(date(2026, 9, 4), p.UPDATE_CONFIRMED, [paper(1, importance=4)]), root / "daily")
            result = p.run_aggregate(CFG, report_kind="weekly", period_start=date(2026, 8, 29), period_end=date(2026, 9, 4), daily_dir=root / "daily", output_dir=root / "weekly")
            self.assertEqual(result["status"], p.UPDATE_NOT_CONFIRMED)


class PeriodRetryTests(unittest.TestCase):
    def test_prompt_budget_and_chunk_cap_defer_without_blocking_daily(self):
        for chunks in (p.StructuredOutputError("too large"), [[], [], []]):
            with self.subTest(chunks=chunks), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ready_week(root)
                with patch.object(p, "_unique_stored_papers", return_value=[paper(1, importance=4)]), patch.object(p, "_build_synthesis_chunks", side_effect=chunks if isinstance(chunks, Exception) else None, return_value=chunks), patch.object(p, "run_aggregate", side_effect=AssertionError("must defer")):
                    self.assertFalse(recovery.retry_one(root, CFG, NOW))
                self.assertTrue((root / "research/pending-periods/weekly/2026-09-04.json").exists())

    def test_marker_rejects_boolean_schema_and_duplicate_keys(self):
        for raw in ('{"schemaVersion":true,"reportKind":"weekly","periodEnd":"2026-09-04"}', '{"schemaVersion":1,"schemaVersion":1,"reportKind":"weekly","periodEnd":"2026-09-04"}'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = marker(root, "weekly", "2026-09-04")
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(p.StateError):
                    recovery.pending_periods(root, CFG, NOW.date())

    def test_ready_period_not_starved_by_old_missing_period_and_only_one_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker(root, "weekly", "2026-08-28")
            marker(root, "monthly", "2026-08-31")
            ready_week(root)
            with patch.object(p, "ResponsesAnalyzer", side_effect=AssertionError("no papers")):
                self.assertFalse(recovery.retry_one(root, CFG, NOW))
            self.assertFalse((root / "research/pending-periods/weekly/2026-09-04.json").exists())
            self.assertTrue((root / "research/pending-periods/weekly/2026-08-28.json").exists())
            self.assertFalse(recovery.retry_one(root, CFG, NOW))

    def test_failure_retains_marker_and_returns_deferred_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_week(root)
            with patch.object(p, "run_aggregate", side_effect=p.StructuredOutputError("bad output")) as run:
                self.assertTrue(recovery.retry_one(root, CFG, NOW))
            self.assertEqual(run.call_count, 1)
            self.assertTrue((root / "research/pending-periods/weekly/2026-09-04.json").exists())

    def test_status_does_not_publish_drafts_or_provider_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_week(root)
            state = p._default_state()
            state.update(lastStatus=p.UPDATE_NOT_CONFIRMED, pendingBatchDate="2026-09-07", lastAttemptedAt="2026-09-08T12:00:00Z")
            p.save_state(root / "research/state.json", state)
            value = recovery.write_status(root, CFG)
            self.assertEqual(value["daily"]["status"], p.UPDATE_NOT_CONFIRMED)
            self.assertTrue(value["pendingPeriods"][0]["ready"])
            self.assertNotIn("papers", json.dumps(value))


class LanguageRepairTests(unittest.TestCase):
    def test_bad_identity_is_rejected_before_any_paid_repair(self):
        source = [paper(1, importance=4)]
        bad = analysis(importance=4)
        bad["summary"] = "Not Japanese prose."
        responses = RecordingResponses([{"papers": [
            {"arxivId": source[0]["metadata"]["arxivId"], "finalAnalysis": bad},
            {"arxivId": "2608.99999v1", "finalAnalysis": analysis(importance=4)},
        ]}])
        analyzer = p.ResponsesAnalyzer(CFG, SimpleNamespace(responses=responses))
        with self.assertRaises(p.StructuredOutputError):
            analyzer.synthesize(source, "weekly", date(2026, 8, 29), date(2026, 9, 4))
        self.assertEqual(len(responses.calls), 1)

    def test_only_bad_draft_is_repaired_and_valid_item_preserved(self):
        source = [paper(1, importance=4), paper(2, importance=4)]
        bad = analysis(importance=4)
        bad["summary"] = "This draft has an English summary in the wrong field."
        original = {"papers": [{"arxivId": source[0]["metadata"]["arxivId"], "finalAnalysis": source[0]["finalAnalysis"]}, {"arxivId": source[1]["metadata"]["arxivId"], "finalAnalysis": bad}]}
        responses = RecordingResponses([original, analysis(importance=4)])
        analyzer = p.ResponsesAnalyzer(CFG, SimpleNamespace(responses=responses))
        result = analyzer.synthesize(source, "weekly", date(2026, 8, 29), date(2026, 9, 4))
        self.assertEqual(len(responses.calls), 2)
        self.assertEqual(result[0]["finalAnalysis"], source[0]["finalAnalysis"])
        self.assertEqual(analyzer.usage_calls[-1]["paperIds"], [source[1]["metadata"]["arxivId"]])

    def test_exhausted_repair_does_not_restart_chunk(self):
        source = [paper(1, importance=4)]
        bad = analysis(importance=4)
        bad["summary"] = "This is not a Japanese sentence."
        responses = RecordingResponses([{"papers": [{"arxivId": source[0]["metadata"]["arxivId"], "finalAnalysis": bad}]}, bad])
        analyzer = p.ResponsesAnalyzer(CFG, SimpleNamespace(responses=responses))
        with self.assertRaises(p.SynthesisRepairExhausted):
            p._retry(lambda: analyzer.synthesize(source, "weekly", date(2026, 8, 29), date(2026, 9, 4)), 3, lambda _: None)
        self.assertEqual(len(responses.calls), 2)


class PublicationCorrectionTests(unittest.TestCase):
    def test_two_pass_publication_refreshes_corrected_empty_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history, english = write_base_bundle(root)
            report = completed_report()
            report.update(expectedBatchDate=report["reportDate"], observedBatchDate=report["reportDate"])
            empty = copy.deepcopy(report)
            empty.update(status=p.NO_NEW_BATCH_EXPECTED, papers=[], expectedBatchDate="2026-08-28", observedBatchDate="2026-08-28")
            archive, latest, reports = root / "archive", root / "latest.json", root / "daily"
            publication.publish_research_report(empty, history, english, regenerate_site=True, latest_path=latest, archive_dir=archive)
            p.persist_report(report, reports)
            publication.reconcile_daily_reports(reports, history, english)
            publication.reconcile_daily_reports(reports, history, english, regenerate_site=True, latest_path=latest, archive_dir=archive)
            loaded = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(loaded["status"], p.UPDATE_CONFIRMED)
            self.assertEqual(len(loaded["papers"]), 1)
            original = latest.read_bytes()
            publication.reconcile_daily_reports(reports, history, english, regenerate_site=True, latest_path=latest, archive_dir=archive)
            self.assertEqual(original, latest.read_bytes())

    def test_narrow_empty_correction_rule_preserves_real_editions(self):
        report = completed_report()
        report.update(expectedBatchDate=report["reportDate"], observedBatchDate=report["reportDate"])
        incoming = publication.adapt_research_report(report).source_edition
        old = copy.deepcopy(incoming)
        old.update(status=p.NO_NEW_BATCH_EXPECTED, papers=[], expectedBatchDate="2026-08-28", observedBatchDate="2026-08-28")
        self.assertTrue(publication._is_calendar_placeholder_correction(old, incoming))
        for change in [{"status": p.NO_RELEVANT_PAPERS}, {"papers": incoming["papers"]}, {"sourceKind": "chatgpt-scheduler"}, {"expectedBatchDate": incoming["editionDate"]}]:
            self.assertFalse(publication._is_calendar_placeholder_correction({**old, **change}, incoming))


if __name__ == "__main__":
    unittest.main()
