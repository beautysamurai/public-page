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

    def test_metadata_406_falls_back_to_official_oai_records(self):
        requested = ["2609.20224", "2609.20405"]
        calls = []

        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def read(self, size=-1):
                return self.body[:size] if size >= 0 else self.body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def oai_xml(arxiv_id):
            return f"""<?xml version="1.0" encoding="UTF-8"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <GetRecord>
                <record>
                  <header>
                    <identifier>oai:arXiv.org:{arxiv_id}</identifier>
                    <datestamp>2026-09-18</datestamp>
                  </header>
                  <metadata>
                    <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
                      <id>{arxiv_id}</id>
                      <created>2026-09-18</created>
                      <updated>2026-09-19</updated>
                      <authors>
                        <author><forenames>Researcher</forenames><keyname>One</keyname></author>
                      </authors>
                      <title>Recovered metadata {arxiv_id}</title>
                      <categories>q-fin.TR cs.LG</categories>
                      <abstract>Market microstructure and rates research.</abstract>
                    </arXiv>
                  </metadata>
                </record>
              </GetRecord>
            </OAI-PMH>
            """.encode("utf-8")

        def raw_xml(arxiv_id, versions):
            version_xml = "".join(
                f'<version version="{version}"><date>Fri, 18 Sep 2026 12:00:00 GMT</date><size>100kb</size></version>'
                for version in versions
            )
            return f"""<?xml version="1.0" encoding="UTF-8"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <GetRecord>
                <record>
                  <header>
                    <identifier>oai:arXiv.org:{arxiv_id}</identifier>
                    <datestamp>2026-09-18</datestamp>
                  </header>
                  <metadata>
                    <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
                      <id>{arxiv_id}</id>
                      {version_xml}
                    </arXivRaw>
                  </metadata>
                </record>
              </GetRecord>
            </OAI-PMH>
            """.encode("utf-8")

        versions = {
            "2609.20224": ("v1", "v2"),
            "2609.20405": ("v1",),
        }

        def opener(request, timeout=0):
            calls.append((request.full_url, timeout))
            parsed = urllib.parse.urlparse(request.full_url)
            if parsed.hostname == "export.arxiv.org":
                raise http_error(406)
            self.assertEqual(parsed.hostname, "oaipmh.arxiv.org")
            query = urllib.parse.parse_qs(parsed.query)
            identifier = query["identifier"][0]
            arxiv_id = identifier.removeprefix("oai:arXiv.org:")
            if query["metadataPrefix"] == ["arXivRaw"]:
                return FakeResponse(raw_xml(arxiv_id, versions[arxiv_id]))
            self.assertEqual(query["metadataPrefix"], ["arXiv"])
            return FakeResponse(oai_xml(arxiv_id))

        fetched = p.fetch_metadata(requested, timeout=7.0, opener=opener)

        self.assertEqual(set(fetched), set(requested))
        self.assertEqual(len(calls), 1 + 2 * len(requested))
        self.assertEqual(fetched["2609.20224"].arxiv_id, "2609.20224v2")
        self.assertEqual(fetched["2609.20405"].arxiv_id, "2609.20405v1")
        self.assertEqual(
            fetched["2609.20224"].authors,
            ("Researcher One",),
        )
        self.assertEqual(
            fetched["2609.20224"].categories,
            ("cs.LG", "q-fin.TR"),
        )
        self.assertEqual(
            fetched["2609.20224"].submitted_at.date().isoformat(),
            "2026-09-18",
        )
        self.assertEqual(
            fetched["2609.20224"].updated_at.date().isoformat(),
            "2026-09-19",
        )

    def test_oai_retry_is_scoped_to_the_failed_getrecord(self):
        requested = ["2609.20224", "2609.20405"]
        calls = []
        sleeps = []
        attempts = {}

        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def read(self, size=-1):
                return self.body[:size] if size >= 0 else self.body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def oai_xml(arxiv_id):
            return f"""<?xml version="1.0" encoding="UTF-8"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <GetRecord><record><metadata>
                <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
                  <id>{arxiv_id}</id>
                  <created>2026-09-18</created>
                  <authors><author><keyname>Researcher</keyname></author></authors>
                  <title>Recovered {arxiv_id}</title>
                  <categories>q-fin.TR</categories>
                  <abstract>Rates research.</abstract>
                </arXiv>
              </metadata></record></GetRecord>
            </OAI-PMH>""".encode("utf-8")

        def raw_xml(arxiv_id):
            return f"""<?xml version="1.0" encoding="UTF-8"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <GetRecord><record><metadata>
                <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
                  <id>{arxiv_id}</id>
                  <version version="v1" />
                </arXivRaw>
              </metadata></record></GetRecord>
            </OAI-PMH>""".encode("utf-8")

        def opener(request, timeout=0):
            parsed = urllib.parse.urlparse(request.full_url)
            if parsed.hostname == "export.arxiv.org":
                calls.append(("search", "all"))
                raise http_error(406)
            query = urllib.parse.parse_qs(parsed.query)
            arxiv_id = query["identifier"][0].removeprefix("oai:arXiv.org:")
            prefix = query["metadataPrefix"][0]
            key = (arxiv_id, prefix)
            attempts[key] = attempts.get(key, 0) + 1
            calls.append(key)
            if key == ("2609.20405", "arXivRaw") and attempts[key] == 1:
                raise http_error(429)
            return FakeResponse(raw_xml(arxiv_id) if prefix == "arXivRaw" else oai_xml(arxiv_id))

        fetched = p.fetch_metadata(
            requested,
            timeout=7.0,
            opener=opener,
            retries=1,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(set(fetched), set(requested))
        self.assertEqual(sleeps, [30])
        self.assertEqual(attempts[("2609.20224", "arXivRaw")], 1)
        self.assertEqual(attempts[("2609.20224", "arXiv")], 1)
        self.assertEqual(attempts[("2609.20405", "arXivRaw")], 2)
        self.assertEqual(attempts[("2609.20405", "arXiv")], 1)

    def test_default_metadata_retry_is_not_wrapped_around_the_whole_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(p, "fetch_metadata", side_effect=http_error()) as fetch:
                report = p.run_daily(
                    config(retries=3),
                    state_path=root / "state.json",
                    output_dir=root / "daily",
                    checked_at=CHECKED_AT,
                    list_fetcher=lambda _: listing_html(new=("2608.10001",)),
                    sleep_fn=lambda _delay: None,
                )

        self.assertEqual(report["status"], p.UPDATER_OFFLINE)
        fetch.assert_called_once()

    def test_oai_retry_resumes_at_failed_record_without_replaying_completed_ids(self):
        requested = ["2609.20224", "2609.20405"]
        calls = []
        sleeps = []
        failed_once = {"2609.20405": False}

        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def read(self, size=-1):
                return self.body[:size] if size >= 0 else self.body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def raw_xml(arxiv_id):
            return f"""<?xml version="1.0" encoding="UTF-8"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <GetRecord><record><metadata>
                <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
                  <id>{arxiv_id}</id>
                  <version version="v1" />
                </arXivRaw>
              </metadata></record></GetRecord>
            </OAI-PMH>""".encode("utf-8")

        def oai_xml(arxiv_id):
            return f"""<?xml version="1.0" encoding="UTF-8"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <GetRecord><record><metadata>
                <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
                  <id>{arxiv_id}</id>
                  <created>2026-09-18</created>
                  <authors>
                    <author><forenames>Researcher</forenames><keyname>One</keyname></author>
                  </authors>
                  <title>Recovered metadata {arxiv_id}</title>
                  <categories>q-fin.TR</categories>
                  <abstract>Market microstructure research.</abstract>
                </arXiv>
              </metadata></record></GetRecord>
            </OAI-PMH>""".encode("utf-8")

        def opener(request, timeout=0):
            parsed = urllib.parse.urlparse(request.full_url)
            calls.append(request.full_url)
            if parsed.hostname == "export.arxiv.org":
                raise http_error(406)
            query = urllib.parse.parse_qs(parsed.query)
            arxiv_id = query["identifier"][0].removeprefix("oai:arXiv.org:")
            prefix = query["metadataPrefix"][0]
            if (
                arxiv_id == "2609.20405"
                and prefix == "arXivRaw"
                and not failed_once[arxiv_id]
            ):
                failed_once[arxiv_id] = True
                raise http_error(503)
            return FakeResponse(
                raw_xml(arxiv_id) if prefix == "arXivRaw" else oai_xml(arxiv_id)
            )

        fetched = p.fetch_metadata(
            requested,
            timeout=7.0,
            opener=opener,
            retries=1,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(set(fetched), set(requested))
        first_raw = [
            url for url in calls
            if "identifier=oai%3AarXiv.org%3A2609.20224" in url
            and "metadataPrefix=arXivRaw" in url
        ]
        first_metadata = [
            url for url in calls
            if "identifier=oai%3AarXiv.org%3A2609.20224" in url
            and "metadataPrefix=arXiv&" in (url + "&")
        ]
        second_raw = [
            url for url in calls
            if "identifier=oai%3AarXiv.org%3A2609.20405" in url
            and "metadataPrefix=arXivRaw" in url
        ]
        self.assertEqual(len(first_raw), 1)
        self.assertEqual(len(first_metadata), 1)
        self.assertEqual(len(second_raw), 2)
        self.assertEqual(sleeps, [30])

    def test_metadata_non_406_http_error_does_not_switch_interface(self):
        calls = []

        def opener(request, timeout=0):
            calls.append(request.full_url)
            raise http_error(403)

        with self.assertRaises(urllib.error.HTTPError):
            p.fetch_metadata(["2609.20224"], timeout=7.0, opener=opener)

        self.assertEqual(len(calls), 1)
        self.assertIn("export.arxiv.org/api/query", calls[0])

    def test_oai_record_must_match_the_requested_id(self):
        wrong = b"""<?xml version="1.0" encoding="UTF-8"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <GetRecord><record><metadata>
            <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
              <id>2609.99999</id>
              <created>2026-09-18</created>
              <authors><author><keyname>Researcher</keyname></author></authors>
              <title>Wrong record</title>
              <categories>q-fin.TR</categories>
              <abstract>Wrong paper.</abstract>
            </arXiv>
          </metadata></record></GetRecord>
        </OAI-PMH>"""

        with self.assertRaisesRegex(p.ListingParseError, "does not match"):
            p._parse_oai_record(wrong, "2609.20224", "v1")

    def test_oai_version_history_must_be_contiguous_and_match_requested_id(self):
        wrong_id = b"""<?xml version="1.0" encoding="UTF-8"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <GetRecord><record><metadata>
            <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
              <id>2609.99999</id>
              <version version="v1" />
            </arXivRaw>
          </metadata></record></GetRecord>
        </OAI-PMH>"""
        with self.assertRaisesRegex(p.ListingParseError, "does not match"):
            p._parse_oai_latest_version(wrong_id, "2609.20224")

        gap = b"""<?xml version="1.0" encoding="UTF-8"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <GetRecord><record><metadata>
            <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
              <id>2609.20224</id>
              <version version="v1" />
              <version version="v3" />
            </arXivRaw>
          </metadata></record></GetRecord>
        </OAI-PMH>"""
        with self.assertRaisesRegex(p.ListingParseError, "non-contiguous"):
            p._parse_oai_latest_version(gap, "2609.20224")

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
