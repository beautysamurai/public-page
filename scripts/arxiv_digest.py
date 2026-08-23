#!/usr/bin/env python3
"""Fetch, score, and safely publish the public arXiv research digest.

Only Python's standard library is used. Public JSON is constructed from an
exact allow-list so unreviewed Atom fields cannot leak into the website.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
API_ENDPOINT = "https://export.arxiv.org/api/query"
USER_AGENT = "public-arxiv-digest/1.0"
MAX_FEED_BYTES = 8 * 1024 * 1024

UPDATE_CONFIRMED = "UPDATE_CONFIRMED"
NO_RELEVANT_PAPERS = "NO_RELEVANT_PAPERS"
UPDATE_NOT_CONFIRMED = "UPDATE_NOT_CONFIRMED"
UPDATER_OFFLINE = "UPDATER_OFFLINE"
STATUSES = frozenset(
    {
        UPDATE_CONFIRMED,
        NO_RELEVANT_PAPERS,
        UPDATE_NOT_CONFIRMED,
        UPDATER_OFFLINE,
    }
)

REPORT_FIELDS = (
    "schemaVersion",
    "generatedAt",
    "checkedAt",
    "expectedBatchDate",
    "observedBatchDate",
    "status",
    "statusMessage",
    "papers",
)
PUBLICATION_FIELDS = (
    "arxivId",
    "title",
    "authors",
    "submittedDate",
    "updatedDate",
    "topics",
    "score",
    "scoreReasons",
    "abstract",
    "absUrl",
    "pdfUrl",
)
ARCHIVE_INDEX_FIELDS = ("schemaVersion", "reports")
ARCHIVE_REPORT_FIELDS = ("date", "path", "status", "paperCount")
CONFIG_FIELDS = frozenset(
    {"categories", "keywords", "minimumScore", "maxResults", "staleAfterDays"}
)

DEFAULT_CATEGORIES = (
    "q-fin.TR",
    "q-fin.MF",
    "q-fin.PR",
    "q-fin.RM",
    "q-fin.EC",
)
DEFAULT_KEYWORDS = (
    "electronic trading",
    "market microstructure",
    "limit order book",
    "order book",
    "LOB",
    "request for quote",
    "RFQ",
    "market making",
    "market maker",
    "optimal execution",
    "trade execution",
    "execution algorithm",
    "interest rate",
    "yield curve",
    "term structure",
    "interest rate swap",
    "swap",
    "swaption",
    "overnight indexed swap",
    "OIS",
    "fixed income",
    "bond market",
)
KEYWORD_WEIGHTS = {
    "electronic trading": 10,
    "market microstructure": 12,
    "limit order book": 12,
    "order book": 8,
    "lob": 8,
    "request for quote": 12,
    "rfq": 10,
    "market making": 10,
    "market maker": 10,
    "optimal execution": 12,
    "trade execution": 9,
    "execution algorithm": 9,
    "interest rate": 8,
    "yield curve": 11,
    "term structure": 9,
    "interest rate swap": 12,
    "swap": 5,
    "swaption": 11,
    "overnight indexed swap": 12,
    "ois": 9,
    "fixed income": 9,
    "bond market": 8,
}

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_ID_RE = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?", re.IGNORECASE
)
CATEGORY_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class DigestError(RuntimeError):
    """Base class for expected updater failures."""


class FeedParseError(DigestError):
    """The remote response is not a trustworthy arXiv Atom feed."""


class SchemaError(DigestError):
    """Generated or persisted JSON violates the public allow-list."""


class ConfigurationError(DigestError):
    """Topic configuration is invalid."""


@dataclass(frozen=True)
class DigestConfig:
    categories: tuple[str, ...] = DEFAULT_CATEGORIES
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    minimum_score: int = 6
    max_results: int = 100
    stale_after_days: int = 4


@dataclass(frozen=True)
class AtomEntry:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    submitted_at: datetime
    updated_at: datetime
    categories: tuple[str, ...]
    abstract: str


@dataclass(frozen=True)
class AtomFeed:
    updated_at: datetime | None
    entries: tuple[AtomEntry, ...]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalise_space(value: str) -> str:
    return " ".join(value.split())


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_datetime(value: str, field: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise FeedParseError(f"invalid {field} timestamp") from exc
    if parsed.tzinfo is None:
        raise FeedParseError(f"{field} timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _require_exact_keys(
    value: Mapping[str, Any], expected: Iterable[str], label: str
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
        raise SchemaError(f"{label}: {'; '.join(details)}")


def _validate_date_string(
    value: object, field: str, *, nullable: bool = False
) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise SchemaError(f"{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SchemaError(f"{field} is not a calendar date") from exc
    if parsed.isoformat() != value:
        raise SchemaError(f"{field} must be canonical YYYY-MM-DD")


def _validate_timestamp(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SchemaError(f"{field} must be an RFC 3339 UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise SchemaError(f"{field} must be an RFC 3339 UTC timestamp") from exc


def _validate_string_list(
    value: object,
    field: str,
    *,
    allow_empty: bool = True,
    maximum: int = 256,
) -> None:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise SchemaError(f"{field} must be a list of strings")
    for item in value:
        if not isinstance(item, str) or not item or len(item) > maximum:
            raise SchemaError(f"{field} contains an invalid string")


def validate_arxiv_url(
    value: object, kind: str, arxiv_id: str | None = None
) -> str:
    """Return a safe public arXiv URL or raise SchemaError."""

    if not isinstance(value, str):
        raise SchemaError(f"{kind} URL must be a string")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SchemaError(f"unsafe {kind} URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.netloc != "arxiv.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SchemaError(f"unsafe {kind} URL")
    if kind == "abs":
        match = re.fullmatch(r"/abs/(.+)", parsed.path)
        candidate = match.group(1) if match else ""
    elif kind == "pdf":
        match = re.fullmatch(r"/pdf/(.+?)(?:\.pdf)?", parsed.path)
        candidate = match.group(1) if match else ""
    else:
        raise ValueError("kind must be 'abs' or 'pdf'")
    if not ARXIV_ID_RE.fullmatch(candidate):
        raise SchemaError(f"invalid {kind} arXiv path")
    if arxiv_id is not None and candidate.casefold() != arxiv_id.casefold():
        raise SchemaError(f"{kind} URL does not match arxivId")
    return value


def validate_publication(value: Mapping[str, Any]) -> None:
    """Validate the exact public publication schema, rejecting extra fields."""

    if not isinstance(value, Mapping):
        raise SchemaError("paper must be an object")
    _require_exact_keys(value, PUBLICATION_FIELDS, "paper")
    arxiv_id = value["arxivId"]
    if not isinstance(arxiv_id, str) or not ARXIV_ID_RE.fullmatch(arxiv_id):
        raise SchemaError("paper.arxivId is invalid")
    for field, maximum in (("title", 500), ("abstract", 10_000)):
        item = value[field]
        if not isinstance(item, str) or not item or len(item) > maximum:
            raise SchemaError(f"paper.{field} is invalid")
    _validate_string_list(value["authors"], "paper.authors", allow_empty=False)
    _validate_string_list(value["topics"], "paper.topics")
    _validate_string_list(
        value["scoreReasons"], "paper.scoreReasons", maximum=500
    )
    _validate_date_string(value["submittedDate"], "paper.submittedDate")
    _validate_date_string(value["updatedDate"], "paper.updatedDate")
    score = value["score"]
    if not _is_int(score) or not 0 <= score <= 100:
        raise SchemaError("paper.score must be an integer from 0 to 100")
    validate_arxiv_url(value["absUrl"], "abs", arxiv_id)
    validate_arxiv_url(value["pdfUrl"], "pdf", arxiv_id)


def validate_report(value: Mapping[str, Any]) -> None:
    """Validate the full site report before any file is written."""

    if not isinstance(value, Mapping):
        raise SchemaError("report must be an object")
    _require_exact_keys(value, REPORT_FIELDS, "report")
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise SchemaError("unsupported report schemaVersion")
    _validate_timestamp(value["generatedAt"], "generatedAt")
    _validate_timestamp(value["checkedAt"], "checkedAt")
    _validate_date_string(value["expectedBatchDate"], "expectedBatchDate")
    _validate_date_string(
        value["observedBatchDate"], "observedBatchDate", nullable=True
    )
    if value["status"] not in STATUSES:
        raise SchemaError("report.status is invalid")
    message = value["statusMessage"]
    if not isinstance(message, str) or not message or len(message) > 500:
        raise SchemaError("report.statusMessage is invalid")
    papers = value["papers"]
    if not isinstance(papers, list):
        raise SchemaError("report.papers must be a list")
    for paper in papers:
        validate_publication(paper)


def _validate_archive_index(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise SchemaError("archive index must be an object")
    _require_exact_keys(value, ARCHIVE_INDEX_FIELDS, "archive index")
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise SchemaError("unsupported archive schemaVersion")
    reports = value["reports"]
    if not isinstance(reports, list):
        raise SchemaError("archive reports must be a list")
    for report in reports:
        if not isinstance(report, Mapping):
            raise SchemaError("archive report must be an object")
        _require_exact_keys(report, ARCHIVE_REPORT_FIELDS, "archive report")
        _validate_date_string(report["date"], "archive report date")
        if report["path"] != f"{report['date']}.json":
            raise SchemaError("archive report path must be a local dated filename")
        if report["status"] not in STATUSES:
            raise SchemaError("archive report status is invalid")
        if not _is_int(report["paperCount"]) or report["paperCount"] < 0:
            raise SchemaError("archive report paperCount is invalid")


def _normalise_config_list(
    value: object, field: str, validator: Callable[[str], bool]
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError(f"{field} must be a non-empty list")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ConfigurationError(f"{field} values must be strings")
        item = _normalise_space(item)
        key = item.casefold()
        if not item or len(item) > 80 or not validator(item):
            raise ConfigurationError(f"{field} contains an invalid value")
        if key in seen:
            raise ConfigurationError(f"{field} contains a duplicate value")
        seen.add(key)
        cleaned.append(item)
    return tuple(sorted(cleaned, key=lambda item: (item.casefold(), item)))


def config_from_mapping(value: Mapping[str, Any]) -> DigestConfig:
    """Parse the documented configuration with strict unknown-key rejection."""

    if not isinstance(value, Mapping):
        raise ConfigurationError("topic configuration must be an object")
    unknown = set(value) - CONFIG_FIELDS
    private = {
        key for key in value if isinstance(key, str) and key.startswith("_")
    }
    if unknown or private:
        raise ConfigurationError("topic configuration contains unknown fields")
    categories = _normalise_config_list(
        value.get("categories", list(DEFAULT_CATEGORIES)),
        "categories",
        lambda item: CATEGORY_RE.fullmatch(item) is not None,
    )
    keywords = _normalise_config_list(
        value.get("keywords", list(DEFAULT_KEYWORDS)),
        "keywords",
        lambda item: '"' not in item and all(ord(char) >= 32 for char in item),
    )
    minimum_score = value.get("minimumScore", 6)
    max_results = value.get("maxResults", 100)
    stale_after_days = value.get("staleAfterDays", 4)
    if not _is_int(minimum_score) or not 1 <= minimum_score <= 100:
        raise ConfigurationError("minimumScore must be an integer from 1 to 100")
    if not _is_int(max_results) or not 1 <= max_results <= 2_000:
        raise ConfigurationError("maxResults must be an integer from 1 to 2000")
    if not _is_int(stale_after_days) or not 0 <= stale_after_days <= 14:
        raise ConfigurationError(
            "staleAfterDays must be an integer from 0 to 14"
        )
    return DigestConfig(
        categories=categories,
        keywords=keywords,
        minimum_score=minimum_score,
        max_results=max_results,
        stale_after_days=stale_after_days,
    )


def load_config(path: Path | None) -> DigestConfig:
    """Load a topic configuration, using safe defaults when it is absent."""

    if path is None or not path.exists():
        return config_from_mapping({})
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"cannot read topic configuration: {path}"
        ) from exc
    return config_from_mapping(value)


def build_search_query(config: DigestConfig) -> str:
    categories = " OR ".join(
        f"cat:{category}" for category in config.categories
    )
    terms = " OR ".join(f'all:"{keyword}"' for keyword in config.keywords)
    return f"({categories}) OR ({terms})"


def build_query_url(
    config: DigestConfig, endpoint: str = API_ENDPOINT
) -> str:
    """Build a fixed-host arXiv API URL sorted by submitted date."""

    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("invalid API endpoint") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "export.arxiv.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path != "/api/query"
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "API endpoint must be the HTTPS arXiv query endpoint"
        )
    parameters = urllib.parse.urlencode(
        {
            "search_query": build_search_query(config),
            "start": 0,
            "max_results": config.max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    return f"{endpoint}?{parameters}"


def fetch_atom_xml(
    url: str,
    timeout: float = 20.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> bytes:
    """Fetch a bounded Atom response using a non-identifying user agent."""

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/atom+xml",
            "User-Agent": USER_AGENT,
        },
    )
    with opener(request, timeout=timeout) as response:
        body = response.read(MAX_FEED_BYTES + 1)
    if not isinstance(body, bytes):
        raise FeedParseError("arXiv response was not bytes")
    if len(body) > MAX_FEED_BYTES:
        raise FeedParseError("Atom feed exceeds the size limit")
    return body


def _required_text(element: ET.Element, child_name: str) -> str:
    child = element.find(f"{ATOM}{child_name}")
    text = (
        ""
        if child is None
        else _normalise_space("".join(child.itertext()))
    )
    if not text:
        raise FeedParseError(f"Atom entry has no {child_name}")
    return text


def _extract_arxiv_id(source_id: str) -> str:
    """Accept the API's historical HTTP id but never expose that URL."""

    try:
        parsed = urllib.parse.urlsplit(source_id)
        port = parsed.port
    except ValueError as exc:
        raise FeedParseError("Atom entry has an unsafe arXiv id URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "arxiv.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise FeedParseError("Atom entry has an unsafe arXiv id URL")
    match = re.fullmatch(r"/abs/(.+)", parsed.path)
    candidate = match.group(1) if match else ""
    if not ARXIV_ID_RE.fullmatch(candidate):
        raise FeedParseError("Atom entry has an invalid arXiv id")
    return candidate


def parse_atom_feed(xml: bytes | str) -> AtomFeed:
    """Parse a bounded Atom feed without accepting DTDs or entities."""

    if isinstance(xml, str):
        raw = xml.encode("utf-8")
    elif isinstance(xml, bytes):
        raw = xml
    else:
        raise TypeError("xml must be bytes or text")
    if len(raw) > MAX_FEED_BYTES:
        raise FeedParseError("Atom feed exceeds the size limit")
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise FeedParseError("DTD and entity declarations are forbidden")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FeedParseError("response is not well-formed XML") from exc
    if root.tag != f"{ATOM}feed":
        raise FeedParseError("response is not an Atom feed")

    updated_text = root.findtext(f"{ATOM}updated")
    feed_updated = (
        _parse_datetime(updated_text, "feed updated")
        if updated_text and updated_text.strip()
        else None
    )
    entries: list[AtomEntry] = []
    seen_ids: set[str] = set()
    for element in root.findall(f"{ATOM}entry"):
        arxiv_id = _extract_arxiv_id(_required_text(element, "id"))
        if arxiv_id.casefold() in seen_ids:
            continue
        title = _required_text(element, "title")
        abstract = _required_text(element, "summary")
        submitted_at = _parse_datetime(
            _required_text(element, "published"), "published"
        )
        updated_at = _parse_datetime(
            _required_text(element, "updated"), "updated"
        )
        authors = tuple(
            _normalise_space(author.findtext(f"{ATOM}name") or "")
            for author in element.findall(f"{ATOM}author")
        )
        authors = tuple(author for author in authors if author)
        if not authors:
            raise FeedParseError("Atom entry has no public author name")
        categories = tuple(
            sorted(
                {
                    term
                    for category in element.findall(f"{ATOM}category")
                    if (
                        term := _normalise_space(category.get("term", ""))
                    )
                },
                key=lambda item: (item.casefold(), item),
            )
        )
        entries.append(
            AtomEntry(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                submitted_at=submitted_at,
                updated_at=updated_at,
                categories=categories,
                abstract=abstract,
            )
        )
        seen_ids.add(arxiv_id.casefold())
    entries.sort(
        key=lambda entry: (
            -entry.submitted_at.timestamp(),
            entry.arxiv_id.casefold(),
        )
    )
    return AtomFeed(feed_updated, tuple(entries))


def keyword_weight(keyword: str) -> int:
    """Return a stable weight for a built-in or custom keyword."""

    known = KEYWORD_WEIGHTS.get(keyword.casefold())
    if known is not None:
        return known
    return min(10, 5 + max(0, len(keyword.split()) - 1) * 2)


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    parts = [
        re.escape(part)
        for part in re.split(r"[\s-]+", keyword)
        if part
    ]
    phrase = r"(?:[\s-]+)".join(parts)
    return re.compile(rf"(?<!\w){phrase}(?!\w)", re.IGNORECASE)


def score_entry(
    entry: AtomEntry, config: DigestConfig
) -> tuple[int, list[str], list[str]]:
    """Return a deterministic score, topic list, and explanation list."""

    total = 0
    topics: list[str] = []
    reasons: list[str] = []
    for keyword in sorted(
        config.keywords, key=lambda item: (item.casefold(), item)
    ):
        pattern = _keyword_pattern(keyword)
        in_title = pattern.search(entry.title) is not None
        in_abstract = pattern.search(entry.abstract) is not None
        if not in_title and not in_abstract:
            continue
        topics.append(keyword)
        weight = keyword_weight(keyword)
        if in_title:
            increment = weight * 2
            total += increment
            reasons.append(
                f'title matches "{keyword}" (+{increment})'
            )
        if in_abstract:
            total += weight
            reasons.append(
                f'abstract matches "{keyword}" (+{weight})'
            )

    configured = {item.casefold() for item in config.categories}
    matched_categories = sorted(
        (
            item
            for item in entry.categories
            if item.casefold() in configured
        ),
        key=lambda item: (item.casefold(), item),
    )
    for category in matched_categories[:2]:
        total += 2
        reasons.append(f'category "{category}" (+2)')
    return min(total, 100), topics, reasons


def publication_from_entry(
    entry: AtomEntry, config: DigestConfig
) -> dict[str, Any]:
    """Create one allow-listed public paper, deriving links from its id."""

    score, topics, reasons = score_entry(entry, config)
    publication: dict[str, Any] = {
        "arxivId": entry.arxiv_id,
        "title": entry.title,
        "authors": list(entry.authors),
        "submittedDate": entry.submitted_at.date().isoformat(),
        "updatedDate": entry.updated_at.date().isoformat(),
        "topics": topics,
        "score": score,
        "scoreReasons": reasons,
        "abstract": entry.abstract,
        "absUrl": f"https://arxiv.org/abs/{entry.arxiv_id}",
        "pdfUrl": f"https://arxiv.org/pdf/{entry.arxiv_id}",
    }
    validate_publication(publication)
    return publication


def expected_batch_date(checked_at: datetime) -> date:
    """Return the most recent weekday on or before the UTC check date."""

    if checked_at.tzinfo is None:
        raise ValueError("checked_at must be timezone-aware")
    result = checked_at.astimezone(timezone.utc).date()
    while result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def _base_report(
    checked_at: datetime, expected: date
) -> dict[str, Any]:
    if checked_at.tzinfo is None:
        raise ValueError("checked_at must be timezone-aware")
    checked = checked_at.astimezone(timezone.utc)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _format_utc(datetime.now(timezone.utc)),
        "checkedAt": _format_utc(checked),
        "expectedBatchDate": expected.isoformat(),
        "observedBatchDate": None,
        "status": UPDATE_NOT_CONFIRMED,
        "statusMessage": "The arXiv update could not be confirmed.",
        "papers": [],
    }


