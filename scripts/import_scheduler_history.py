#!/usr/bin/env python3
"""Validate and publish curated ChatGPT scheduler history without network use.

Latest-edition ordering is deliberately independent of input order:

1. editionDate (the explicit Asia/Tokyo calendar edition date),
2. importedAt (compared as an absolute timezone-aware instant),
3. editionId (lexicographic tie-breaker).

editionKind never participates in ordering. A weekly or monthly edition can
therefore win on a same-day tie only because its explicit importedAt or
editionId sorts later, never because aggregate reports receive an implicit
preference.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 2
JST = ZoneInfo("Asia/Tokyo")

SOURCE_FIELDS = ("schemaVersion", "editions")
EDITION_FIELDS = (
    "editionId",
    "editionDate",
    "editionKind",
    "sourceKind",
    "sourceLabel",
    "importedAt",
    "status",
    "message",
    "expectedBatchDate",
    "observedBatchDate",
    "periodStart",
    "periodEnd",
    "sourceText",
    "papers",
)
PAPER_FIELDS = (
    "arxivId",
    "title",
    "authors",
    "submittedDate",
    "updatedDate",
    "topics",
    "absUrl",
    "pdfUrl",
    "schedulerRank",
    "schedulerRating",
    "schedulerRatingScale",
    "schedulerLabel",
    "schedulerSummary",
    "ratings",
)
RATING_FIELDS = ("label", "value", "scale")
INDEX_FIELDS = ("schemaVersion", "editions")
INDEX_EDITION_FIELDS = (
    "editionId",
    "date",
    "periodEnd",
    "kind",
    "path",
    "status",
    "paperCount",
    "sourceKind",
    "title",
)

EDITION_KINDS = frozenset({"daily", "weekly", "monthly"})
SOURCE_KINDS = frozenset(
    {"chatgpt-scheduled-task", "openai-responses-api"}
)
STATUSES = frozenset(
    {
        "UPDATE_CONFIRMED",
        "NO_RELEVANT_PAPERS",
        "UPDATE_NOT_CONFIRMED",
        "UPDATER_OFFLINE",
        "NO_NEW_BATCH_EXPECTED",
        "WEEKLY_REVIEW",
        "MONTHLY_REVIEW",
    }
)

EDITION_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ARXIV_ID_RE = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
URL_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\[\]()\"']+",
    re.IGNORECASE,
)
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
THREAD_ID_RE = re.compile(
    r"\b(?:thread|conversation|chat)[_-]?(?:id)?\s*[:=]\s*"
    r"[0-9a-f][0-9a-f-]{7,}",
    re.IGNORECASE,
)
INTERNAL_CITATION_RE = re.compile(
    r"(?:\ue200cite|\ue202|\ue201|"
    r"\bturn\d+(?:search|view|open|fetch|academia)\d+\b|"
    r":chatgpt-content-reference)",
    re.IGNORECASE,
)
HTML_RE = re.compile(r"(?:<!--|-->|<\s*/?\s*[A-Za-z][^>]*>)")
EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w.-])"
)
WINDOWS_PATH_RE = re.compile(
    r"(?:^|[\s('\"])(?:[A-Za-z]:[\\/]|\\\\(?:wsl|localhost|[^\\\s]+)\\)",
    re.IGNORECASE | re.MULTILINE,
)
POSIX_PATH_RE = re.compile(
    r"(?:^|[\s('\"])/(?:home|Users|mnt|tmp|private|var/tmp)/",
    re.IGNORECASE | re.MULTILINE,
)
LOCAL_URI_RE = re.compile(r"\b(?:file|vscode)://", re.IGNORECASE)
UNSUPPORTED_URI_RE = re.compile(
    r"\b(?:data|javascript|ftp|sftp|ssh):",
    re.IGNORECASE,
)
RELATIVE_PATH_RE = re.compile(
    r"(?:^|[\s('\"])(?:\.\.?[\\/])+(?:[^\s'\"]+)",
    re.MULTILINE,
)
UTM_RE = re.compile(
    r"\butm_(?:source|medium|campaign|term|content)\b",
    re.IGNORECASE,
)
BARE_WEB_RE = re.compile(
    r"(?<![\w./-])www\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s]*)?",
    re.IGNORECASE,
)
BARE_DOMAIN_RE = re.compile(
    r"(?<!://)(?<![@\w.-])(?:[A-Za-z0-9-]+\.)+"
    r"(?:com|org|net|io|ai|dev|app|co|jp)\b",
    re.IGNORECASE,
)


class HistoryImportError(RuntimeError):
    """Base class for expected importer failures."""


class HistorySchemaError(HistoryImportError):
    """The source or generated data violates the exact public schema."""


class UnsafePublicContentError(HistorySchemaError):
    """A public string contains a forbidden token or destination."""


class ArchiveConflictError(HistoryImportError):
    """An immutable archive path already contains different bytes."""


@dataclass(frozen=True)
class GeneratedArtifacts:
    latest: bytes
    index: bytes
    archives: tuple[tuple[str, bytes], ...]
    papers: bytes


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: Iterable[str],
    label: str,
) -> None:
    expected_set = set(expected)
    actual_set = set(value)
    private = sorted(
        key for key in actual_set if isinstance(key, str) and key.startswith("_")
    )
    extra = sorted(str(key) for key in actual_set - expected_set)
    missing = sorted(expected_set - actual_set)
    if private or extra or missing:
        details: list[str] = []
        if private:
            details.append("private fields are forbidden")
        if extra:
            details.append("unexpected fields: " + ", ".join(extra))
        if missing:
            details.append("missing fields: " + ", ".join(missing))
        raise HistorySchemaError(f"{label}: {'; '.join(details)}")


def _validate_date(
    value: object,
    field: str,
    *,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise HistorySchemaError(f"{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HistorySchemaError(f"{field} is not a calendar date") from exc
    if parsed.isoformat() != value:
        raise HistorySchemaError(f"{field} must be canonical YYYY-MM-DD")
    return value


def _parse_imported_at(value: object, field: str = "importedAt") -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise HistorySchemaError(f"{field} must be a timezone-aware RFC 3339 timestamp")
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HistorySchemaError(
            f"{field} must be a timezone-aware RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistorySchemaError(
            f"{field} must be a timezone-aware RFC 3339 timestamp"
        )
    return parsed


def _validate_arxiv_url(
    value: object,
    kind: str,
    *,
    arxiv_id: str | None = None,
    field: str = "URL",
) -> str:
    if not isinstance(value, str):
        raise UnsafePublicContentError(f"{field} must be a string")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise UnsafePublicContentError(f"{field} is not a safe arXiv URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.netloc != "arxiv.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UnsafePublicContentError(f"{field} is not a canonical arXiv URL")
    match = re.fullmatch(rf"/{kind}/(.+)", parsed.path)
    candidate = match.group(1) if match else ""
    if not ARXIV_ID_RE.fullmatch(candidate):
        raise UnsafePublicContentError(f"{field} has an invalid arXiv path")
    if arxiv_id is not None and candidate.casefold() != arxiv_id.casefold():
        raise UnsafePublicContentError(f"{field} does not match arxivId")
    return value


def _validate_public_url(value: str, field: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise UnsafePublicContentError(f"{field} contains an invalid URL") from exc
    if parsed.path.startswith("/abs/"):
        _validate_arxiv_url(value, "abs", field=field)
    elif parsed.path.startswith("/pdf/"):
        _validate_arxiv_url(value, "pdf", field=field)
    else:
        raise UnsafePublicContentError(
            f"{field} contains a non-arXiv or unsupported URL"
        )


def _validate_public_text(
    value: object,
    field: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    """Validate a public string without modifying a single character."""

    if not isinstance(value, str):
        raise HistorySchemaError(f"{field} must be a string")
    if (not allow_empty and not value) or len(value) > maximum:
        raise HistorySchemaError(f"{field} has an invalid length")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise UnsafePublicContentError(f"{field} contains a control character")
    forbidden_patterns = (
        (INTERNAL_CITATION_RE, "an internal citation token"),
        (UUID_RE, "a UUID or thread identifier"),
        (THREAD_ID_RE, "a thread identifier"),
        (HTML_RE, "HTML"),
        (EMAIL_RE, "an email address"),
        (WINDOWS_PATH_RE, "a local Windows path"),
        (POSIX_PATH_RE, "a local filesystem path"),
        (LOCAL_URI_RE, "a local URI"),
        (UNSUPPORTED_URI_RE, "an unsupported URI"),
        (RELATIVE_PATH_RE, "a relative local path"),
        (UTM_RE, "tracking parameters"),
        (BARE_WEB_RE, "a non-canonical web address"),
        (BARE_DOMAIN_RE, "a non-canonical web address"),
    )
    for pattern, description in forbidden_patterns:
        if pattern.search(value):
            raise UnsafePublicContentError(f"{field} contains {description}")
    for match in URL_RE.finditer(value):
        url = match.group(0).rstrip(".,;:!?。、，；：！？")
        _validate_public_url(url, field)
    return value


def _validate_string_list(
    value: object,
    field: str,
    *,
    maximum_items: int = 100,
    maximum_string: int = 300,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise HistorySchemaError(f"{field} must be a bounded list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(
            _validate_public_text(
                item,
                f"{field}[{index}]",
                maximum=maximum_string,
            )
        )
    return result


def _validate_rating(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HistorySchemaError(f"{field} must be an object")
    _require_exact_keys(value, RATING_FIELDS, field)
    label = _validate_public_text(
        value["label"],
        f"{field}.label",
        maximum=120,
    )
    rating_value = value["value"]
    scale = value["scale"]
    if not _is_number(scale) or scale <= 0 or scale > 100:
        raise HistorySchemaError(f"{field}.scale must be a number from 1 to 100")
    if not _is_number(rating_value) or not 0 <= rating_value <= scale:
        raise HistorySchemaError(
            f"{field}.value must be a number from 0 through its scale"
        )
    return {"label": label, "value": rating_value, "scale": scale}


def _validate_paper(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HistorySchemaError(f"{field} must be an object")
    _require_exact_keys(value, PAPER_FIELDS, field)
    arxiv_id = value["arxivId"]
    if not isinstance(arxiv_id, str) or not ARXIV_ID_RE.fullmatch(arxiv_id):
        raise HistorySchemaError(f"{field}.arxivId is invalid")
    _validate_public_text(
        arxiv_id,
        f"{field}.arxivId",
        maximum=80,
    )
    title = _validate_public_text(
        value["title"],
        f"{field}.title",
        maximum=500,
    )
    authors = _validate_string_list(
        value["authors"],
        f"{field}.authors",
        maximum_items=100,
        maximum_string=300,
    )
    submitted_date = _validate_date(
        value["submittedDate"],
        f"{field}.submittedDate",
    )
    updated_date = _validate_date(
        value["updatedDate"],
        f"{field}.updatedDate",
    )
    if date.fromisoformat(updated_date) < date.fromisoformat(submitted_date):
        raise HistorySchemaError(
            f"{field}.updatedDate cannot precede submittedDate"
        )
    topics = _validate_string_list(
        value["topics"],
        f"{field}.topics",
        maximum_items=50,
        maximum_string=120,
    )

    expected_abs = f"https://arxiv.org/abs/{arxiv_id}"
    expected_pdf = f"https://arxiv.org/pdf/{arxiv_id}"
    _validate_arxiv_url(
        value["absUrl"],
        "abs",
        arxiv_id=arxiv_id,
        field=f"{field}.absUrl",
    )
    _validate_arxiv_url(
        value["pdfUrl"],
        "pdf",
        arxiv_id=arxiv_id,
        field=f"{field}.pdfUrl",
    )
    if value["absUrl"] != expected_abs or value["pdfUrl"] != expected_pdf:
        raise UnsafePublicContentError(
            f"{field} URLs must be canonical values derived from arxivId"
        )

    rank = value["schedulerRank"]
    if rank is not None and (
        not _is_int(rank) or not 1 <= rank <= 10_000
    ):
        raise HistorySchemaError(
            f"{field}.schedulerRank must be null or a positive integer"
        )
    scheduler_rating = value["schedulerRating"]
    scheduler_scale = value["schedulerRatingScale"]
    if (
        not _is_number(scheduler_scale)
        or scheduler_scale <= 0
        or scheduler_scale > 100
    ):
        raise HistorySchemaError(
            f"{field}.schedulerRatingScale must be from 1 to 100"
        )
    if (
        not _is_number(scheduler_rating)
        or not 0 <= scheduler_rating <= scheduler_scale
    ):
        raise HistorySchemaError(
            f"{field}.schedulerRating must be within its scale"
        )
    scheduler_label = _validate_public_text(
        value["schedulerLabel"],
        f"{field}.schedulerLabel",
        maximum=200,
    )
    scheduler_summary = _validate_public_text(
        value["schedulerSummary"],
        f"{field}.schedulerSummary",
        maximum=10_000,
    )
    ratings_value = value["ratings"]
    if not isinstance(ratings_value, list) or len(ratings_value) > 50:
        raise HistorySchemaError(f"{field}.ratings must be a bounded list")
    ratings = [
        _validate_rating(item, f"{field}.ratings[{index}]")
        for index, item in enumerate(ratings_value)
    ]
    rating_labels = [item["label"].casefold() for item in ratings]
    if len(rating_labels) != len(set(rating_labels)):
        raise HistorySchemaError(f"{field}.ratings has duplicate labels")

    return {
        "arxivId": arxiv_id,
        "title": title,
        "authors": authors,
        "submittedDate": submitted_date,
        "updatedDate": updated_date,
        "topics": topics,
        "absUrl": expected_abs,
        "pdfUrl": expected_pdf,
        "schedulerRank": rank,
        "schedulerRating": scheduler_rating,
        "schedulerRatingScale": scheduler_scale,
        "schedulerLabel": scheduler_label,
        "schedulerSummary": scheduler_summary,
        "ratings": ratings,
    }


def _validate_edition(value: object, index: int) -> dict[str, Any]:
    field = f"editions[{index}]"
    if not isinstance(value, Mapping):
        raise HistorySchemaError(f"{field} must be an object")
    _require_exact_keys(value, EDITION_FIELDS, field)

    edition_id = value["editionId"]
    if (
        not isinstance(edition_id, str)
        or len(edition_id) > 120
        or not EDITION_ID_RE.fullmatch(edition_id)
        or edition_id in {"index", "latest"}
    ):
        raise HistorySchemaError(
            f"{field}.editionId must be a safe lowercase archive identifier"
        )
    _validate_public_text(
        edition_id,
        f"{field}.editionId",
        maximum=120,
    )
    edition_date = _validate_date(
        value["editionDate"],
        f"{field}.editionDate",
    )
    edition_kind = value["editionKind"]
    if not isinstance(edition_kind, str) or edition_kind not in EDITION_KINDS:
        raise HistorySchemaError(f"{field}.editionKind is invalid")
    source_kind = value["sourceKind"]
    if not isinstance(source_kind, str) or source_kind not in SOURCE_KINDS:
        raise HistorySchemaError(
            f"{field}.sourceKind must be one of {sorted(SOURCE_KINDS)!r}"
        )
    source_label = _validate_public_text(
        value["sourceLabel"],
        f"{field}.sourceLabel",
        maximum=300,
    )
    imported_at = _validate_public_text(
        value["importedAt"],
        f"{field}.importedAt",
        maximum=50,
    )
    _parse_imported_at(imported_at, f"{field}.importedAt")
    status = value["status"]
    if not isinstance(status, str) or status not in STATUSES:
        raise HistorySchemaError(f"{field}.status is invalid")
    message = _validate_public_text(
        value["message"],
        f"{field}.message",
        maximum=2_000,
    )
    expected_batch_date = _validate_date(
        value["expectedBatchDate"],
        f"{field}.expectedBatchDate",
        nullable=True,
    )
    observed_batch_date = _validate_date(
        value["observedBatchDate"],
        f"{field}.observedBatchDate",
        nullable=True,
    )
    period_start = _validate_date(
        value["periodStart"],
        f"{field}.periodStart",
        nullable=True,
    )
    period_end = _validate_date(
        value["periodEnd"],
        f"{field}.periodEnd",
        nullable=True,
    )
    if (
        period_start is not None
        and period_end is not None
        and date.fromisoformat(period_end) < date.fromisoformat(period_start)
    ):
        raise HistorySchemaError(f"{field}.periodEnd cannot precede periodStart")
    source_text = _validate_public_text(
        value["sourceText"],
        f"{field}.sourceText",
        maximum=500_000,
    )
    papers_value = value["papers"]
    if not isinstance(papers_value, list) or len(papers_value) > 2_000:
        raise HistorySchemaError(f"{field}.papers must be a bounded list")
    papers = [
        _validate_paper(item, f"{field}.papers[{paper_index}]")
        for paper_index, item in enumerate(papers_value)
    ]
    paper_ids = [paper["arxivId"].casefold() for paper in papers]
    if len(paper_ids) != len(set(paper_ids)):
        raise HistorySchemaError(f"{field}.papers contains duplicate arXiv ids")
    ranks = [
        paper["schedulerRank"]
        for paper in papers
        if paper["schedulerRank"] is not None
    ]
    if len(ranks) != len(set(ranks)):
        raise HistorySchemaError(
            f"{field}.papers contains duplicate assigned scheduler ranks"
        )

    return {
        "editionId": edition_id,
        "editionDate": edition_date,
        "editionKind": edition_kind,
        "sourceKind": source_kind,
        "sourceLabel": source_label,
        "importedAt": imported_at,
        "status": status,
        "message": message,
        "expectedBatchDate": expected_batch_date,
        "observedBatchDate": observed_batch_date,
        "periodStart": period_start,
        "periodEnd": period_end,
        "sourceText": source_text,
        "papers": papers,
    }


def validate_history(value: object) -> dict[str, Any]:
    """Return a validated, deterministically ordered-key source object."""

    if not isinstance(value, Mapping):
        raise HistorySchemaError("history source must be an object")
    _require_exact_keys(value, SOURCE_FIELDS, "history source")
    if (
        not _is_int(value["schemaVersion"])
        or value["schemaVersion"] != SCHEMA_VERSION
    ):
        raise HistorySchemaError("history source schemaVersion must be 2")
    editions_value = value["editions"]
    if not isinstance(editions_value, list) or not editions_value:
        raise HistorySchemaError("history source editions must be non-empty")
    editions = [
        _validate_edition(item, index)
        for index, item in enumerate(editions_value)
    ]
    edition_ids = [edition["editionId"] for edition in editions]
    if len(edition_ids) != len(set(edition_ids)):
        raise HistorySchemaError("history source contains duplicate editionId values")
    return {"schemaVersion": SCHEMA_VERSION, "editions": editions}


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistorySchemaError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def load_history(path: Path) -> dict[str, Any]:
    """Load and validate one offline source file."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HistoryImportError(f"cannot read history source: {path}") from exc
    if len(raw) > 16 * 1024 * 1024:
        raise HistorySchemaError("history source exceeds the 16 MiB limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistorySchemaError("history source must be UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise HistorySchemaError("history source is not valid JSON") from exc
    return validate_history(value)


def _edition_order_key(
    edition: Mapping[str, Any],
) -> tuple[date, datetime, str]:
    # importedAt is converted to JST before comparison. Aware datetime
    # comparison still represents the absolute instant, while making the
    # intended local scheduling context explicit.
    imported_at_jst = _parse_imported_at(edition["importedAt"]).astimezone(JST)
    return (
        date.fromisoformat(edition["editionDate"]),
        imported_at_jst,
        edition["editionId"],
    )


def order_editions(
    editions: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return newest-first editions using the documented explicit key."""

    return sorted(editions, key=_edition_order_key, reverse=True)


def _edition_document(edition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        **{field: edition[field] for field in EDITION_FIELDS},
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def generate_artifacts(history: Mapping[str, Any]) -> GeneratedArtifacts:
    """Render deterministic v2 bytes without touching the filesystem."""

    validated = validate_history(history)
    ordered = order_editions(validated["editions"])
    latest = _json_bytes(_edition_document(ordered[0]))
    archives = tuple(
        (
            f"{edition['editionId']}.json",
            _json_bytes(_edition_document(edition)),
        )
        for edition in ordered
    )
    index_editions = [
        {
            "editionId": edition["editionId"],
            "date": edition["editionDate"],
            "periodEnd": edition["periodEnd"],
            "kind": edition["editionKind"],
            "path": f"{edition['editionId']}.json",
            "status": edition["status"],
            "paperCount": len(edition["papers"]),
            "sourceKind": edition["sourceKind"],
            # Keep sourceLabel as provenance in the edition document. The
            # index title describes the edition itself and is stable by kind.
            "title": {
                "daily": "Daily research screen",
                "weekly": "Weekly research review",
                "monthly": "Monthly research review",
            }[edition["editionKind"]],
        }
        for edition in ordered
    ]
    index = {
        "schemaVersion": SCHEMA_VERSION,
        "editions": index_editions,
    }
    _require_exact_keys(index, INDEX_FIELDS, "generated index")
    for item in index_editions:
        _require_exact_keys(item, INDEX_EDITION_FIELDS, "generated index edition")
    return GeneratedArtifacts(
        latest=latest,
        index=_json_bytes(index),
        archives=archives,
        # A lightweight cross-edition catalogue, not a new analysis. Preserve
        # every stored rating and tag; leave long narratives in their archives.
        papers=_json_bytes({
            "schemaVersion": 1,
            "editions": [
                {
                    "editionId": edition["editionId"],
                    "date": edition["editionDate"],
                    "kind": edition["editionKind"],
                    "periodEnd": edition["periodEnd"],
                    "papers": [
                        {field: paper[field] for field in (
                            "arxivId", "title", "authors", "topics",
                            "schedulerRating", "schedulerRatingScale",
                        )}
                        for paper in edition["papers"]
                    ],
                }
                for edition in ordered
            ],
        }),
    )


def _artifact_paths(
    artifacts: GeneratedArtifacts,
    output: Path,
    archive_dir: Path,
) -> tuple[tuple[Path, bytes, bool], ...]:
    result: list[tuple[Path, bytes, bool]] = [
        (archive_dir / name, content, True)
        for name, content in artifacts.archives
    ]
    result.append((archive_dir / "index.json", artifacts.index, False))
    result.append((output.with_name("papers.json"), artifacts.papers, False))
    result.append((output, artifacts.latest, False))
    resolved = [path.resolve(strict=False) for path, _content, _immutable in result]
    if len(resolved) != len(set(resolved)):
        raise HistoryImportError("generated artifact paths collide")
    return tuple(result)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _write_if_changed(path: Path, content: bytes) -> bool:
    try:
        if path.read_bytes() == content:
            return False
    except FileNotFoundError:
        pass
    _atomic_write_bytes(path, content)
    return True


def persist_artifacts(
    artifacts: GeneratedArtifacts,
    output: Path,
    archive_dir: Path,
    *,
    refresh_archive_ids: Iterable[str] = (),
) -> list[Path]:
    """Persist artifacts, with explicit opt-in for managed archive refreshes."""

    planned = _artifact_paths(artifacts, output, archive_dir)
    refresh_names = {f"{edition_id}.json" for edition_id in refresh_archive_ids}
    archive_names = {
        path.name for path, _content, immutable in planned if immutable
    }
    unknown_refreshes = sorted(refresh_names - archive_names)
    if unknown_refreshes:
        raise HistoryImportError(
            f"archive refresh targets are not generated editions: {unknown_refreshes}"
        )
    # Preflight every immutable archive before any mutation. This prevents a
    # partial latest/index update when a historical edition conflicts.
    for path, expected, immutable in planned:
        if not immutable or path.name in refresh_names:
            continue
        try:
            existing = path.read_bytes()
        except FileNotFoundError:
            continue
        if existing != expected:
            raise ArchiveConflictError(
                f"refusing to overwrite different archive bytes: {path}"
            )

    changed: list[Path] = []
    for path, content, _immutable in planned:
        if _write_if_changed(path, content):
            changed.append(path)
    return changed


def check_artifacts(
    artifacts: GeneratedArtifacts,
    output: Path,
    archive_dir: Path,
) -> list[Path]:
    """Return missing/different expected artifacts without writing anything."""

    mismatches: list[Path] = []
    for path, expected, _immutable in _artifact_paths(
        artifacts,
        output,
        archive_dir,
    ):
        try:
            actual = path.read_bytes()
        except FileNotFoundError:
            mismatches.append(path)
            continue
        if actual != expected:
            mismatches.append(path)
    return mismatches


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("content/chatgpt_scheduler_history.json"),
        help="curated v2 scheduler-history JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("site/data/latest.json"),
        help="generated latest-edition JSON",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("site/data/archive"),
        help="generated edition archive directory",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare deterministic bytes and perform no writes",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        history = load_history(args.source)
        artifacts = generate_artifacts(history)
        if args.check:
            mismatches = check_artifacts(
                artifacts,
                args.output,
                args.archive_dir,
            )
            if mismatches:
                for path in mismatches:
                    print(f"out of date: {path}", file=sys.stderr)
                return 1
            print("scheduler history artifacts are up to date")
            return 0
        changed = persist_artifacts(
            artifacts,
            args.output,
            args.archive_dir,
        )
    except (HistoryImportError, OSError) as exc:
        print(f"import_scheduler_history: {exc}", file=sys.stderr)
        return 1
    print(
        f"published {len(artifacts.archives)} edition(s); "
        f"changed {len(changed)} file(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
