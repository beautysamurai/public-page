import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, TextStringObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_public_pdf import prepare


class PublicPdfTests(unittest.TestCase):
    def test_published_file_matches_explicit_version_metadata(self):
        folder = ROOT / "site/documents"
        metadata = json.loads((folder / "decision_theory_rates_e_trading.json").read_text())
        content = (folder / metadata["file"]).read_bytes()
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertEqual(len(content), metadata["bytes"])
        self.assertEqual(hashlib.sha256(content).hexdigest(), metadata["sha256"])
        reader = PdfReader(folder / metadata["file"])
        self.assertEqual(len(reader.pages), metadata["pageCount"])
        self.assertEqual(reader.metadata.title, metadata["title"])
        self.assertFalse(reader.attachments)
        self.assertTrue(reader.outline)
        self.assertLessEqual(metadata["firstPublishedOn"], metadata["lastUploadedOn"])

    def test_no_local_uris_remain_even_in_unreferenced_pdf_objects(self):
        reader = PdfReader(ROOT / "site/documents/decision_theory_rates_e_trading.pdf")
        uri_count = 0

        def check(obj):
            nonlocal uri_count
            if isinstance(obj, dict):
                if "/URI" in obj:
                    parsed = urlsplit(str(obj["/URI"]))
                    self.assertIn(parsed.scheme, ("http", "https"))
                    self.assertTrue(parsed.hostname)
                    uri_count += 1
                for value in obj.values():
                    if not isinstance(value, IndirectObject):
                        check(value)
            elif isinstance(obj, list):
                for value in obj:
                    if not isinstance(value, IndirectObject):
                        check(value)

        for generation, objects in reader.xref.items():
            for object_id in objects:
                if object_id:
                    check(reader.get_object(IndirectObject(object_id, generation, reader)))
        self.assertEqual(uri_count, 54)

    def test_preparer_preserves_source_and_removes_local_links(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "source.pdf", Path(temp) / "public.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=300, height=300)
            annotations = ArrayObject()
            for uri in ("file:///private/notebook.ipynb", "notes/readme.md", "https://example.org/paper"):
                annotations.append(DictionaryObject({NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(uri)})}))
            page[NameObject("/Annots")] = annotations
            writer.write(source)
            original = source.read_bytes()
            result = prepare(source, output)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(result["localLinksRemoved"], 2)
            self.assertEqual(result["webLinksRetained"], 1)
            with self.assertRaises(ValueError):
                prepare(source, source)