def report_from_feed(
    feed: AtomFeed,
    config: DigestConfig,
    checked_at: datetime,
    expected: date | None = None,
) -> dict[str, Any]:
    """Classify a validated feed without conflating stale and zero-hit states."""

    expected = expected or expected_batch_date(checked_at)
    report = _base_report(checked_at, expected)
    if not feed.entries:
        report["statusMessage"] = (
            "The Atom feed returned no entries, so batch freshness "
            "could not be confirmed."
        )
        validate_report(report)
        return report

    observed = max(entry.submitted_at.date() for entry in feed.entries)
    report["observedBatchDate"] = observed.isoformat()
    batch_entries = [
        entry
        for entry in feed.entries
        if entry.submitted_at.date() == observed
    ]
    papers = [
        publication_from_entry(entry, config)
        for entry in batch_entries
    ]
    papers = [
        paper
        for paper in papers
        if paper["score"] >= config.minimum_score
    ]
    papers.sort(
        key=lambda paper: (
            -date.fromisoformat(paper["submittedDate"]).toordinal(),
            -paper["score"],
            paper["arxivId"].casefold(),
        )
    )
    report["papers"] = papers

    age = (expected - observed).days
    if age > config.stale_after_days:
        report["status"] = UPDATE_NOT_CONFIRMED
        report["statusMessage"] = (
            f"The newest feed submission is {age} days behind the "
            "expected batch date; this is not a confirmed no-results "
            "update."
        )
    elif observed > expected + timedelta(days=1):
        report["status"] = UPDATE_NOT_CONFIRMED
        report["statusMessage"] = (
            "The feed contains a submission date beyond the expected "
            "batch window."
        )
    elif papers:
        report["status"] = UPDATE_CONFIRMED
        suffix = "." if len(papers) == 1 else "s."
        report["statusMessage"] = (
            f"Update confirmed with {len(papers)} relevant paper{suffix}"
        )
    else:
        report["status"] = NO_RELEVANT_PAPERS
        report["statusMessage"] = (
            "The batch was checked successfully and contained no "
            "papers above the relevance threshold."
        )
    validate_report(report)
    return report


