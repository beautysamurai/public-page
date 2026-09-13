"""Create a public PDF copy without local-only link targets; never edit the source."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject


def prepare(source: Path, output: Path) -> dict:
    if source.resolve() == output.resolve():
        raise ValueError("The public copy must not overwrite its source.")
    reader = PdfReader(source)
    root = reader.trailer["/Root"]
    if reader.is_encrypted or reader.attachments or "/AA" in root:
        raise ValueError("Encrypted PDFs, attachments and automatic actions require separate review.")
    opening = root.get("/OpenAction", DictionaryObject()).get_object()
    if opening and (not isinstance(opening, DictionaryObject) or opening.get("/S") != "/GoTo"):
        raise ValueError("Only internal opening destinations are supported.")
    if "/JavaScript" in root.get("/Names", DictionaryObject()).get_object():
        raise ValueError("PDF JavaScript is not supported for publication.")
    writer = PdfWriter(clone_from=reader)
    removed = 0
    retained = 0
    for page in writer.pages:
        if "/AA" in page:
            raise ValueError("Automatic page actions require separate review.")
        annotations = ArrayObject()
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            action = annotation.get("/A", DictionaryObject()).get_object()
            kind = action.get("/S")
            if "/AA" in annotation or kind not in (None, "/GoTo", "/URI"):
                raise ValueError("Unexpected annotation action; refusing publication.")
            if kind == "/URI":
                uri = str(action.get("/URI", ""))
                parsed = urlsplit(uri)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                    removed += 1
                    continue
                retained += 1
            annotations.append(reference)
        if "/Annots" in page:
            page[NameObject("/Annots")] = annotations
    # Drop unreachable annotation objects too, so local paths are not left in
    # unused PDF objects. Content streams, fonts and internal navigation remain.
    writer.compress_identical_objects(remove_identicals=False, remove_orphans=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    writer.write(output)
    published = PdfReader(output)
    if len(published.pages) != len(reader.pages):
        raise ValueError("Page count changed.")
    for before, after in zip(reader.pages, published.pages, strict=True):
        old_content, new_content = before.get_contents(), after.get_contents()
        if (old_content.get_data() if old_content is not None else b"") != (new_content.get_data() if new_content is not None else b""):
            raise ValueError("Page content changed.")
    return {"pages": len(published.pages), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "localLinksRemoved": removed, "webLinksRetained": retained}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output)))
