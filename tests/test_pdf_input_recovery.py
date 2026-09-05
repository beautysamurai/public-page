import base64
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts import research_pipeline as p
from tests.test_research_pipeline import config, entry, analysis, RecordingResponses


class PdfInputRecoveryTests(unittest.TestCase):
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