def _unconfirmed_report(
    checked_at: datetime, expected: date, message: str
) -> dict[str, Any]:
    report = _base_report(checked_at, expected)
    report["status"] = UPDATE_NOT_CONFIRMED
    report["statusMessage"] = message
    validate_report(report)
    return report


def generate_report(
    config: DigestConfig,
    *,
    checked_at: datetime | None = None,
    expected: date | None = None,
    timeout: float = 20.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Fetch and classify one update without writing files."""

    checked = checked_at or datetime.now(timezone.utc)
    if checked.tzinfo is None:
        raise ValueError("checked_at must be timezone-aware")
    checked = checked.astimezone(timezone.utc)
    expected = expected or expected_batch_date(checked)
    url = build_query_url(config)
    try:
        xml = fetch_atom_xml(url, timeout=timeout, opener=opener)
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        TimeoutError,
        OSError,
    ):
        report = _base_report(checked, expected)
        report["status"] = UPDATER_OFFLINE
        report["statusMessage"] = (
            "The updater could not reach the arXiv API; no result "
            "claim was made."
        )
        validate_report(report)
        return report
    except FeedParseError:
        return _unconfirmed_report(
            checked,
            expected,
            "The arXiv response could not be validated as a complete "
            "Atom feed.",
        )
    try:
        feed = parse_atom_feed(xml)
    except FeedParseError:
        return _unconfirmed_report(
            checked,
            expected,
            "The arXiv response could not be validated as a complete "
            "Atom feed.",
        )
    return report_from_feed(feed, config, checked, expected)


def _atomic_write_json(
    path: Path, value: Mapping[str, Any]
) -> None:
    """Replace one JSON file atomically after flushing it to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
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


def _read_archive_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schemaVersion": SCHEMA_VERSION, "reports": []}
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(
            "existing archive index is not readable JSON"
        ) from exc
    _validate_archive_index(value)
    return dict(value)


def persist_report(
    report: Mapping[str, Any],
    output: Path,
    archive_dir: Path,
) -> None:
    """Atomically write the archive, index, and then latest report.

    The existing index is validated before any write. The latest report is
    replaced last, so it never points ahead of its corresponding archive.
    """

    validate_report(report)
    archive_date = report["expectedBatchDate"]
    archive_name = f"{archive_date}.json"
    archive_path = archive_dir / archive_name
    index_path = archive_dir / "index.json"
    existing = _read_archive_index(index_path)
    entry = {
        "date": archive_date,
        "path": archive_name,
        "status": report["status"],
        "paperCount": len(report["papers"]),
    }
    reports = [
        item
        for item in existing["reports"]
        if item["date"] != archive_date
    ]
    reports.append(entry)
    reports.sort(key=lambda item: item["date"], reverse=True)
    index = {"schemaVersion": SCHEMA_VERSION, "reports": reports}
    _validate_archive_index(index)

    _atomic_write_json(archive_path, report)
    _atomic_write_json(index_path, index)
    _atomic_write_json(output, report)


def _parse_date_argument(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD")
    return parsed


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("site/data/latest.json"),
        help="latest JSON path",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("site/data/archive"),
        help="dated archive directory",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/topics.json"),
        help="optional topic configuration JSON",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="arXiv request timeout in seconds",
    )
    parser.add_argument(
        "--expected-batch-date",
        type=_parse_date_argument,
        help="override expected UTC batch date (YYYY-MM-DD)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 120:
        parser.error(
            "--timeout must be greater than 0 and no more than 120"
        )
    try:
        config = load_config(args.config)
        report = generate_report(
            config,
            expected=args.expected_batch_date,
            timeout=args.timeout,
        )
        persist_report(report, args.output, args.archive_dir)
    except (ConfigurationError, SchemaError, OSError) as exc:
        print(f"arxiv_digest: {exc}", file=sys.stderr)
        return 1
    print(
        f"{report['status']}: wrote {len(report['papers'])} "
        f"paper(s) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
