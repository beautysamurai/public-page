#!/usr/bin/env python3
"""Publish a completed automated-research report into the public v2 history.

Research reports are untrusted input.  This adapter accepts one exact report
shape, maps only allowlisted fields, renders deterministic bilingual editorial
text, and can reconcile every durable completed daily report.  It validates the
complete candidate bundle before changing either public source file. Existing
editions are immutable except for a narrowly checked presentation refresh of
pipeline-managed editions; reusing an edition ID with different identity or
summary content is an error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from import_scheduler_history import (
    ARXIV_ID_RE,
    HistoryImportError,
    _parse_imported_at,
    _validate_public_text,
    generate_artifacts,
    load_history,
    persist_artifacts,
    validate_history,
)
from research_language import (
    contains_english_prose,
    contains_japanese_characters,
    contains_japanese_prose,
    contains_latin_characters,
)
from validate_public_bundle import (
    PublicBundleError,
    load_translation,
    validate_bundle,
    validate_translation,
)


REPORT_SCHEMA_VERSION = 2
MAX_REPORT_BYTES = 16 * 1024 * 1024
SOURCE_KIND = "openai-responses-api"
SOURCE_LABEL = "OpenAI Responses API · automated research pipeline"

REPORT_FIELDS = frozenset(
    {
        "schemaVersion",
        "reportKind",
        "reportDate",
        "generatedAt",
        "status",
        "message",
        "expectedBatchDate",
        "observedBatchDate",
        "periodStart",
        "periodEnd",
        "papers",
    }
)
PAPER_FIELDS = frozenset({"metadata", "finalAnalysis"})
METADATA_FIELDS = frozenset(
    {
        "arxivId",
        "title",
        "authors",
        "submittedDate",
        "updatedDate",
        "categories",
    }
)
ANALYSIS_FIELDS = frozenset(
    {
        "classification",
        "summary",
        "mainResult",
        "practicalApplication",
        "methodology",
        "limitations",
        "importance",
        "recommended",
        "reason",
        "tags",
        "english",
    }
)
ENGLISH_FIELDS = frozenset(
    {
        "classification",
        "summary",
        "mainResult",
        "practicalApplication",
        "methodology",
        "limitations",
        "reason",
        "tags",
    }
)
REPORT_KINDS = frozenset({"daily", "weekly", "monthly"})
CLASSIFICATIONS = frozenset(
    {
        "electronic_trading",
        "market_microstructure",
        "interest_rate_models",
        "yield_curve",
        "rates",
        "mixed",
        "out_of_scope",
    }
)
PUBLIC_TOPIC_BY_CLASSIFICATION = {
    "electronic_trading": "Electronic trading",
    "market_microstructure": "Market microstructure",
    "interest_rate_models": "Interest-rate models",
    "yield_curve": "Yield curves",
    "rates": "Rates",
    "mixed": "Cross-disciplinary finance",
    "out_of_scope": "Out of scope",
}
REPORT_STATUSES = frozenset(
    {
        "UPDATE_CONFIRMED",
        "NO_RELEVANT_PAPERS",
        "NO_NEW_BATCH_EXPECTED",
        "UPDATE_NOT_CONFIRMED",
        "UPDATER_OFFLINE",
    }
)
INCOMPLETE_REPORT_STATUSES = frozenset(
    {"UPDATE_NOT_CONFIRMED", "UPDATER_OFFLINE"}
)
MAX_DAILY_REPORT_FILES = 5_000

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
WHITESPACE_RE = re.compile(r"\s+")
MARKDOWN_ESCAPE_RE = re.compile(r"([\\`*_\[\]#!|])")
TEX_MARKDOWN_RE = re.compile(r"(\$\$[^$]+\$\$|\$[^$\n]+\$)")
ARXIV_METADATA_CATEGORY_RE = re.compile(
    r"(?:astro-ph|cond-mat|cs|econ|eess|math|nlin|physics|q-bio|q-fin|stat)"
    r"\.[A-Za-z0-9-]+"
    r"|(?:astro-ph|gr-qc|hep-ex|hep-lat|hep-ph|hep-th|math-ph|nucl-ex|"
    r"nucl-th|quant-ph)"
)

JA_STATUS_MESSAGES = {
    "UPDATE_CONFIRMED": "arXivの更新を確認し、対象論文を評価しました。",
    "NO_RELEVANT_PAPERS": (
        "arXivの更新を確認しましたが、今回の条件に合う推薦論文は"
        "ありませんでした。"
    ),
    "NO_NEW_BATCH_EXPECTED": (
        "通常の公開スケジュール上、新しいarXivバッチは予定されていません。"
    ),
    "UPDATE_NOT_CONFIRMED": (
        "予定されたarXivバッチの反映を確認できないため、レビューは完了扱いにせず"
        "翌日に持ち越します。"
    ),
    "UPDATER_OFFLINE": (
        "arXivの更新状況を確認できないため、レビューは完了扱いにせず翌日に"
        "持ち越します。"
    ),
    "WEEKLY_REVIEW": "対象期間の週次レビューを生成しました。",
    "MONTHLY_REVIEW": "対象期間の月次レビューを生成しました。",
}
EN_STATUS_MESSAGES = {
    "UPDATE_CONFIRMED": (
        "The arXiv update was confirmed and the qualifying papers were assessed."
    ),
    "NO_RELEVANT_PAPERS": (
        "The arXiv update was confirmed, but no papers met the recommendation "
        "criteria."
    ),
    "NO_NEW_BATCH_EXPECTED": (
        "No new arXiv batch was expected under the normal announcement schedule."
    ),
    "UPDATE_NOT_CONFIRMED": (
        "The expected arXiv batch could not be confirmed, so this review remains "
        "incomplete and is carried forward."
    ),
    "UPDATER_OFFLINE": (
        "The arXiv update status could not be checked, so this review remains "
        "incomplete and is carried forward."
    ),
    "WEEKLY_REVIEW": "The weekly review for the selected period was generated.",
    "MONTHLY_REVIEW": "The monthly review for the selected period was generated.",
}

JA_KIND_LABELS = {
    "daily": "日次レビュー",
    "weekly": "週次レビュー",
    "monthly": "月次レビュー",
}
EN_KIND_LABELS = {
    "daily": "Daily review",
    "weekly": "Weekly review",
    "monthly": "Monthly review",
}
ENGLISH_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class ResearchPublicationError(RuntimeError):
    """Base class for expected report-adaptation and publication failures."""


class ResearchReportSchemaError(ResearchPublicationError):
    """The completed research report does not match the exact safe contract."""


class PublicationConflictError(ResearchPublicationError):
    """An immutable edition ID already exists with different public content."""


@dataclass(frozen=True)
class AdaptedPublication:
    source_edition: dict[str, Any]
    english_edition: dict[str, Any]


@dataclass(frozen=True)
class PublicationResult:
    edition_id: str
    changed: bool
    generated_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ReconciliationResult:
    report_count: int
    completed_count: int
    published_edition_ids: tuple[str, ...]
    refreshed_edition_ids: tuple[str, ...]
    existing_edition_ids: tuple[str, ...]
    incomplete_count: int
    generated_paths: tuple[Path, ...] = ()


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResearchReportSchemaError(
                f"research report contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchReportSchemaError(f"{context} must be an object")
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ResearchReportSchemaError(
            f"{context} has invalid fields; missing={missing}, unknown={unknown}"
        )
    return value


def _safe_plain_text(
    value: object,
    context: str,
    *,
    maximum: int,
    english: bool = False,
    japanese: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ResearchReportSchemaError(f"{context} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    normalized = WHITESPACE_RE.sub(" ", normalized.strip())
    if not normalized or len(normalized) > maximum:
        raise ResearchReportSchemaError(f"{context} has an invalid length")
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co"}
        for character in normalized
    ):
        raise ResearchReportSchemaError(
            f"{context} contains a control, private-use, or formatting character"
        )
    if english and not contains_english_prose(normalized):
        raise ResearchReportSchemaError(f"{context} must contain English text")
    if japanese and not contains_japanese_prose(normalized):
        raise ResearchReportSchemaError(f"{context} must contain Japanese text")
    try:
        _validate_public_text(normalized, context, maximum=maximum)
    except HistoryImportError as exc:
        raise ResearchReportSchemaError(str(exc)) from exc
    return normalized


def _safe_string_list(
    value: object,
    context: str,
    *,
    maximum_items: int,
    maximum_string: int,
    english: bool = False,
) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > maximum_items
    ):
        raise ResearchReportSchemaError(
            f"{context} must be a non-empty bounded list"
        )
    result = [
        _safe_plain_text(
            item,
            f"{context}[{index}]",
            maximum=maximum_string,
        )
        for index, item in enumerate(value)
    ]
    if english:
        for index, item in enumerate(result):
            if (
                contains_japanese_characters(item)
                or not contains_latin_characters(item)
            ):
                raise ResearchReportSchemaError(
                    f"{context}[{index}] must contain English text"
                )
    folded = [item.casefold() for item in result]
    if len(folded) != len(set(folded)):
        raise ResearchReportSchemaError(f"{context} contains duplicate values")
    return result


def _safe_arxiv_category_list(value: object, context: str) -> list[str]:
    """Validate arXiv taxonomy tokens without treating ``cs.AI`` as a URL."""

    if not isinstance(value, list) or not value or len(value) > 50:
        raise ResearchReportSchemaError(
            f"{context} must be a non-empty bounded list"
        )
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ResearchReportSchemaError(f"{context}[{index}] must be a string")
        normalized = unicodedata.normalize("NFC", item)
        if (
            normalized != item
            or len(normalized) > 120
            or not ARXIV_METADATA_CATEGORY_RE.fullmatch(normalized)
        ):
            raise ResearchReportSchemaError(
                f"{context}[{index}] is not a valid arXiv category"
            )
        result.append(normalized)
    folded = [item.casefold() for item in result]
    if len(folded) != len(set(folded)):
        raise ResearchReportSchemaError(f"{context} contains duplicate values")
    return result


def _safe_date(
    value: object,
    context: str,
    *,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ResearchReportSchemaError(f"{context} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ResearchReportSchemaError(
            f"{context} must be a calendar date"
        ) from exc
    if parsed.isoformat() != value:
        raise ResearchReportSchemaError(
            f"{context} must be canonical YYYY-MM-DD"
        )
    return value


def _safe_timestamp(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ResearchReportSchemaError(
            f"{context} must be a timezone-aware RFC 3339 timestamp"
        )
    try:
        _parse_imported_at(value, context)
    except HistoryImportError as exc:
        raise ResearchReportSchemaError(str(exc)) from exc
    return value


def _validate_analysis(
    value: object,
    context: str,
) -> dict[str, Any]:
    analysis = _require_exact_keys(value, ANALYSIS_FIELDS, context)
    english_value = _require_exact_keys(
        analysis["english"], ENGLISH_FIELDS, f"{context}.english"
    )

    narrative_limits = {
        "summary": 10_000,
        "mainResult": 10_000,
        "practicalApplication": 10_000,
        "methodology": 10_000,
        "limitations": 10_000,
        "reason": 5_000,
    }
    classification = _safe_plain_text(
        analysis["classification"],
        f"{context}.classification",
        maximum=500,
    )
    if classification not in CLASSIFICATIONS:
        raise ResearchReportSchemaError(f"{context}.classification is invalid")
    english_classification = _safe_plain_text(
        english_value["classification"],
        f"{context}.english.classification",
        maximum=500,
    )
    if english_classification != classification:
        raise ResearchReportSchemaError(
            f"{context}.english.classification must exactly match classification"
        )

    validated: dict[str, Any] = {
        "classification": classification,
        **{
            field: _safe_plain_text(
                analysis[field],
                f"{context}.{field}",
                maximum=maximum,
                japanese=True,
            )
            for field, maximum in narrative_limits.items()
        },
    }
    english_analysis: dict[str, Any] = {
        "classification": english_classification,
        **{
            field: _safe_plain_text(
                english_value[field],
                f"{context}.english.{field}",
                maximum=maximum,
                english=True,
            )
            for field, maximum in narrative_limits.items()
        },
    }

    importance = analysis["importance"]
    if (
        type(importance) is not int
        or importance < 1
        or importance > 5
    ):
        raise ResearchReportSchemaError(
            f"{context}.importance must be an integer from 1 through 5"
        )
    recommended = analysis["recommended"]
    if type(recommended) is not bool:
        raise ResearchReportSchemaError(f"{context}.recommended must be boolean")

    tags = _safe_string_list(
        analysis["tags"],
        f"{context}.tags",
        maximum_items=12,
        maximum_string=120,
    )
    english_tags = _safe_string_list(
        english_value["tags"],
        f"{context}.english.tags",
        maximum_items=12,
        maximum_string=120,
        english=True,
    )
    validated.update(
        {
            "importance": importance,
            "recommended": recommended,
            "tags": tags,
            "english": {**english_analysis, "tags": english_tags},
        }
    )
    return validated


def _validate_paper(value: object, index: int) -> dict[str, Any]:
    context = f"research report papers[{index}]"
    paper = _require_exact_keys(value, PAPER_FIELDS, context)
    metadata = _require_exact_keys(
        paper["metadata"], METADATA_FIELDS, f"{context}.metadata"
    )

    arxiv_id = metadata["arxivId"]
    if not isinstance(arxiv_id, str) or not ARXIV_ID_RE.fullmatch(arxiv_id):
        raise ResearchReportSchemaError(f"{context}.metadata.arxivId is invalid")
    arxiv_id = _safe_plain_text(
        arxiv_id,
        f"{context}.metadata.arxivId",
        maximum=80,
    )
    title = _safe_plain_text(
        metadata["title"],
        f"{context}.metadata.title",
        maximum=500,
    )
    authors = _safe_string_list(
        metadata["authors"],
        f"{context}.metadata.authors",
        maximum_items=100,
        maximum_string=300,
    )
    submitted_date = _safe_date(
        metadata["submittedDate"], f"{context}.metadata.submittedDate"
    )
    updated_date = _safe_date(
        metadata["updatedDate"], f"{context}.metadata.updatedDate"
    )
    assert submitted_date is not None and updated_date is not None
    if date.fromisoformat(updated_date) < date.fromisoformat(submitted_date):
        raise ResearchReportSchemaError(
            f"{context}.metadata.updatedDate cannot precede submittedDate"
        )
    categories = _safe_arxiv_category_list(
        metadata["categories"],
        f"{context}.metadata.categories",
    )

    return {
        "metadata": {
            "arxivId": arxiv_id,
            "title": title,
            "authors": authors,
            "submittedDate": submitted_date,
            "updatedDate": updated_date,
            "categories": categories,
        },
        "finalAnalysis": _validate_analysis(
            paper["finalAnalysis"], f"{context}.finalAnalysis"
        ),
    }


def validate_research_report(value: object) -> dict[str, Any]:
    """Validate and normalize one completed pipeline report."""

    report = _require_exact_keys(value, REPORT_FIELDS, "research report")
    schema_version = report["schemaVersion"]
    if type(schema_version) is not int or schema_version != REPORT_SCHEMA_VERSION:
        raise ResearchReportSchemaError(
            f"research report schemaVersion must be integer {REPORT_SCHEMA_VERSION}"
        )

    report_kind = report["reportKind"]
    if not isinstance(report_kind, str) or report_kind not in REPORT_KINDS:
        raise ResearchReportSchemaError("research report reportKind is invalid")
    report_date = _safe_date(report["reportDate"], "research report reportDate")
    generated_at = _safe_timestamp(
        report["generatedAt"], "research report generatedAt"
    )
    status = report["status"]
    if not isinstance(status, str) or status not in REPORT_STATUSES:
        raise ResearchReportSchemaError("research report status is invalid")
    message = _safe_plain_text(
        report["message"], "research report message", maximum=2_000
    )

    expected_batch_date = _safe_date(
        report["expectedBatchDate"],
        "research report expectedBatchDate",
        nullable=True,
    )
    observed_batch_date = _safe_date(
        report["observedBatchDate"],
        "research report observedBatchDate",
        nullable=True,
    )
    period_start = _safe_date(
        report["periodStart"], "research report periodStart", nullable=True
    )
    period_end = _safe_date(
        report["periodEnd"], "research report periodEnd", nullable=True
    )

    if report_kind == "daily":
        if (
            expected_batch_date is None
            or period_start is not None
            or period_end is not None
        ):
            raise ResearchReportSchemaError(
                "daily research reports require expectedBatchDate and null periods"
            )
    else:
        if expected_batch_date is not None or observed_batch_date is not None:
            raise ResearchReportSchemaError(
                "aggregate research reports require null batch dates"
            )
        if period_start is None or period_end is None:
            raise ResearchReportSchemaError(
                "aggregate research reports require periodStart and periodEnd"
            )
        if report_date != period_end:
            raise ResearchReportSchemaError(
                "aggregate research reportDate must equal periodEnd"
            )
    if (
        period_start is not None
        and period_end is not None
        and date.fromisoformat(period_end) < date.fromisoformat(period_start)
    ):
        raise ResearchReportSchemaError(
            "research report periodEnd cannot precede periodStart"
        )

    papers_value = report["papers"]
    if not isinstance(papers_value, list) or len(papers_value) > 2_000:
        raise ResearchReportSchemaError(
            "research report papers must be a bounded list"
        )
    papers = [_validate_paper(paper, index) for index, paper in enumerate(papers_value)]
    paper_ids = [
        re.sub(r"v\d+$", "", paper["metadata"]["arxivId"], flags=re.IGNORECASE)
        .casefold()
        for paper in papers
    ]
    if len(paper_ids) != len(set(paper_ids)):
        raise ResearchReportSchemaError(
            "research report papers contain duplicate arXiv IDs"
        )

    statuses_requiring_no_papers = {
        "NO_RELEVANT_PAPERS",
        "NO_NEW_BATCH_EXPECTED",
        "UPDATER_OFFLINE",
    }
    if status in statuses_requiring_no_papers and papers:
        raise ResearchReportSchemaError(
            f"research report status {status} cannot contain papers"
        )
    if status == "UPDATE_NOT_CONFIRMED" and report_kind == "daily" and papers:
        raise ResearchReportSchemaError(
            "daily UPDATE_NOT_CONFIRMED research reports cannot contain papers"
        )
    if status == "UPDATE_CONFIRMED" and not papers:
        raise ResearchReportSchemaError(
            "UPDATE_CONFIRMED research reports must contain at least one paper"
        )

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "reportKind": report_kind,
        "reportDate": report_date,
        "generatedAt": generated_at,
        "status": status,
        "message": message,
        "expectedBatchDate": expected_batch_date,
        "observedBatchDate": observed_batch_date,
        "periodStart": period_start,
        "periodEnd": period_end,
        "papers": papers,
    }


def load_research_report(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ResearchPublicationError(f"cannot read research report: {path}") from exc
    if len(raw) > MAX_REPORT_BYTES:
        raise ResearchReportSchemaError(
            f"research report exceeds the {MAX_REPORT_BYTES}-byte limit"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResearchReportSchemaError("research report must be UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise ResearchReportSchemaError("research report is not valid JSON") from exc
    return validate_research_report(value)


def _public_status(report_kind: str, report_status: str) -> str:
    if report_status == "UPDATE_CONFIRMED" and report_kind == "weekly":
        return "WEEKLY_REVIEW"
    if report_status == "UPDATE_CONFIRMED" and report_kind == "monthly":
        return "MONTHLY_REVIEW"
    return report_status


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        folded = value.casefold()
        if folded not in seen:
            result.append(value)
            seen.add(folded)
    return result


def _markdown_text(value: str) -> str:
    """Escape model-controlled Markdown and remove HTML-shaped angle brackets."""

    value = value.replace("<", "‹").replace(">", "›")
    return "".join(
        part
        if TEX_MARKDOWN_RE.fullmatch(part)
        else MARKDOWN_ESCAPE_RE.sub(r"\\\1", part)
        for part in TEX_MARKDOWN_RE.split(value)
    )


def _ja_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _en_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{ENGLISH_MONTHS[parsed.month - 1]} {parsed.day}, {parsed.year}"


def _render_source_text(
    report: Mapping[str, Any],
    *,
    english: bool,
    public_status: str,
) -> str:
    kind = report["reportKind"]
    report_date = report["reportDate"]
    if english:
        heading = f"## {_en_date(report_date)} — {EN_KIND_LABELS[kind]}"
        status_message = EN_STATUS_MESSAGES[public_status]
    else:
        heading = f"## {_ja_date(report_date)} — {JA_KIND_LABELS[kind]}"
        status_message = JA_STATUS_MESSAGES[public_status]
    parts = [heading, "", status_message]

    papers = report["papers"]
    if not papers:
        parts.extend(
            [
                "",
                (
                    "**Recommended papers: None**"
                    if english
                    else "**推薦論文: なし**"
                ),
            ]
        )
        return "\n".join(parts)

    for index, paper in enumerate(papers, start=1):
        metadata = paper["metadata"]
        analysis = paper["finalAnalysis"]
        localized = analysis["english"] if english else analysis
        public_importance = analysis["importance"] * 2
        recommended = analysis["recommended"]
        recommendation = (
            ("Recommended" if recommended else "Not recommended")
            if english
            else ("推奨" if recommended else "非推奨")
        )
        authors = ", ".join(_markdown_text(author) for author in metadata["authors"])
        topic = _markdown_text(localized["tags"][0])
        parts.extend(
            [
                "",
                f"## {index}. {_markdown_text(metadata['title'])}",
                "",
                (
                    f"**[arXiv:{metadata['arxivId']}]"
                    f"(https://arxiv.org/abs/{metadata['arxivId']}) — {authors}**"
                ),
                "",
                (
                    f"**Importance: {public_importance}/10 — {recommendation} · {topic}**"
                    if english
                    else f"**重要度: {public_importance}/10 — {recommendation}・{topic}**"
                ),
                "",
                _markdown_text(localized["summary"]),
                "",
                (
                    f"{_markdown_text(localized['methodology'])} "
                    f"{_markdown_text(localized['mainResult'])}"
                ),
                "",
                (
                    f"{_markdown_text(localized['practicalApplication'])} "
                    f"{_markdown_text(localized['limitations'])}"
                ),
                "",
                _markdown_text(localized["reason"]),
            ]
        )
    return "\n".join(parts)


def adapt_research_report(value: object) -> AdaptedPublication:
    """Map exactly one validated research report to the two public schemas."""

    report = validate_research_report(value)
    kind = report["reportKind"]
    report_date = report["reportDate"]
    edition_id = f"{report_date}-{kind}-openai-01"
    public_status = _public_status(kind, report["status"])
    ja_message = JA_STATUS_MESSAGES[public_status]
    en_message = EN_STATUS_MESSAGES[public_status]

    source_papers: list[dict[str, Any]] = []
    english_papers: list[dict[str, Any]] = []
    for rank, paper in enumerate(report["papers"], start=1):
        metadata = paper["metadata"]
        analysis = paper["finalAnalysis"]
        english_analysis = analysis["english"]
        arxiv_id = metadata["arxivId"]
        topics = [PUBLIC_TOPIC_BY_CLASSIFICATION[analysis["classification"]]]
        recommended_ja = "推奨" if analysis["recommended"] else "非推奨"
        recommended_en = (
            "Recommended" if analysis["recommended"] else "Not recommended"
        )
        public_importance = analysis["importance"] * 2
        source_papers.append(
            {
                "arxivId": arxiv_id,
                "title": metadata["title"],
                "authors": metadata["authors"],
                "submittedDate": metadata["submittedDate"],
                "updatedDate": metadata["updatedDate"],
                "topics": topics,
                "absUrl": f"https://arxiv.org/abs/{arxiv_id}",
                "pdfUrl": f"https://arxiv.org/pdf/{arxiv_id}",
                "schedulerRank": rank,
                "schedulerRating": public_importance,
                "schedulerRatingScale": 10,
                "schedulerLabel": (
                    f"{recommended_ja}・重要度 {public_importance}/10"
                ),
                "schedulerSummary": analysis["summary"],
                "ratings": [
                    {
                        "label": "重要度",
                        "value": public_importance,
                        "scale": 10,
                    }
                ],
            }
        )
        english_papers.append(
            {
                "arxivId": arxiv_id,
                "schedulerLabel": (
                    f"{recommended_en} · Importance {public_importance}/10"
                ),
                "schedulerSummary": english_analysis["summary"],
                "ratings": [{"label": "Importance"}],
            }
        )

    source_edition = {
        "editionId": edition_id,
        "editionDate": report_date,
        "editionKind": kind,
        "sourceKind": SOURCE_KIND,
        "sourceLabel": SOURCE_LABEL,
        "importedAt": report["generatedAt"],
        "status": public_status,
        "message": ja_message,
        "expectedBatchDate": report["expectedBatchDate"],
        "observedBatchDate": report["observedBatchDate"],
        "periodStart": report["periodStart"],
        "periodEnd": report["periodEnd"],
        "sourceText": _render_source_text(
            report, english=False, public_status=public_status
        ),
        "papers": source_papers,
    }
    english_edition = {
        "editionId": edition_id,
        "message": en_message,
        "sourceText": _render_source_text(
            report, english=True, public_status=public_status
        ),
        "papers": english_papers,
    }

    validated_source = validate_history(
        {"schemaVersion": 2, "editions": [source_edition]}
    )["editions"][0]
    validated_english = validate_translation(
        {
            "schemaVersion": 1,
            "language": "en",
            "editions": [english_edition],
        }
    )["editions"][0]
    return AdaptedPublication(validated_source, validated_english)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _prepare_atomic_file(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _replace_pair_atomically(
    history_path: Path,
    history_content: bytes,
    history_original: bytes,
    translation_path: Path,
    translation_content: bytes,
    translation_original: bytes,
) -> None:
    if history_path.resolve(strict=False) == translation_path.resolve(strict=False):
        raise ResearchPublicationError(
            "history and English-overlay paths must be different"
        )
    history_temporary = _prepare_atomic_file(history_path, history_content)
    translation_temporary = _prepare_atomic_file(
        translation_path, translation_content
    )
    history_replaced = False
    try:
        if history_path.read_bytes() != history_original:
            raise PublicationConflictError(
                "history changed while the publication candidate was prepared"
            )
        if translation_path.read_bytes() != translation_original:
            raise PublicationConflictError(
                "English overlay changed while the publication candidate was prepared"
            )
        os.replace(history_temporary, history_path)
        history_replaced = True
        os.replace(translation_temporary, translation_path)
    except BaseException:
        if history_replaced:
            rollback = _prepare_atomic_file(history_path, history_original)
            try:
                os.replace(rollback, history_path)
            finally:
                rollback.unlink(missing_ok=True)
        raise
    finally:
        history_temporary.unlink(missing_ok=True)
        translation_temporary.unlink(missing_ok=True)


def _edition_by_id(
    editions: Sequence[Mapping[str, Any]], edition_id: str
) -> Mapping[str, Any] | None:
    for edition in editions:
        if edition["editionId"] == edition_id:
            return edition
    return None


def _managed_edition_anchor(edition: Mapping[str, Any]) -> dict[str, Any]:
    """Return identity/provenance fields that a presentation refresh cannot alter."""

    return {
        field: edition[field]
        for field in (
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
        )
    } | {
        "papers": [
            {
                field: paper[field]
                for field in (
                    "arxivId",
                    "title",
                    "authors",
                    "submittedDate",
                    "updatedDate",
                    "topics",
                    "absUrl",
                    "pdfUrl",
                    "schedulerRank",
                    "schedulerSummary",
                )
            }
            for paper in edition["papers"]
        ]
    }


def _validate_bundle_alignment(
    history: Mapping[str, Any], translation: Mapping[str, Any]
) -> None:
    source_editions = history["editions"]
    english_editions = translation["editions"]
    source_ids = [edition["editionId"] for edition in source_editions]
    english_ids = [edition["editionId"] for edition in english_editions]
    if source_ids != english_ids:
        raise PublicationConflictError(
            "English overlay edition order/identity must match source history"
        )
    for source, english in zip(source_editions, english_editions, strict=True):
        source_papers = source["papers"]
        english_papers = english["papers"]
        if [paper["arxivId"] for paper in source_papers] != [
            paper["arxivId"] for paper in english_papers
        ]:
            raise PublicationConflictError(
                f"English paper identity mismatch in {source['editionId']}"
            )
        for source_paper, english_paper in zip(
            source_papers, english_papers, strict=True
        ):
            if len(source_paper["ratings"]) != len(english_paper["ratings"]):
                raise PublicationConflictError(
                    f"English rating-label count mismatch in {source['editionId']}"
                )


def publish_research_report(
    report: object,
    history_path: Path,
    translation_path: Path,
    *,
    regenerate_site: bool = False,
    latest_path: Path = Path("site/data/latest.json"),
    archive_dir: Path = Path("site/data/archive"),
) -> PublicationResult:
    """Validate, append, and atomically commit one immutable public edition."""

    validated_report = validate_research_report(report)
    if validated_report["status"] in INCOMPLETE_REPORT_STATUSES:
        raise ResearchPublicationError(
            "incomplete research reports cannot be published"
        )
    publication = adapt_research_report(validated_report)
    edition_id = publication.source_edition["editionId"]
    try:
        history_original = history_path.read_bytes()
        translation_original = translation_path.read_bytes()
    except OSError as exc:
        raise ResearchPublicationError(
            "both existing public source files must be readable"
        ) from exc

    current_history = load_history(history_path)
    current_translation = load_translation(translation_path)
    source_existing = _edition_by_id(current_history["editions"], edition_id)
    english_existing = _edition_by_id(
        current_translation["editions"], edition_id
    )

    if source_existing is not None or english_existing is not None:
        if source_existing is None or english_existing is None:
            raise PublicationConflictError(
                f"edition {edition_id!r} exists in only one public source"
            )
        if (
            dict(source_existing) == publication.source_edition
            and dict(english_existing) == publication.english_edition
        ):
            generated_paths: tuple[Path, ...] = ()
            if regenerate_site:
                artifacts = generate_artifacts(current_history)
                generated_paths = tuple(
                    persist_artifacts(artifacts, latest_path, archive_dir)
                )
            return PublicationResult(
                edition_id=edition_id,
                changed=False,
                generated_paths=generated_paths,
            )
        if (
            source_existing["sourceKind"] != SOURCE_KIND
            or _managed_edition_anchor(source_existing)
            != _managed_edition_anchor(publication.source_edition)
            or [paper["schedulerSummary"] for paper in english_existing["papers"]]
            != [
                paper["schedulerSummary"]
                for paper in publication.english_edition["papers"]
            ]
        ):
            raise PublicationConflictError(
                f"refusing conflicting content for immutable edition {edition_id!r}"
            )
        _validate_bundle_alignment(current_history, current_translation)
        candidate_history = deepcopy(current_history)
        candidate_translation = deepcopy(current_translation)
        source_index = next(
            index
            for index, edition in enumerate(candidate_history["editions"])
            if edition["editionId"] == edition_id
        )
        english_index = next(
            index
            for index, edition in enumerate(candidate_translation["editions"])
            if edition["editionId"] == edition_id
        )
        candidate_history["editions"][source_index] = publication.source_edition
        candidate_translation["editions"][english_index] = (
            publication.english_edition
        )
        candidate_history = validate_history(candidate_history)
        candidate_translation = validate_translation(candidate_translation)
        _validate_bundle_alignment(candidate_history, candidate_translation)
        _replace_pair_atomically(
            history_path,
            _json_bytes(candidate_history),
            history_original,
            translation_path,
            _json_bytes(candidate_translation),
            translation_original,
        )
        generated_paths: tuple[Path, ...] = ()
        if regenerate_site:
            artifacts = generate_artifacts(candidate_history)
            generated_paths = tuple(
                persist_artifacts(
                    artifacts,
                    latest_path,
                    archive_dir,
                    refresh_archive_ids=(edition_id,),
                )
            )
        return PublicationResult(
            edition_id=edition_id,
            changed=True,
            generated_paths=generated_paths,
        )

    incoming_history = deepcopy(current_history)
    incoming_translation = deepcopy(current_translation)
    incoming_history["editions"].append(publication.source_edition)
    incoming_translation["editions"].append(publication.english_edition)
    incoming_history = validate_history(incoming_history)
    incoming_translation = validate_translation(incoming_translation)
    validate_bundle(
        current_history,
        current_translation,
        incoming_history,
        incoming_translation,
    )

    history_content = _json_bytes(incoming_history)
    translation_content = _json_bytes(incoming_translation)
    _replace_pair_atomically(
        history_path,
        history_content,
        history_original,
        translation_path,
        translation_content,
        translation_original,
    )

    generated_paths: tuple[Path, ...] = ()
    if regenerate_site:
        artifacts = generate_artifacts(incoming_history)
        generated_paths = tuple(
            persist_artifacts(artifacts, latest_path, archive_dir)
        )
    return PublicationResult(
        edition_id=edition_id,
        changed=True,
        generated_paths=generated_paths,
    )


def reconcile_daily_reports(
    report_dir: Path,
    history_path: Path,
    translation_path: Path,
    *,
    regenerate_site: bool = False,
    latest_path: Path = Path("site/data/latest.json"),
    archive_dir: Path = Path("site/data/archive"),
) -> ReconciliationResult:
    """Publish every durable completed daily report that is not public yet."""

    if report_dir.is_symlink() or not report_dir.is_dir():
        raise ResearchPublicationError(
            f"daily report directory is not a regular directory: {report_dir}"
        )
    try:
        report_paths = sorted(
            (
                path
                for path in report_dir.iterdir()
                if path.suffix.lower() == ".json"
            ),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise ResearchPublicationError(
            f"cannot enumerate daily report directory: {report_dir}"
        ) from exc
    if len(report_paths) > MAX_DAILY_REPORT_FILES:
        raise ResearchPublicationError(
            f"daily report directory exceeds the {MAX_DAILY_REPORT_FILES}-file limit"
        )

    completed_reports: list[dict[str, Any]] = []
    incomplete_count = 0
    for path in report_paths:
        if path.is_symlink() or not path.is_file():
            raise ResearchPublicationError(
                f"daily research report is not a regular file: {path}"
            )
        report = load_research_report(path)
        if report["reportKind"] != "daily":
            raise ResearchReportSchemaError(
                f"daily report directory contains a non-daily report: {path}"
            )
        expected_name = f"{report['reportDate']}.json"
        if path.name != expected_name:
            raise ResearchReportSchemaError(
                f"daily report filename must match reportDate: {path}"
            )
        if report["status"] in INCOMPLETE_REPORT_STATUSES:
            incomplete_count += 1
            continue
        completed_reports.append(report)

    try:
        history_original = history_path.read_bytes()
        translation_original = translation_path.read_bytes()
        current_history = load_history(history_path)
        current_translation = load_translation(translation_path)
    except OSError as exc:
        raise ResearchPublicationError(
            "both existing public source files must be readable"
        ) from exc

    _validate_bundle_alignment(current_history, current_translation)
    candidate_history = deepcopy(current_history)
    candidate_translation = deepcopy(current_translation)
    source_indexes = {
        edition["editionId"]: index
        for index, edition in enumerate(candidate_history["editions"])
    }
    english_indexes = {
        edition["editionId"]: index
        for index, edition in enumerate(candidate_translation["editions"])
    }
    published_ids: list[str] = []
    refreshed_ids: list[str] = []
    existing_ids: list[str] = []
    seen_ids: set[str] = set()
    for report in completed_reports:
        adapted = adapt_research_report(report)
        edition_id = adapted.source_edition["editionId"]
        if edition_id in seen_ids:
            raise PublicationConflictError(
                f"daily reports contain duplicate edition {edition_id!r}"
            )
        seen_ids.add(edition_id)
        source_index = source_indexes.get(edition_id)
        english_index = english_indexes.get(edition_id)
        if source_index is None and english_index is None:
            source_indexes[edition_id] = len(candidate_history["editions"])
            english_indexes[edition_id] = len(candidate_translation["editions"])
            candidate_history["editions"].append(adapted.source_edition)
            candidate_translation["editions"].append(adapted.english_edition)
            published_ids.append(edition_id)
            continue
        if source_index is None or english_index is None:
            raise PublicationConflictError(
                f"edition {edition_id!r} exists in only one public source"
            )
        source_existing = candidate_history["editions"][source_index]
        english_existing = candidate_translation["editions"][english_index]
        if (
            dict(source_existing) == adapted.source_edition
            and dict(english_existing) == adapted.english_edition
        ):
            existing_ids.append(edition_id)
            continue
        if (
            source_existing["sourceKind"] != SOURCE_KIND
            or _managed_edition_anchor(source_existing)
            != _managed_edition_anchor(adapted.source_edition)
            or [paper["schedulerSummary"] for paper in english_existing["papers"]]
            != [
                paper["schedulerSummary"]
                for paper in adapted.english_edition["papers"]
            ]
        ):
            raise PublicationConflictError(
                f"refusing conflicting content for immutable edition {edition_id!r}"
            )
        candidate_history["editions"][source_index] = adapted.source_edition
        candidate_translation["editions"][english_index] = adapted.english_edition
        refreshed_ids.append(edition_id)

    candidate_history = validate_history(candidate_history)
    candidate_translation = validate_translation(candidate_translation)
    _validate_bundle_alignment(candidate_history, candidate_translation)
    if published_ids and not refreshed_ids:
        validate_bundle(
            current_history,
            current_translation,
            candidate_history,
            candidate_translation,
        )
    elif refreshed_ids:
        current_source_ids = [
            edition["editionId"] for edition in current_history["editions"]
        ]
        candidate_source_ids = [
            edition["editionId"] for edition in candidate_history["editions"]
        ]
        if candidate_source_ids[: len(current_source_ids)] != current_source_ids:
            raise PublicationConflictError(
                "managed presentation refresh cannot remove or reorder editions"
            )

    if published_ids or refreshed_ids:
        _replace_pair_atomically(
            history_path,
            _json_bytes(candidate_history),
            history_original,
            translation_path,
            _json_bytes(candidate_translation),
            translation_original,
        )

    generated_paths: tuple[Path, ...] = ()
    if regenerate_site:
        final_history = load_history(history_path)
        artifacts = generate_artifacts(final_history)
        generated_paths = tuple(
            persist_artifacts(
                artifacts,
                latest_path,
                archive_dir,
                refresh_archive_ids=refreshed_ids,
            )
        )
    return ReconciliationResult(
        report_count=len(report_paths),
        completed_count=len(completed_reports),
        published_edition_ids=tuple(published_ids),
        refreshed_edition_ids=tuple(refreshed_ids),
        existing_edition_ids=tuple(existing_ids),
        incomplete_count=incomplete_count,
        generated_paths=generated_paths,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--report", type=Path)
    source.add_argument(
        "--daily-report-dir",
        type=Path,
        help="reconcile every completed YYYY-MM-DD.json daily report",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=Path("content/chatgpt_scheduler_history.json"),
    )
    parser.add_argument(
        "--translation",
        type=Path,
        default=Path("site/data/i18n/en.json"),
    )
    parser.add_argument(
        "--regenerate-site",
        action="store_true",
        help="also regenerate latest.json and the immutable archive/index",
    )
    parser.add_argument(
        "--latest", type=Path, default=Path("site/data/latest.json")
    )
    parser.add_argument(
        "--archive-dir", type=Path, default=Path("site/data/archive")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        if args.report is not None:
            report = load_research_report(args.report)
            result = publish_research_report(
                report,
                args.history,
                args.translation,
                regenerate_site=args.regenerate_site,
                latest_path=args.latest,
                archive_dir=args.archive_dir,
            )
        else:
            assert args.daily_report_dir is not None
            reconciliation = reconcile_daily_reports(
                args.daily_report_dir,
                args.history,
                args.translation,
                regenerate_site=args.regenerate_site,
                latest_path=args.latest,
                archive_dir=args.archive_dir,
            )
    except (
        HistoryImportError,
        PublicBundleError,
        ResearchPublicationError,
        OSError,
    ) as exc:
        print(f"research_publication: {exc}", file=sys.stderr)
        return 1
    if args.report is not None:
        action = "published" if result.changed else "already published"
        print(f"{action}: {result.edition_id}")
    else:
        print(
            f"reconciled {reconciliation.report_count} daily report(s): "
            f"published {len(reconciliation.published_edition_ids)}, "
            f"refreshed {len(reconciliation.refreshed_edition_ids)}, "
            f"already published {len(reconciliation.existing_edition_ids)}, "
            f"incomplete {reconciliation.incomplete_count}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
