import base64
import contextlib
import io
import unittest
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import research_pipeline as p
from tests.test_research_pipeline import config, entry, analysis, RecordingResponses, CHECKED_AT, listing_html


class PdfInputRecoveryTests(unittest.TestCase):
    def test_withdrawal_requires_official_notice_and_is_not_retried(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://arxiv.org/abs/2608.12345v1"
        for body, expected in [(b'<span class="error" style="border: 2px solid grey">This paper has been withdrawn by Author</span>', True), (b'<blockquote>This paper has been withdrawn by Author</blockquote>', False), (b'Not found', False)]:
            response.read.return_value = body
            with mock.patch.object(p.urllib.request, "urlopen", return_value=response):
                self.assertEqual(p._is_withdrawn("2608.12345v1", timeout=2), expected)
        error = p.urllib.error.HTTPError("url", 404, "missing", {}, None)
        for withdrawn in (True, False):
            with mock.patch.object(p.urllib.request, "urlopen", side_effect=error), mock.patch.object(p, "_is_withdrawn", return_value=withdrawn):
                with self.assertRaises(p.PaperWithdrawn if withdrawn else p.urllib.error.HTTPError):
                    p.fetch_pdf_for_inline_input("2608.12345v1", timeout=2)
        operation = mock.Mock(side_effect=p.PaperWithdrawn("2608.12345v1"))
        with self.assertRaises(p.PaperWithdrawn):
            p._retry(operation, 3, lambda _: None)
        operation.assert_called_once()

    def test_withdrawn_candidate_is_checkpointed_and_other_papers_continue(self):
        analyzer = SimpleNamespace(
            analyze_abstract=mock.Mock(return_value=analysis(importance=4)),
            analyze_pdf=mock.Mock(side_effect=[p.PaperWithdrawn("2608.12345v1"), analysis(importance=4)]),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = p.run_daily(config(), state_path=root / "state.json", output_dir=root / "daily",
                checked_at=CHECKED_AT, list_fetcher=lambda _: listing_html(new=("2608.12345", "2608.12346")),
                metadata_fetcher=lambda ids: {key: entry(key) for key in ids}, analyzer=analyzer)
            self.assertEqual(report["status"], p.UPDATE_CONFIRMED)
            self.assertEqual(len(report["papers"]), 1)
            self.assertIn("withdrawn", report["message"])
            checkpoint = json.loads((root / "checkpoints/2026-08-28.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["results"]["2608.12345"]["status"], "withdrawn")

    def candidate(self):
        return p.PaperCandidate(entry("2608.12345"), ("new",), ("q-fin.TR",))

    def error(self, status, message):
        error = RuntimeError("private diagnostic sentinel")
        error.status_code = status
        error.body = {"error": {"message": message}}
        return error

    def test_rejected_file_url_uses_same_pdf_inline_without_changing_analysis(self):
        responses = RecordingResponses([analysis(importance=4)])
        create = responses.create
        seen = []
        def request(**kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                raise self.error(400, "Failed to download file URL: private diagnostic sentinel")
            return create(**kwargs)
        responses.create = request
        adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
        stderr = io.StringIO()
        with mock.patch.object(p, "fetch_pdf_for_inline_input", return_value=b"%PDF-1.7\nfixture") as fetch, contextlib.redirect_stderr(stderr):
            result = adapter.analyze_pdf(self.candidate())
        self.assertEqual(result, analysis(importance=4))
        fetch.assert_called_once()
        self.assertEqual(len(seen), 2)
        payload = seen[1]["input"][0]["content"][0]
        self.assertNotIn("file_url", payload)
        self.assertEqual(base64.b64decode(payload["file_data"].split(",", 1)[1]), b"%PDF-1.7\nfixture")
        for field in ("model", "reasoning", "text", "store", "instructions"):
            self.assertEqual(seen[0][field], seen[1][field])
        self.assertNotIn("private diagnostic sentinel", stderr.getvalue())
        self.assertIn("category=file_url_download", stderr.getvalue())

    def test_other_errors_never_trigger_inline_retry(self):
        self.assertTrue(p._file_url_download_error(self.error(400, "Error while downloading https://arxiv.org/pdf/2608.12345v1")))
        for status, message in [(429, "rate limit"), (401, "unauthorized"), (400, "context too long"), (500, "download file URL"), (None, "timeout")]:
            with self.subTest(status=status):
                responses = SimpleNamespace(create=mock.Mock(side_effect=self.error(status, message)))
                adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
                with mock.patch.object(p, "fetch_pdf_for_inline_input") as fetch, contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(p.UpdaterOfflineError):
                        adapter.analyze_pdf(self.candidate())
                fetch.assert_not_called()
                self.assertEqual(responses.create.call_count, 1)

    def test_pdf_download_is_bounded_and_rejects_non_pdf_content(self):
        for body, valid in [(b"%PDF-1.7\nfixture", True), (b"<html>Unavailable</html>", False), (b"%PDF-" + b"x" * (20 * 1024 * 1024), False)]:
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.geturl.return_value = "https://arxiv.org/pdf/2608.12345v1"
            response.read.return_value = body
            with mock.patch.object(p.urllib.request, "urlopen", return_value=response):
                if valid:
                    self.assertEqual(p.fetch_pdf_for_inline_input("2608.12345v1", timeout=12), body)
                else:
                    with self.assertRaises(p.UpdaterOfflineError):
                        p.fetch_pdf_for_inline_input("2608.12345v1", timeout=12)
            response.read.assert_called_once_with(20 * 1024 * 1024 + 1)

    def test_transport_failure_can_use_only_the_official_export_host(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://export.arxiv.org/pdf/2608.12345v1"
        response.read.return_value = b"%PDF-1.7\nfixture"
        error = p.urllib.error.HTTPError("https://arxiv.org/pdf/2608.12345v1", 403, "private sentinel", {}, None)
        with mock.patch.object(p.urllib.request, "urlopen", side_effect=[error, response]) as opener, contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertTrue(p.fetch_pdf_for_inline_input("2608.12345v1", timeout=12).startswith(b"%PDF-"))
        self.assertEqual(opener.call_args_list[1].args[0].full_url, "https://export.arxiv.org/pdf/2608.12345v1")
        self.assertNotIn("private sentinel", log.getvalue())
        response.geturl.return_value = "https://example.org/pdf/2608.12345v1"
        with mock.patch.object(p.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(p.digest.SchemaError):
                p.fetch_pdf_for_inline_input("2608.12345v1", timeout=12)
        response.geturl.return_value = "https://export.arxiv.org/pdf/2608.12345v2"
        with mock.patch.object(p.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(p.digest.SchemaError):
                p.fetch_pdf_for_inline_input("2608.12345v1", timeout=12)
