"""Bounded retries of existing period markers and a separate public status feed."""
from __future__ import annotations

import argparse
import calendar
import contextlib
import http.client
import json
import os
import sys
import time
import urllib.error
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import research_pipeline as pipeline


COMPLETE = {pipeline.UPDATE_CONFIRMED, pipeline.NO_RELEVANT_PAPERS, pipeline.NO_NEW_BATCH_EXPECTED}
PENDING = {pipeline.UPDATE_NOT_CONFIRMED, pipeline.UPDATER_OFFLINE}


def pending_daily_dates(root: Path, config: pipeline.PipelineConfig, expected: date) -> list[date]:
    """Disk records are the queue; advancing the latest cursor cannot erase gaps."""
    pending = set()
    complete = set()
    for directory in (root / "research/daily", root / "research/listings"):
        if directory.is_symlink():
            raise pipeline.StateError("redirected daily carry-forward directory")
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise pipeline.StateError("invalid daily carry-forward file")
            batch = date.fromisoformat(path.stem)
            if path.stem != str(batch):
                raise pipeline.StateError("noncanonical daily carry-forward date")
            if directory.name == "listings":
                pipeline.load_listing_snapshot(path, config.categories)
                pending.add(batch)
            else:
                report = pipeline._read_persisted_report(path)
                if report["reportKind"] != pipeline.DAILY or report["reportDate"] != str(batch):
                    raise pipeline.StateError("daily carry-forward identity mismatch")
                if report["status"] in {pipeline.UPDATE_CONFIRMED, pipeline.NO_RELEVANT_PAPERS}:
                    complete.add(batch)
                elif report["status"] in PENDING:
                    pending.add(batch)
    return sorted(batch for batch in pending - complete if batch <= expected
                  and pipeline.is_scheduled_batch_date(batch, config.no_announcement_dates))


def capture_pending_listings(root, config, now, *, history_fetcher=None, sleep_fn=time.sleep):
    """Capture each missing batch once, before any metadata/paid API calls.

    A date absent from even one category is not inferred to be an empty batch.
    Old snapshots remain usable after arXiv's rolling past-week window expires.
    """
    expected = pipeline.expected_batch_date(now, config.no_announcement_dates)
    directory = root / "research/listings"
    targets = [batch for batch in pending_daily_dates(root, config, expected)
               if not (directory / f"{batch}.json").exists()
               and 0 <= (expected - batch).days <= pipeline.MAX_PASTWEEK_RECOVERY_DAYS]
    if not targets:
        return
    deadline = time.monotonic() + 300
    pacer = pipeline.ArxivRequestPacer(sleep_fn=sleep_fn, deadline=deadline)
    fetcher = history_fetcher or (lambda category: pipeline.fetch_pastweek_listing_page(
        category, timeout=config.timeout, opener=pacer.open))
    by_category = {}
    for category in config.categories:
        raw = pipeline._retry(lambda: fetcher(category), config.retries, sleep_fn, deadline=deadline)
        by_category[category] = {page.batch_date: page for page in pipeline.parse_pastweek_listing_page(raw, category)}
    for target in targets:
        if all(target in by_category[category] for category in config.categories):
            pipeline.save_listing_snapshot(directory, [by_category[c][target] for c in config.categories], now)
            print(f"Saved confirmed listing for carry-forward: {target}", file=sys.stderr)
    return True  # Network access occurred; keep pacing across phase boundaries.


