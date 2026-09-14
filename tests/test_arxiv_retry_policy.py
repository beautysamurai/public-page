import contextlib
import io
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_pipeline as p
from test_research_pipeline import CHECKED_AT, config, entry, listing_html


def http_error(code=429, retry_after=None):
    return urllib.error.HTTPError("https://export.arxiv.org/api/query?private", code,
                                  "private response", {"Retry-After": retry_after} if retry_after else {}, None)


class ArxivRetryPolicyTests(unittest.TestCase):
    def test_rate_limit_uses_exponential_backoff_then_succeeds(self):
        operation = mock.Mock(side_effect=[http_error(), http_error(), http_error(), "ok"])
        sleeps = []
        self.assertEqual(p._retry(operation, 3, sleeps.append), "ok")
        self.assertEqual(sleeps, [30, 60, 120])

    def test_retry_after_seconds_or_http_date_is_not_shortened(self):
        now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        for value in ("180", "Mon, 14 Sep 2026 12:03:00 GMT"):
            with self.subTest(value=value):
                operation = mock.Mock(side_effect=[http_error(503, value), "ok"])
                sleeps = []
                self.assertEqual(p._retry(operation, 1, sleeps.append, utc_now_fn=lambda: now), "ok")
                self.assertEqual(sleeps, [180])

    def test_invalid_header_falls_back_safely(self):
        for value in ("invalid", "-20", "NaN", "Infinity"):
            sleeps = []
            operation = mock.Mock(side_effect=[http_error(429, value), "ok"])
            p._retry(operation, 1, sleeps.append)
            self.assertEqual(sleeps, [30])

    def test_permanent_http_errors_are_not_retried(self):
        for code in (400, 401, 403, 404, 422):
            operation = mock.Mock(side_effect=http_error(code))
            sleep = mock.Mock()
            with self.assertRaises(urllib.error.HTTPError):
                p._retry(operation, 3, sleep)
            self.assertEqual(operation.call_count, 1)
            sleep.assert_not_called()

    def test_cooldown_beyond_deadline_keeps_pending_without_early_retry(self):
        for header, deadline in (("180", 100), ("999999999999999", None)):
            operation = mock.Mock(side_effect=http_error(429, header))
            sleep = mock.Mock()
            with self.assertRaises(p.WorkBudgetExceeded):
                p._retry(operation, 3, sleep, deadline=deadline, monotonic_fn=lambda: 0)
            self.assertEqual(operation.call_count, 1)
            sleep.assert_not_called()

    def test_transport_failures_use_at_least_three_seconds(self):
        operation = mock.Mock(side_effect=[urllib.error.URLError("private"), "ok"])
        sleeps = []
        p._retry(operation, 1, sleeps.append)
        self.assertEqual(sleeps, [3])

    def test_pacer_spaces_requests_and_counts_failed_attempts(self):
        clock = [0.0]
        sleeps = []
        def sleep(delay):
            sleeps.append(delay)
            clock[0] += delay
        pacer = p.ArxivRequestPacer(sleep_fn=sleep, monotonic_fn=lambda: clock[0])
        with mock.patch.object(p.urllib.request, "urlopen", side_effect=[http_error(), "ok", "ok"]):
            with self.assertRaises(urllib.error.HTTPError):
                pacer.open("fixture", timeout=1)
            pacer.open("fixture", timeout=1)
            clock[0] += 1
            pacer.open("fixture", timeout=1)
        self.assertEqual(sleeps, [3, 2])

    def test_pacer_respects_deadline(self):
        pacer = p.ArxivRequestPacer(monotonic_fn=lambda: 0, deadline=2)
        with mock.patch.object(p.urllib.request, "urlopen", return_value="ok") as opener:
            pacer.open("fixture", timeout=1)
            with self.assertRaises(p.WorkBudgetExceeded):
                pacer.open("fixture", timeout=1)
            self.assertEqual(opener.call_count, 1)

    def test_pdf_rate_limit_or_denial_never_switches_host(self):
        for code, header in ((429, None), (400, None), (401, None), (403, None),
                             (422, None), (451, None), (503, "120")):
            with mock.patch.object(p.urllib.request, "urlopen", side_effect=http_error(code, header)) as opener:
                with self.assertRaises(urllib.error.HTTPError):
                    p.fetch_pdf_for_inline_input("2609.10001v1", timeout=1)
                self.assertEqual(opener.call_count, 1)

    def test_metadata_failure_retains_safe_diagnostic_and_pending_date(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as log:
            root = Path(directory)
            report = p.run_daily(config(), state_path=root / "state.json", output_dir=root / "daily",
                                 checked_at=CHECKED_AT,
                                 list_fetcher=lambda _: listing_html(new=("2608.10001",)),
                                 metadata_fetcher=mock.Mock(side_effect=http_error()))
            self.assertEqual(report["status"], p.UPDATER_OFFLINE)
            self.assertIn("stage=metadata", report["message"])
            self.assertIn("httpStatus=429", report["message"])
            self.assertNotIn("private", report["message"] + log.getvalue())
            self.assertEqual(p.load_state(root / "state.json")["pendingBatchDate"], "2026-08-28")
            self.assertFalse((root / "checkpoints/2026-08-28.json").exists())

    def test_candidate_validation_is_distinguished_from_model_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = p.run_daily(config(), state_path=root / "state.json", output_dir=root / "daily",
                                 checked_at=CHECKED_AT,
                                 list_fetcher=lambda _: listing_html(new=("2608.10001",)),
                                 metadata_fetcher=lambda _: {"2608.10001": entry("2608.10001", title=" invalid ")})
            self.assertEqual(report["status"], p.UPDATE_NOT_CONFIRMED)
            self.assertIn("stage=candidate_validation", report["message"])
            self.assertNotIn("abstract_analysis", report["message"])

    def test_pending_daily_refreshes_diagnostics_but_not_completed_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = p.run_daily(config(), state_path=root / "state.json", output_dir=root / "daily",
                              checked_at=CHECKED_AT, list_fetcher=mock.Mock(side_effect=http_error()))
            new = p.run_daily(config(), state_path=root / "state.json", output_dir=root / "daily",
                              checked_at=CHECKED_AT + timedelta(minutes=10),
                              list_fetcher=lambda _: listing_html(new=("2608.10001",)),
                              metadata_fetcher=mock.Mock(side_effect=TimeoutError("private")))
            self.assertNotEqual(new["generatedAt"], old["generatedAt"])
            self.assertIn("stage=metadata; error=TimeoutError", new["message"])
            self.assertEqual(new["papers"], old["papers"])
            self.assertEqual(new.get("usage"), old.get("usage"))
            self.assertEqual(p.persist_report(old, root / "daily"), new)
            complete = p.persist_report({**new, "status": p.NO_RELEVANT_PAPERS,
                                         "observedBatchDate": new["expectedBatchDate"]}, root / "daily")
            self.assertEqual(p.persist_report({**new, "generatedAt": "2026-08-28T14:00:00Z"}, root / "daily"), complete)
