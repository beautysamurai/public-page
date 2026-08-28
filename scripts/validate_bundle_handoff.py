#!/usr/bin/env python3
"""Create and validate a producer-complete reviewed-bundle handoff.

A complete handoff directory contains exactly:

- chatgpt_scheduler_history.json
- en.json
- bundle.complete.json

The completion manifest binds the two public JSON snapshots to exact byte
lengths and SHA-256 digests. Producers create the manifest only after both
snapshot files have been copied into a fresh staging directory. Consumers
validate the manifest after atomically claiming that directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
MANIFEST_NAME = "bundle.complete.json"
HISTORY_NAME = "chatgpt_scheduler_history.json"
TRANSLATION_NAME = "en.json"
BUNDLE_FILES = (HISTORY_NAME, TRANSLATION_NAME)
EXPECTED_ENTRIES = frozenset((*BUNDLE_FILES, MANIFEST_NAME))
MANIFEST_FIELDS = frozenset({"schemaVersion", "files"})
FILE_FIELDS = frozenset({"sha256", "bytes"})
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAX_MANIFEST_BYTES = 64 * 1024


class BundleHandoffError(RuntimeError):
    """The producer handoff is incomplete, ambiguous, or internally inconsistent."""


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleHandoffError(f"manifest contains duplicate key {key!r}")
        result[key] = value
    return result


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    context: str,
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise BundleHandoffError(
            f"{context} has invalid fields; missing={missing}, unknown={unknown}"
        )


def _require_bundle_directory(bundle_dir: Path) -> Path:
    resolved = bundle_dir.resolve(strict=False)
    if not resolved.is_dir():
        raise BundleHandoffError(f"bundle directory does not exist: {bundle_dir}")
    return resolved


def _require_regular_file(path: Path, context: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise BundleHandoffError(f"{context} must be a regular non-symlink file")


def _digest(path: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            hasher.update(chunk)
    return size, hasher.hexdigest()


def _manifest_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def create_manifest(bundle_dir: Path) -> dict[str, Any]:
    """Create a completion manifest for an otherwise complete staging directory."""

    root = _require_bundle_directory(bundle_dir)
    actual_entries = frozenset(item.name for item in root.iterdir())
    expected_sources = frozenset(BUNDLE_FILES)
    if actual_entries != expected_sources:
        missing = sorted(expected_sources - actual_entries)
        unknown = sorted(actual_entries - expected_sources)
        raise BundleHandoffError(
            "staging directory must contain exactly the two reviewed JSON files; "
            f"missing={missing}, unknown={unknown}"
        )

    files: dict[str, dict[str, Any]] = {}
    for name in BUNDLE_FILES:
        path = root / name
        _require_regular_file(path, name)
        size, digest = _digest(path)
        files[name] = {"sha256": digest, "bytes": size}

    manifest = {"schemaVersion": SCHEMA_VERSION, "files": files}
    destination = root / MANIFEST_NAME
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=root,
            prefix=f".{MANIFEST_NAME}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(_manifest_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    _require_regular_file(path, "completion manifest")
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BundleHandoffError("completion manifest exceeds the 64 KiB limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except UnicodeDecodeError as exc:
        raise BundleHandoffError("completion manifest must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise BundleHandoffError("completion manifest is not valid JSON") from exc

    if not isinstance(value, dict):
        raise BundleHandoffError("completion manifest must be a JSON object")
    _require_exact_keys(value, MANIFEST_FIELDS, "completion manifest")

    schema_version = value["schemaVersion"]
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise BundleHandoffError("completion manifest schemaVersion must be integer 1")

    files = value["files"]
    if not isinstance(files, dict):
        raise BundleHandoffError("completion manifest files must be an object")
    if frozenset(files) != frozenset(BUNDLE_FILES):
        missing = sorted(set(BUNDLE_FILES) - set(files))
        unknown = sorted(set(files) - set(BUNDLE_FILES))
        raise BundleHandoffError(
            f"completion manifest file set is invalid; missing={missing}, "
            f"unknown={unknown}"
        )

    for name in BUNDLE_FILES:
        metadata = files[name]
        if not isinstance(metadata, dict):
            raise BundleHandoffError(f"manifest metadata for {name} must be an object")
        _require_exact_keys(metadata, FILE_FIELDS, f"manifest metadata for {name}")
        digest = metadata["sha256"]
        size = metadata["bytes"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest.casefold()):
            raise BundleHandoffError(f"manifest sha256 for {name} is invalid")
        if type(size) is not int or size < 0:
            raise BundleHandoffError(
                f"manifest byte length for {name} must be a non-negative integer"
            )
    return value


def validate_handoff(bundle_dir: Path) -> dict[str, Any]:
    """Validate an immutable producer-complete handoff directory."""

    root = _require_bundle_directory(bundle_dir)
    actual_entries = frozenset(item.name for item in root.iterdir())
    if actual_entries != EXPECTED_ENTRIES:
        missing = sorted(EXPECTED_ENTRIES - actual_entries)
        unknown = sorted(actual_entries - EXPECTED_ENTRIES)
        raise BundleHandoffError(
            f"bundle directory entries are invalid; missing={missing}, "
            f"unknown={unknown}"
        )

    manifest = load_manifest(root / MANIFEST_NAME)
    for name in BUNDLE_FILES:
        path = root / name
        _require_regular_file(path, name)
        actual_size, actual_digest = _digest(path)
        expected = manifest["files"][name]
        if actual_size != expected["bytes"]:
            raise BundleHandoffError(
                f"byte length mismatch for {name}: "
                f"expected {expected['bytes']}, observed {actual_size}"
            )
        if actual_digest != expected["sha256"].casefold():
            raise BundleHandoffError(f"SHA-256 mismatch for {name}")
    return manifest


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    for command in ("create", "validate"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        if args.command == "create":
            create_manifest(args.bundle_dir)
            print("created producer completion manifest")
        else:
            validate_handoff(args.bundle_dir)
            print("producer completion manifest and bundle hashes are valid")
    except (BundleHandoffError, OSError) as exc:
        print(f"validate_bundle_handoff: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
