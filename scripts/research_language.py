#!/usr/bin/env python3
"""Deterministic language heuristics for bilingual research narratives.

These checks deliberately apply only to sentence-like narrative fields.  Short
schema tokens and topical tags are not natural language and need separate
allowlist validation.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


MIN_JAPANESE_LETTER_RATIO = 0.20
MIN_JAPANESE_KANA_LETTERS = 2
MIN_JAPANESE_KANA_RATIO = 0.08
MIN_ENGLISH_LATIN_LETTERS = 8
MIN_ENGLISH_LATIN_RATIO = 0.70
MAX_ENGLISH_JAPANESE_LETTERS = 16


@dataclass(frozen=True)
class _LanguageCounts:
    letters: int
    latin: int
    japanese: int
    kana: int


def _is_kana(codepoint: int) -> bool:
    return (
        0x3040 <= codepoint <= 0x30FF
        or 0x31F0 <= codepoint <= 0x31FF
    )


def _is_han(codepoint: int) -> bool:
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2EBEF
        or 0x30000 <= codepoint <= 0x323AF
    )


def _language_counts(value: str) -> _LanguageCounts:
    letters = 0
    latin = 0
    japanese = 0
    kana = 0
    for character in unicodedata.normalize("NFKC", value):
        codepoint = ord(character)
        is_letter = unicodedata.category(character).startswith("L")
        if is_letter and _is_kana(codepoint):
            kana += 1
            japanese += 1
        elif is_letter and _is_han(codepoint):
            japanese += 1

        if is_letter:
            letters += 1
            if "LATIN" in unicodedata.name(character, ""):
                latin += 1
    return _LanguageCounts(
        letters=letters,
        latin=latin,
        japanese=japanese,
        kana=kana,
    )


def contains_japanese_characters(value: str) -> bool:
    """Return whether text contains kana or a CJK ideograph used by Japanese."""

    return _language_counts(value).japanese > 0


def contains_latin_characters(value: str) -> bool:
    """Return whether text contains at least one Unicode Latin letter."""

    return _language_counts(value).latin > 0


def contains_japanese_prose(value: str) -> bool:
    """Require kana and a material Japanese share in sentence-like prose."""

    counts = _language_counts(value)
    return (
        counts.letters > 0
        and counts.kana >= MIN_JAPANESE_KANA_LETTERS
        and counts.japanese / counts.letters >= MIN_JAPANESE_LETTER_RATIO
        and counts.kana / counts.letters >= MIN_JAPANESE_KANA_RATIO
    )


def contains_english_prose(value: str) -> bool:
    """Require predominantly Latin prose while allowing a short Japanese name."""

    counts = _language_counts(value)
    return (
        counts.letters > 0
        and counts.latin >= MIN_ENGLISH_LATIN_LETTERS
        and counts.latin / counts.letters >= MIN_ENGLISH_LATIN_RATIO
        and counts.japanese <= MAX_ENGLISH_JAPANESE_LETTERS
    )


def contains_english_document(value: str) -> bool:
    """Validate a combined English document by ratios, not a per-field name cap."""

    counts = _language_counts(value)
    return (
        counts.letters > 0
        and counts.latin >= MIN_ENGLISH_LATIN_LETTERS
        and counts.latin / counts.letters >= MIN_ENGLISH_LATIN_RATIO
    )
