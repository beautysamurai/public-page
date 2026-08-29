#!/usr/bin/env python3
"""Validate a complete reviewed public bundle before a startup sync.

The bundle consists of two full snapshots:

- chatgpt_scheduler_history.json: the Japanese/source public history
- en.json: the reviewed English editorial overlay

This validator is intentionally conservative. Existing public editions and
translations are immutable, incoming snapshots may only append editions, and
the English overlay must match the source edition/paper/rating structure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from import_scheduler_history import HistoryImportError, load_history


MAX_BYTES = 16 * 1024 * 1024
TRANSLATION_TOP_FIELDS = frozenset({"schemaVersion", "language", "editions"})
TRANSLATION_EDITION_FIELDS = frozenset(
    {"editionId", "message", "sourceText", "papers"}
)
TRANSLATION_PAPER_FIELDS = frozenset(
    {"arxivId", "schedulerLabel", "schedulerSummary", "ratings"}
)
TRANSLATION_RATING_FIELDS = frozenset({"label"})

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w.-])"
)
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
INTERNAL_REFERENCE_RE = re.compile(
    r"(?:\ue200cite|\ue202|\ue201|"
    r"\bturn\d+(?:search|view|open|fetch|academia)\d+\b|"
    r":chatgpt-content-reference)",
    re.IGNORECASE,
)
LOCAL_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/]|"
    r"\\\\[^\\/\s]+[\\/][^\\/\s]+|"
    r"(?:^|[\s('\"`\[\]{}=,:;|])/"
    r"(?!/)[^/\s'\"`\[\]{}=,:;|]+/"
    r"[^/\s'\"`\[\]{}=,:;|]+"
    r"(?:/[^/\s'\"`\[\]{}=,:;|]+)*)",
    re.IGNORECASE | re.MULTILINE,
)
LOCAL_URI_RE = re.compile(r"\b(?:file|vscode)://", re.IGNORECASE)
RELATIVE_PATH_RE = re.compile(
    r"(?:^|[\s('\"`\[\]{}=,:;|])"
    r"(?:\.\.?[\\/])+"
    r"[^\\/\s'\"`\[\]{}=,:;|]+"
    r"(?:[\\/][^\\/\s'\"`\[\]{}=,:;|]+)*",
    re.MULTILINE,
)
UNSAFE_MARKUP_RE = re.compile(r"(?:<\s*script\b|javascript:)", re.IGNORECASE)


class PublicBundleError(RuntimeError):
    """The reviewed public bundle is incomplete, unsafe, or non-monotonic."""


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicBundleError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicBundleError(f"cannot read bundle file: {path}") from exc
    if len(raw) > MAX_BYTES:
        raise PublicBundleError(f"bundle file exceeds {MAX_BYTES} bytes: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublicBundleError(f"bundle file must be UTF-8: {path}") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise PublicBundleError(f"bundle file is not valid JSON: {path}") from exc


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    context: str,
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise PublicBundleError(
            f"{context} has invalid fields; missing={missing}, unknown={unknown}"
        )


def _require_nonempty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublicBundleError(f"{context} must be a non-empty string")
    return value


def _validate_public_english_text(value: str, context: str) -> None:
    checks = (
        (CJK_RE, "contains Japanese/CJK text"),
        (EMAIL_RE, "contains an email address"),
        (UUID_RE, "contains a UUID-like identifier"),
        (INTERNAL_REFERENCE_RE, "contains an internal citation/reference"),
        (LOCAL_PATH_RE, "contains an absolute local path"),
        (LOCAL_URI_RE, "contains a local file or editor URI"),
        (RELATIVE_PATH_RE, "contains a relative local path"),
        (UNSAFE_MARKUP_RE, "contains unsafe markup or a javascript URI"),
    )
    for pattern, reason in checks:
        if pattern.search(value):
            raise PublicBundleError(f"{context} {reason}")


def validate_translation(value: object) -> dict[str, Any]:
    """Return a strictly validated English public-overlay object."""

    if not isinstance(value, dict):
        raise PublicBundleError("English overlay must be a JSON object")
    _require_exact_keys(value, TRANSLATION_TOP_FIELDS, "English overlay")
    schema_version = value["schemaVersion"]
    if type(schema_version) is not int or schema_version != 1:
        raise PublicBundleError("English overlay schemaVersion must be integer 1")
    if value["language"] != "en":
        raise PublicBundleError("English overlay language must be 'en'")
    editions = value["editions"]
    if not isinstance(editions, list):
        raise PublicBundleError("English overlay editions must be a list")

    seen_editions: set[str] = set()
    for edition_index, edition in enumerate(editions):
        context = f"English edition[{edition_index}]"
        if not isinstance(edition, dict):
            raise PublicBundleError(f"{context} must be an object")
        _require_exact_keys(edition, TRANSLATION_EDITION_FIELDS, context)
        edition_id = _require_nonempty_string(
            edition["editionId"], f"{context}.editionId"
        )
        if edition_id in seen_editions:
            raise PublicBundleError(f"duplicate English editionId: {edition_id}")
        seen_editions.add(edition_id)

        for field in ("message", "sourceText"):
            text = _require_nonempty_string(edition[field], f"{context}.{field}")
            _validate_public_english_text(text, f"{context}.{field}")

        papers = edition["papers"]
        if not isinstance(papers, list):
            raise PublicBundleError(f"{context}.papers must be a list")
        seen_papers: set[str] = set()
        for paper_index, paper in enumerate(papers):
            paper_context = f"{context}.papers[{paper_index}]"
            if not isinstance(paper, dict):
                raise PublicBundleError(f"{paper_context} must be an object")
            _require_exact_keys(paper, TRANSLATION_PAPER_FIELDS, paper_context)
            arxiv_id = _require_nonempty_string(
                paper["arxivId"], f"{paper_context}.arxivId"
            )
            if arxiv_id in seen_papers:
                raise PublicBundleError(
                    f"duplicate English paper arXiv ID in {edition_id}: {arxiv_id}"
                )
            seen_papers.add(arxiv_id)
            for field in ("schedulerLabel", "schedulerSummary"):
                text = _require_nonempty_string(
                    paper[field], f"{paper_context}.{field}"
                )
                _validate_public_english_text(text, f"{paper_context}.{field}")

            ratings = paper["ratings"]
            if not isinstance(ratings, list):
                raise PublicBundleError(f"{paper_context}.ratings must be a list")
            for rating_index, rating in enumerate(ratings):
                rating_context = f"{paper_context}.ratings[{rating_index}]"
                if not isinstance(rating, dict):
                    raise PublicBundleError(f"{rating_context} must be an object")
                _require_exact_keys(
                    rating, TRANSLATION_RATING_FIELDS, rating_context
                )
                label = _require_nonempty_string(
                    rating["label"], f"{rating_context}.label"
                )
                _validate_public_english_text(label, f"{rating_context}.label")
    return value


def load_translation(path: Path) -> dict[str, Any]:
    return validate_translation(_read_json(path))


def _by_edition_id(
    editions: Sequence[Mapping[str, Any]],
    context: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for edition in editions:
        edition_id = str(edition["editionId"])
        if edition_id in result:
            raise PublicBundleError(f"duplicate {context} editionId: {edition_id}")
        result[edition_id] = edition
    return result


def _require_append_only(
    current: Sequence[Mapping[str, Any]],
    incoming: Sequence[Mapping[str, Any]],
    context: str,
) -> None:
    current_list = list(current)
    incoming_list = list(incoming)
    current_by_id = _by_edition_id(current_list, f"current {context}")
    incoming_by_id = _by_edition_id(incoming_list, f"incoming {context}")
    missing = sorted(set(current_by_id) - set(incoming_by_id))
    if missing:
        raise PublicBundleError(
            f"incoming {context} removes existing public editions: {missing}"
        )
    changed = sorted(
        edition_id
        for edition_id, current_edition in current_by_id.items()
        if incoming_by_id[edition_id] != current_edition
    )
    if changed:
        raise PublicBundleError(
            f"incoming {context} modifies immutable public editions: {changed}"
        )
    if incoming_list[: len(current_list)] != current_list:
        raise PublicBundleError(
            f"incoming {context} reorders existing public editions; "
            "the current sequence must remain an exact prefix"
        )


def validate_bundle(
    current_history: Mapping[str, Any],
    current_translation: Mapping[str, Any],
    incoming_history: Mapping[str, Any],
    incoming_translation: Mapping[str, Any],
) -> tuple[int, int, int]:
    current_editions = current_history["editions"]
    incoming_editions = incoming_history["editions"]
    current_english = current_translation["editions"]
    incoming_english = incoming_translation["editions"]

    _require_append_only(current_editions, incoming_editions, "source history")
    _require_append_only(current_english, incoming_english, "English overlay")

    source_ids = [edition["editionId"] for edition in incoming_editions]
    english_ids = [edition["editionId"] for edition in incoming_english]
    if english_ids != source_ids:
        raise PublicBundleError(
            "English overlay edition order/identity must exactly match source history"
        )

    paper_count = 0
    rating_count = 0
    for source, translated in zip(
        incoming_editions, incoming_english, strict=True
    ):
        source_papers = source["papers"]
        translated_papers = translated["papers"]
        source_arxiv_ids = [paper["arxivId"] for paper in source_papers]
        translated_arxiv_ids = [paper["arxivId"] for paper in translated_papers]
        if translated_arxiv_ids != source_arxiv_ids:
            raise PublicBundleError(
                f"English paper order/identity mismatch in {source['editionId']}"
            )
        for source_paper, translated_paper in zip(
            source_papers, translated_papers, strict=True
        ):
            if len(translated_paper["ratings"]) != len(source_paper["ratings"]):
                raise PublicBundleError(
                    "English rating-label count mismatch for "
                    f"{source['editionId']} / {source_paper['arxivId']}"
                )
            paper_count += 1
            rating_count += len(source_paper["ratings"])

    added = len(incoming_editions) - len(current_editions)
    if added <= 0:
        raise PublicBundleError("incoming bundle contains no new editions")
    return added, paper_count, rating_count


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current-history",
        type=Path,
        default=Path("content/chatgpt_scheduler_history.json"),
    )
    parser.add_argument(
        "--current-translation",
        type=Path,
        default=Path("site/data/i18n/en.json"),
    )
    parser.add_argument("--incoming-history", type=Path, required=True)
    parser.add_argument("--incoming-translation", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        current_history = load_history(args.current_history)
        incoming_history = load_history(args.incoming_history)
        current_translation = load_translation(args.current_translation)
        incoming_translation = load_translation(args.incoming_translation)
        added, paper_count, rating_count = validate_bundle(
            current_history,
            current_translation,
            incoming_history,
            incoming_translation,
        )
    except (HistoryImportError, PublicBundleError, OSError) as exc:
        print(f"validate_public_bundle: {exc}", file=sys.stderr)
        return 1
    print(
        "reviewed public bundle is valid: "
        f"{added} new edition(s), {paper_count} paper(s), "
        f"{rating_count} rating label(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
