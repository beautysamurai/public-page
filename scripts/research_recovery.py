"""Bounded retries of existing period markers and a separate public status feed."""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import research_pipeline as pipeline


COMPLETE = {pipeline.UPDATE_CONFIRMED, pipeline.NO_RELEVANT_PAPERS, pipeline.NO_NEW_BATCH_EXPECTED}


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise pipeline.StateError("duplicate period marker key")
        value[key] = item
    return value


def pending_periods(root: Path, config: pipeline.PipelineConfig, today: date) -> list[dict]:
    results = []
    marker_root = root / "research/pending-periods"
    if marker_root.is_symlink():
        raise pipeline.StateError("redirected period marker root")
    for kind in ("weekly", "monthly"):
        directory = marker_root / kind
        if directory.is_symlink():
            raise pipeline.StateError("redirected period marker directory")
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                raise pipeline.StateError("unsafe period marker")
            end = date.fromisoformat(path.stem)
            if path.stem != end.isoformat() or (kind == "weekly" and end.weekday() != 4) or (
                kind == "monthly" and end.day != calendar.monthrange(end.year, end.month)[1]
            ):
                raise pipeline.StateError("invalid period marker date")
            marker = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
            if not isinstance(marker, dict) or type(marker.get("schemaVersion")) is not int or marker != {"schemaVersion": 1, "reportKind": kind, "periodEnd": end.isoformat()}:
                raise pipeline.StateError("invalid period marker identity")
            start = end - timedelta(days=6) if kind == "weekly" else end.replace(day=1)
            report_path = root / f"research/reviews/{kind}/{end}.json"
            if report_path.exists():
                if report_path.is_symlink():
                    raise pipeline.StateError("unsafe period report")
                report = pipeline._read_persisted_report(report_path)
                if (report["reportKind"], report["periodStart"], report["periodEnd"]) != (kind, str(start), str(end)):
                    raise pipeline.StateError("period report identity mismatch")
                if report["status"] in COMPLETE:
                    continue
            reports = pipeline.load_daily_reports(root / "research/daily", start, end)
            missing = pipeline.missing_batch_dates(config, reports, start, end)
            results.append({"reportKind": kind, "periodEnd": str(end),
                            "missingBatchDates": [str(day) for day in missing],
                            "ready": end < today and not missing})
    return sorted(results, key=lambda item: (item["periodEnd"], item["reportKind"]))


def retry_one(root: Path, config: pipeline.PipelineConfig, now: datetime) -> bool:
    """At most one ready period / two bounded chunks; no discovery of new periods."""
    for item in pending_periods(root, config, now.date()):
        if not item["ready"]:
            continue
        kind, end = item["reportKind"], date.fromisoformat(item["periodEnd"])
        start = end - timedelta(days=6) if kind == "weekly" else end.replace(day=1)
        papers = pipeline._unique_stored_papers(pipeline.load_daily_reports(root / "research/daily", start, end))
        try:
            chunks = pipeline._build_synthesis_chunks(
                papers, report_kind=kind, period_start=start, period_end=end,
                max_items=config.synthesis_chunk_max_items, max_bytes=config.synthesis_chunk_max_bytes,
            ) if papers else []
        except pipeline.StructuredOutputError:
            print(f"period exceeds prompt budget; remains pending: {kind} {end}")
            continue
        if len(chunks) > 2:
            print(f"keeping larger period for scheduled/manual synthesis: {kind} {end}")
            continue
        print(f"retrying ready period: {kind} {end}")
        try:
            report = pipeline.run_aggregate(
                config, report_kind=kind, period_start=start, period_end=end,
                daily_dir=root / "research/daily", output_dir=root / f"research/reviews/{kind}",
                generated_at=now,
            )
        except pipeline.PipelineError:
            # Never print model drafts, source text, credentials or provider errors.
            print(f"period generation failed; retry marker retained: {kind} {end}")
            return True
        if report["status"] in COMPLETE:
            marker = root / f"research/pending-periods/{kind}/{end}.json"
            marker.unlink()  # Only this validated successful retry marker is removed.
            return False
        return True
    return False


def write_status(root: Path, config: pipeline.PipelineConfig) -> dict:
    state = pipeline.load_state(root / "research/state.json")
    attempted = state["lastAttemptedAt"]
    today = datetime.fromisoformat(attempted.replace("Z", "+00:00")).date() if attempted else datetime.now(timezone.utc).date()
    value = {"schemaVersion": 1, "daily": {
        "status": state["lastStatus"], "checkedAt": attempted,
        "lastCompletedBatchDate": state["lastCompletedBatchDate"],
        "pendingBatchDate": state["pendingBatchDate"],
    }, "pendingPeriods": pending_periods(root, config, today)}
    target = root / "site/data/research-status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("retry", "status"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    config = pipeline.load_pipeline_config(args.root / "config/research.json")
    if args.mode == "status":
        write_status(args.root, config)
        return 0
    failed = retry_one(args.root, config, datetime.now(timezone.utc))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"failed={str(failed).lower()}\n")
    # Defer workflow failure until after daily output has been published.
    return 0


if __name__ == "__main__":
    sys.exit(main())
