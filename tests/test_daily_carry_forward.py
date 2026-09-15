import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_pipeline as p
import research_recovery as r
from test_research_pipeline import config, entry, analysis, listing_html, pastweek_html, FakeAnalyzer
from test_research_recovery import ready_week

NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
CFG = config()


def pending(root, day, *, when=None):
    return p.persist_report(p._report(report_kind="daily", report_date=day,
        generated_at=when or NOW - timedelta(days=1), status=p.UPDATER_OFFLINE,
        message="API temporarily unavailable; pending retry.", expected_batch_date=day,
        observed_batch_date=day, period_start=None, period_end=None, papers=[]), root / "research/daily")


def snapshot(root, day, ids=()):
    p.save_listing_snapshot(root / "research/listings", [p.ListingPage("q-fin.TR", day,
        tuple(p.ListingItem(key, "new") for key in ids))], NOW)


class CarryForwardTests(unittest.TestCase):
    def root(self):
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def runner(self, analyzer=None, metadata=None, calls=None):
        def run(cfg, **kwargs):
            kwargs.pop("published_history", None)  # The fixture has no public archive.
            if calls is not None:
                calls.append(kwargs.get("target_batch"))
            label = p.expected_batch_date(kwargs["checked_at"], cfg.no_announcement_dates).strftime("%A, %d %B %Y")
            kwargs.setdefault("list_fetcher", lambda _: listing_html(label))
            return p.run_daily(cfg, **kwargs, analyzer=analyzer or FakeAnalyzer(),
                metadata_fetcher=metadata or (lambda ids: {key: entry(key) for key in ids}), sleep_fn=lambda _: None)
        return run

    def test_latest_and_only_one_old_batch_then_no_completed_reanalysis(self):
        root = self.root()
        for day, key in ((date(2026, 9, 10), "2609.10001"), (date(2026, 9, 14), "2609.10002")):
            pending(root, day)
            snapshot(root, day, (key,))
        analyzer = FakeAnalyzer()
        calls = []
        result = r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner(analyzer, calls=calls), capture=lambda *_: None)
        self.assertFalse(result["failed"])
        self.assertEqual(calls, [None, date(2026, 9, 10)])
        self.assertEqual(result["pending"], [date(2026, 9, 14)])
        self.assertEqual(analyzer.screened, ["2609.10001"])
        self.assertEqual(p.load_state(root / "research/state.json")["lastCompletedBatchDate"], "2026-09-15")
        completed = (root / "research/daily/2026-09-10.json").read_bytes()
        r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner(analyzer), capture=lambda *_: None)
        self.assertEqual(analyzer.screened, ["2609.10001", "2609.10002"])
        self.assertEqual((root / "research/daily/2026-09-10.json").read_bytes(), completed)

    def test_api_outage_carries_latest_and_does_not_hit_old_api(self):
        root = self.root()
        pending(root, date(2026, 9, 10))
        snapshot(root, date(2026, 9, 10), ("2609.10001",))
        calls = []
        def run(cfg, **kwargs):
            kwargs.pop("published_history")
            calls.append(kwargs.get("target_batch"))
            return p.run_daily(cfg, **kwargs,
                list_fetcher=lambda _: listing_html("Tuesday, 15 September 2026", new=("2609.10002",)),
                metadata_fetcher=mock.Mock(side_effect=urllib.error.HTTPError("private", 429, "private", {}, None)))
        result = r.run_daily_cycle(root, CFG, NOW, daily_runner=run, capture=lambda *_: None)
        self.assertFalse(result["failed"])
        self.assertEqual(calls, [None])
        self.assertEqual(result["pending"], [date(2026, 9, 10), date(2026, 9, 15)])
        self.assertEqual(result["latest"]["papers"], [])
        self.assertEqual(len(p.load_listing_snapshot(root / "research/listings/2026-09-15.json", CFG.categories)[0].items), 1)

    def test_old_api_outage_keeps_cursor_current_and_next_day_resumes_pdf_only(self):
        root = self.root()
        day = date(2026, 9, 14)
        pending(root, day)
        snapshot(root, day, ("2609.10001",))
        first = FakeAnalyzer()
        first.analyze_pdf = mock.Mock(side_effect=p.UpdaterOfflineError("private"))
        result = r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner(first), capture=lambda *_: None)
        self.assertFalse(result["failed"])
        self.assertEqual(first.screened, ["2609.10001"])
        self.assertEqual(result["pending"], [day])
        self.assertEqual(p.load_state(root / "research/state.json")["lastCompletedBatchDate"], "2026-09-15")
        calls = []
        r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner(calls=calls), capture=lambda *_: None)
        self.assertEqual(calls, [None])  # No second attempt that same JST day.
        second = FakeAnalyzer()
        result = r.run_daily_cycle(root, CFG, NOW + timedelta(days=1),
            daily_runner=self.runner(second), capture=lambda *_: None)
        self.assertFalse(result["failed"])
        self.assertEqual(second.screened, [])
        self.assertEqual(result["carried"][0]["status"], p.UPDATE_CONFIRMED)
        self.assertEqual(result["pending"], [])
        self.assertEqual(second.screened, [])
        self.assertEqual(second.pdfs, ["2609.10001"])

    def test_listing_snapshot_recovers_after_pastweek_expiry(self):
        root = self.root()
        day = date(2026, 9, 10)
        pending(root, day)
        snapshot(root, day, ("2609.10001",))
        result = p.run_daily(CFG, state_path=root / "research/state.json", output_dir=root / "research/daily",
            checked_at=NOW + timedelta(days=15), recover_pending=True, target_batch=day,
            list_fetcher=mock.Mock(side_effect=AssertionError("must use snapshot")),
            history_fetcher=mock.Mock(side_effect=AssertionError("must use snapshot")),
            metadata_fetcher=lambda ids: {key: entry(key) for key in ids}, analyzer=FakeAnalyzer())
        self.assertEqual(result["status"], p.UPDATE_CONFIRMED)

    def test_capture_requires_explicit_presence_in_every_category(self):
        root = self.root()
        day = date(2026, 9, 10)
        pending(root, day)
        cfg = config(categories=("q-fin.TR", "q-fin.MF"))
        def fetch(category):
            if category == "q-fin.MF":
                return pastweek_html(("Monday, 14 September 2026", ()))
            return pastweek_html(("Thursday, 10 September 2026", ("2609.10001",)))
        r.capture_pending_listings(root, cfg, NOW, history_fetcher=fetch, sleep_fn=lambda _: None)
        self.assertFalse((root / "research/listings/2026-09-10.json").exists())
        r.capture_pending_listings(root, cfg, NOW,
            history_fetcher=lambda _: pastweek_html(("Thursday, 10 September 2026", ())), sleep_fn=lambda _: None)
        self.assertEqual(len(p.load_listing_snapshot(root / "research/listings/2026-09-10.json", cfg.categories)), 2)

    def test_capture_api_failure_becomes_pending_not_false_completion(self):
        root = self.root()
        result = r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner(),
            capture=mock.Mock(side_effect=urllib.error.URLError("private")))
        self.assertFalse(result["failed"])
        self.assertEqual(result["pending"], [date(2026, 9, 15)])
        self.assertNotIn("private", result["latest"]["message"])

    def test_request_pacing_is_kept_between_capture_latest_and_recovery(self):
        root = self.root()
        pending(root, date(2026, 9, 14))
        snapshot(root, date(2026, 9, 14))
        sleep = mock.Mock()
        result = r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner(),
            capture=lambda *_: True, sleep_fn=sleep)
        self.assertFalse(result["failed"])
        self.assertEqual(sleep.call_args_list, [mock.call(p.ARXIV_REQUEST_INTERVAL_SECONDS)] * 2)

    def test_corrupt_snapshot_and_configuration_errors_are_not_deferred(self):
        root = self.root()
        day = date(2026, 9, 10)
        snapshot(root, day)
        path = root / "research/listings/2026-09-10.json"
        data = json.loads(path.read_text())
        data["pages"][0]["category"] = "private"
        path.write_text(json.dumps(data))
        with self.assertRaises(p.StateError):
            r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner())
        self.assertFalse(p.is_deferred_report({"status": p.UPDATE_NOT_CONFIRMED, "reportKind": "daily",
            "message": "Diagnostic: error=RemoteConfigurationError.", "observedBatchDate": "2026-09-15", "expectedBatchDate": "2026-09-15"}))

    def test_source_capture_is_byte_stable_and_rejects_changed_category_config(self):
        root = self.root()
        day = date(2026, 9, 10)
        snapshot(root, day, ("2609.10001",))
        path = root / "research/listings/2026-09-10.json"
        before = path.read_bytes()
        snapshot(root, day, ("2609.10001",))
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(p.StateError):
            p.load_listing_snapshot(path, ("q-fin.TR", "q-fin.MF"))
        snapshot(root, day, ("2609.10002",))
        self.assertEqual({item.arxiv_id for item in p.load_listing_snapshot(path, CFG.categories)[0].items}, {"2609.10001", "2609.10002"})

    def test_old_unavailable_source_does_not_starve_recent_backlog(self):
        root = self.root()
        pending(root, date(2026, 8, 31))
        pending(root, date(2026, 9, 14))
        snapshot(root, date(2026, 9, 14))
        result = r.run_daily_cycle(root, CFG, NOW, daily_runner=self.runner(), capture=lambda *_: None)
        self.assertEqual(result["carried"][0]["reportDate"], "2026-09-14")
        self.assertEqual(result["pending"], [date(2026, 8, 31)])

    def test_period_api_outage_keeps_marker_without_failing_daily_job(self):
        root = self.root()
        ready_week(root)
        with mock.patch.object(p, "run_aggregate", side_effect=p.UpdaterOfflineError("private")):
            self.assertFalse(r.retry_one(root, CFG, NOW))
        self.assertTrue((root / "research/pending-periods/weekly/2026-09-04.json").exists())

    def test_openai_auth_and_billing_errors_require_attention_but_429_is_deferred(self):
        for status, code, expected in ((401, None, p.RemoteConfigurationError),
                                       (429, "credit_balance_exhausted", p.RemoteConfigurationError),
                                       (429, "rate_limit_exceeded", p.UpdaterOfflineError),
                                       (503, None, p.UpdaterOfflineError)):
            error = RuntimeError("private")
            error.status_code = status
            error.body = {"error": {"code": code}}
            client = SimpleNamespace(responses=SimpleNamespace(create=mock.Mock(side_effect=error)))
            adapter = p.ResponsesAnalyzer(CFG, client)
            with self.assertRaises(expected):
                adapter.analyze_abstract(p.PaperCandidate(entry("2609.10001"), ("new",), ("q-fin.TR",)))

    def test_period_api_failure_returns_a_durable_pending_report(self):
        from test_research_pipeline_chunking import paper
        root = self.root()
        for offset in range(5):
            day = date(2026, 8, 31) + timedelta(days=offset)
            p.persist_report(p._report(report_kind="daily", report_date=day, generated_at=NOW,
                status=p.UPDATE_CONFIRMED if offset == 4 else p.NO_RELEVANT_PAPERS,
                message="Confirmed batch.", expected_batch_date=day, observed_batch_date=day,
                period_start=None, period_end=None, papers=[paper(1, importance=4)] if offset == 4 else []), root / "research/daily")
        analyzer = FakeAnalyzer()
        analyzer.synthesize = mock.Mock(side_effect=p.UpdaterOfflineError("private"))
        usage = p.research_usage.record(None, model=CFG.weekly_model, effort="medium", stage="weekly",
            paper_ids=[], scope="stored_reviews", pages=None)
        analyzer.usage_calls = [usage]
        result = p.run_aggregate(CFG, report_kind="weekly", period_start=date(2026, 8, 29),
            period_end=date(2026, 9, 4), daily_dir=root / "research/daily",
            output_dir=root / "research/reviews/weekly", generated_at=NOW, analyzer=analyzer)
        self.assertEqual(result["status"], p.UPDATER_OFFLINE)
        self.assertTrue(p.is_deferred_report(result))
        self.assertNotIn("private", result["message"])
        self.assertEqual(p._read_persisted_report(root / "research/reviews/weekly/2026-09-04.json"), result)
        retried = p.run_aggregate(CFG, report_kind="weekly", period_start=date(2026, 8, 29),
            period_end=date(2026, 9, 4), daily_dir=root / "research/daily",
            output_dir=root / "research/reviews/weekly", generated_at=NOW + timedelta(days=1), analyzer=analyzer)
        self.assertEqual(retried["usage"], [usage, usage])