def run_daily_cycle(root, config, now, *, daily_runner=None, capture=None, sleep_fn=time.sleep):
    """Latest batch plus at most one older unfinished batch; never a busy loop."""
    daily_runner = daily_runner or pipeline.run_daily
    capture = capture or capture_pending_listings
    state_path = root / "research/state.json"
    output_dir = root / "research/daily"
    expected = pipeline.expected_batch_date(now, config.no_announcement_dates)
    run_config = replace(config, daily_time_budget=min(config.daily_time_budget, 1800),
                         openai_timeout=min(config.openai_timeout, 300))
    kwargs = dict(state_path=state_path, output_dir=output_dir, checked_at=now,
                  published_history=root / "content/chatgpt_scheduler_history.json")
    # A transient source outage is carried, but corrupt source/state must fail.
    try:
        if capture(root, run_config, now):
            sleep_fn(pipeline.ARXIV_REQUEST_INTERVAL_SECONDS)
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException, pipeline.WorkBudgetExceeded) as exc:
        def unavailable(_category):
            raise exc
        latest = daily_runner(replace(run_config, retries=0), **kwargs, list_fetcher=unavailable)
        return {"latest": latest, "carried": [], "pending": pending_daily_dates(root, config, expected),
                "failed": latest["status"] in PENDING and not pipeline.is_deferred_report(latest)}
    latest = daily_runner(run_config, **kwargs)
    carried = []
    if latest["status"] in COMPLETE:
        for batch in pending_daily_dates(root, config, expected):
            if batch >= expected:
                continue
            path = output_dir / f"{batch}.json"
            if path.exists():
                previous = pipeline._read_persisted_report(path)
                attempted = datetime.fromisoformat(previous["generatedAt"].replace("Z", "+00:00"))
                if attempted.astimezone(ZoneInfo("Asia/Tokyo")).date() >= now.astimezone(ZoneInfo("Asia/Tokyo")).date():
                    continue  # At most one automatic attempt per batch per day.
            if not (root / f"research/listings/{batch}.json").exists() and (expected - batch).days > pipeline.MAX_PASTWEEK_RECOVERY_DAYS:
                continue  # Do not let an unrecoverable legacy gap starve newer work.
            latest_state = pipeline.load_state(state_path)
            try:
                sleep_fn(pipeline.ARXIV_REQUEST_INTERVAL_SECONDS)
                carried.append(daily_runner(replace(run_config, daily_time_budget=600, retries=0),
                    **kwargs, recover_pending=True, target_batch=batch))
            finally:
                # Operational freshness describes the latest batch; all older
                # pending identities remain independently visible in the queue.
                pipeline.save_state(state_path, latest_state)
            break
    reports = [latest, *carried]
    return {"latest": latest, "carried": carried, "pending": pending_daily_dates(root, config, expected),
            "failed": any(r["status"] in PENDING and not pipeline.is_deferred_report(r) for r in reports)}


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
                # A daily run must leave time to persist its own completed data.
                # Two chunks, each with one initial request + at most two draft
                # repairs, fit within 30 minutes at the maximum 300s timeout.
                replace(config, retries=0, openai_timeout=min(config.openai_timeout, 300)),
                report_kind=kind, period_start=start, period_end=end,
                daily_dir=root / "research/daily", output_dir=root / f"research/reviews/{kind}",
                generated_at=now,
            )
        except pipeline.UpdaterOfflineError:
            print(f"period API unavailable; carried to next daily run: {kind} {end}")
            return False
        except pipeline.PipelineError:
            # Never print model drafts, source text, credentials or provider errors.
            print(f"period generation failed; retry marker retained: {kind} {end}")
            return True
        if report["status"] in COMPLETE:
            marker = root / f"research/pending-periods/{kind}/{end}.json"
            marker.unlink()  # Only this validated successful retry marker is removed.
            return False
        return not pipeline.is_deferred_report(report)
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
    expected = pipeline.expected_batch_date(datetime.now(timezone.utc), config.no_announcement_dates)
    value["pendingDailyBatches"] = [str(batch) for batch in pending_daily_dates(root, config, expected)]
    value["dailyRetryPolicy"] = "next_daily_run"
    target = root / "site/data/research-status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("daily", "retry", "status"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    config = pipeline.load_pipeline_config(args.root / "config/research.json")
    if args.mode == "daily":
        with contextlib.redirect_stdout(sys.stderr):
            result = run_daily_cycle(args.root, config, datetime.now(timezone.utc))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"carry_failed={str(result['failed']).lower()}\n")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("## Daily carry-forward\n\n")
                summary.write(f"Latest batch: {result['latest']['reportDate']} ({result['latest']['status']})\n\n")
                summary.write("Queued incomplete batches: " + (", ".join(map(str, result["pending"])) or "none") + "\n\n")
                summary.write("Temporary API outages remain pending for the next daily run; completed analyses are reused.\n")
        print(json.dumps(result["latest"], ensure_ascii=False))
        return 0
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
