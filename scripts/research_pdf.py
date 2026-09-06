"""Local PDF inspection before any paid file input; never publish PDF bytes."""
from __future__ import annotations

import io
import re

MAX_FULL_TEXT_PAGES = 50
MAX_INTRO_PAGES = 20
MAX_INTRO_CHARS = 30_000


class PdfInspectionError(ValueError):
    pass


def extract_introduction(text: str) -> str:
    # Accept common numbered headings, but not a TOC line or prose mention.
    start = re.search(r"(?im)^\s*(?:(?:1|I)[.\s]+)?Introduction\s*\n", text)
    if not start:
        raise PdfInspectionError("Introduction heading was not identifiable")
    rest = text[start.end():]
    # Stop at the next top-level section, not 1.1 / 1.2 subsections.
    end = re.search(
        r"(?im)^[ \t]*(?:(?:2|II)\.?[ \t]+(?:[A-Z][^\n]{0,140})|"
        r"(?:Background|Related work|Model|Methodology|Methods|Preliminaries|"
        r"The model|Problem formulation|Notation))\s*\n", rest)
    if not end:
        raise PdfInspectionError("Introduction end was not identifiable within the local bound")
    intro = rest[:end.start()].strip()
    if not 100 <= len(intro) <= MAX_INTRO_CHARS:
        raise PdfInspectionError("Introduction was empty or exceeded the local bound")
    return intro


def inspect_pdf(body: bytes) -> tuple[int, str | None]:
    """Return total pages and, only above 50 pages, the exact introduction."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body), strict=True)
        if reader.is_encrypted:
            raise PdfInspectionError("Encrypted PDF cannot be inspected safely")
        pages = len(reader.pages)
        if pages < 1:
            raise PdfInspectionError("PDF has no pages")
        if pages <= MAX_FULL_TEXT_PAGES:
            return pages, None
        # Text is extracted locally, not sent to a model for section detection.
        texts = []
        for page in reader.pages[:MAX_INTRO_PAGES]:
            texts.append(page.extract_text() or "")
            text = "\n".join(texts)
            if len(text) > 150_000:
                raise PdfInspectionError("PDF opening text exceeds the local bound")
            try:
                return pages, extract_introduction(text)
            except PdfInspectionError:
                continue
        raise PdfInspectionError("Could not isolate Introduction; no full PDF was sent")
    except PdfInspectionError:
        raise
    except Exception as exc:
        raise PdfInspectionError("PDF page count or text could not be validated") from exc
