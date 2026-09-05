import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import arxiv_digest as digest  # noqa: E402
import research_pipeline as pipeline  # noqa: E402


UTC = timezone.utc
CHECKED_AT = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
TARGET = date(2026, 8, 28)


def listing_html(*arxiv_ids: str) -> bytes:
    items = "".join(
        f'<dt><a name="item{index}">[{index}]</a>'
        f'<a href="/abs/{arxiv_id}" title="Abstract">arXiv:{arxiv_id}</a></dt>'
        f'<dd><div class="meta">Untrusted source {arxiv_id}</div></dd>'
        for index, arxiv_id in enumerate(arxiv_ids, 1)
    )
    return (
        "<!doctype html><html><body>"
        "<h3>Showing new listings for Friday, 28 August 2026</h3>"
        f"<h3>New submissions (showing {len(arxiv_ids)} of {len(arxiv_ids)} entries)</h3>"
        f"<dl>{items}</dl>"
        "<h3>Cross submissions (showing 0 of 0 entries)</h3><dl></dl>"
        "<h3>Replacement submissions (showing 0 of 0 entries)</h3><dl></dl>"
        "</body></html>"
    ).encode("utf-8")


def entry(
    arxiv_id: str,
    *,
    title: str = "Electronic execution in rates markets",
    abstract: str = "We study market microstructure and yield-curve trading.",
) -> digest.AtomEntry:
    return digest.AtomEntry(
        arxiv_id=f"{arxiv_id}v1" if "v" not in arxiv_id else arxiv_id,
        title=title,
        authors=("Researcher One",),
        submitted_at=datetime(2026, 8, 28, 8, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
        categories=("q-fin.TR",),
        abstract=abstract,
    )


def analysis(
    *,
    importance: int = 2,
    classification: str = "market_microstructure",
    recommended: bool = True,
) -> dict:
    return {
        "classification": classification,
        "summary": "日本語の要約です。",
        "mainResult": "主要な結果です。",
        "practicalApplication": "実務への応用です。",
        "methodology": "研究手法です。",
        "limitations": "限界があります。",
        "importance": importance,
        "recommended": recommended,
        "reason": "読む理由です。",
        "tags": ["市場マイクロストラクチャー"],
        "english": {
            "classification": classification,
            "summary": "English summary.",
            "mainResult": "Main result.",
            "practicalApplication": "Practical application.",
            "methodology": "Research methodology.",
            "limitations": "Study limitations.",
            "reason": "Reason to read.",
            "tags": ["market microstructure"],
        },
    }


def config(**changes) -> pipeline.PipelineConfig:
    values = {
        "categories": ("q-fin.TR",),
        "pdf_importance_threshold": 3,
        "screen_model": "screen-model",
        "full_model": "full-model",
        "weekly_model": "weekly-model",
        "monthly_model": "monthly-model",
        "screen_reasoning_effort": "low",
        "full_reasoning_effort": "medium",
        "weekly_reasoning_effort": "medium",
        "monthly_reasoning_effort": "high",
        "pdf_detail": "low",
        "max_candidates": 100,
        "retries": 0,
        "timeout": 2.0,
        "openai_timeout": 37.5,
        "daily_time_budget": 100.0,
    }
    values.update(changes)
    return pipeline.PipelineConfig(**values)


class FakeAnalyzer:
    def __init__(self, *, screens=None, full=None):
        self.screens = screens or {}
        self.full = full or {}
        self.screened = []
        self.pdfs = []

    def analyze_abstract(self, candidate):
        arxiv_id = pipeline._base_arxiv_id(candidate.entry.arxiv_id)
        self.screened.append(arxiv_id)
        return self.screens.get(arxiv_id, analysis())

    def analyze_pdf(self, candidate):
        arxiv_id = pipeline._base_arxiv_id(candidate.entry.arxiv_id)
        self.pdfs.append(arxiv_id)
        return self.full.get(arxiv_id, analysis(importance=5))


class SequenceClock:
    def __init__(self, values):
        self._values = iter(values)
        self._last = 0.0

    def __call__(self):
        try:
            self._last = float(next(self._values))
        except StopIteration:
            pass
        return self._last


class DailyResilienceTests(unittest.TestCase):
    @staticmethod
    def _run_daily(
        root: Path,
        arxiv_ids: tuple[str, ...],
        analyzer: FakeAnalyzer,
        *,
        run_config: pipeline.PipelineConfig,
        monotonic_fn,
        entries=None,
    ) -> dict:
        metadata = entries or {arxiv_id: entry(arxiv_id) for arxiv_id in arxiv_ids}
        return pipeline.run_daily(
            run_config,
            state_path=root / "state.json",
            output_dir=root / "daily",
            checkpoint_dir=root / "checkpoints",
            checked_at=CHECKED_AT,
            list_fetcher=lambda _category: listing_html(*arxiv_ids),
            metadata_fetcher=lambda requested: {
                arxiv_id: metadata[arxiv_id] for arxiv_id in requested
            },
            analyzer=analyzer,
            sleep_fn=lambda _delay: None,
            monotonic_fn=monotonic_fn,
        )

    def test_soft_deadline_checkpoints_first_candidate_and_resume_skips_it(self):
        arxiv_ids = ("2608.10001", "2608.10002")
        first_analyzer = FakeAnalyzer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._run_daily(
                root,
                arxiv_ids,
                first_analyzer,
                run_config=config(daily_time_budget=5.0),
                monotonic_fn=SequenceClock((0, 0, 0, 0, 5)),
            )

            self.assertEqual(first["status"], pipeline.UPDATE_NOT_CONFIRMED)
            self.assertEqual(first_analyzer.screened, [arxiv_ids[0]])
            checkpoint_path = root / "checkpoints" / "2026-08-28.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(set(checkpoint["results"]), {arxiv_ids[0]})
            self.assertEqual(
                checkpoint["results"][arxiv_ids[0]]["status"], "completed"
            )

            second_analyzer = FakeAnalyzer()
            second = self._run_daily(
                root,
                arxiv_ids,
                second_analyzer,
                run_config=config(daily_time_budget=5.0),
                monotonic_fn=lambda: 0.0,
            )

            self.assertEqual(second["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(second_analyzer.screened, [arxiv_ids[1]])
            self.assertEqual(len(second["papers"]), 2)
            self.assertTrue(checkpoint_path.exists())

    def test_resume_from_awaiting_pdf_does_not_repeat_abstract_screen(self):
        arxiv_id = "2608.10001"
        first_analyzer = FakeAnalyzer(
            screens={arxiv_id: analysis(importance=4)}
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._run_daily(
                root,
                (arxiv_id,),
                first_analyzer,
                run_config=config(daily_time_budget=5.0),
                monotonic_fn=SequenceClock((0, 0, 0, 0, 5)),
            )

            self.assertEqual(first["status"], pipeline.UPDATE_NOT_CONFIRMED)
            checkpoint_path = root / "checkpoints" / "2026-08-28.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(
                checkpoint["results"][arxiv_id]["status"], "awaiting_pdf"
            )

            second_analyzer = FakeAnalyzer()
            second = self._run_daily(
                root,
                (arxiv_id,),
                second_analyzer,
                run_config=config(daily_time_budget=5.0),
                monotonic_fn=lambda: 0.0,
            )

            self.assertEqual(second["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(second_analyzer.screened, [])
            self.assertEqual(second_analyzer.pdfs, [arxiv_id])
            self.assertTrue(checkpoint_path.exists())

    def test_model_change_preserves_completed_candidate_analysis(self):
        arxiv_ids = ("2608.10001", "2608.10002")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_analyzer = FakeAnalyzer()
            first = self._run_daily(
                root,
                arxiv_ids,
                first_analyzer,
                run_config=config(
                    screen_model="old-screen-model", daily_time_budget=5.0
                ),
                monotonic_fn=SequenceClock((0, 0, 0, 0, 5)),
            )
            self.assertEqual(first["status"], pipeline.UPDATE_NOT_CONFIRMED)

            second_analyzer = FakeAnalyzer()
            second = self._run_daily(
                root,
                arxiv_ids,
                second_analyzer,
                run_config=config(
                    screen_model="new-screen-model", daily_time_budget=5.0
                ),
                monotonic_fn=lambda: 0.0,
            )

            self.assertEqual(second["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(second_analyzer.screened, [arxiv_ids[1]])

    def test_invalid_checkpoint_is_preserved_without_paid_reanalysis(self):
        arxiv_id = "2608.10001"
        candidate = pipeline.PaperCandidate(
            entry(arxiv_id), ("new",), ("q-fin.TR",)
        )
        candidates = {arxiv_id: candidate}
        run_config = config()
        fingerprint = pipeline._checkpoint_fingerprint(
            run_config, TARGET, candidates
        )
        valid_analysis = analysis()
        invalid_checkpoints = (
            {
                **pipeline._new_checkpoint(TARGET, fingerprint),
                "unexpected": True,
            },
            {
                **pipeline._new_checkpoint(TARGET, fingerprint),
                "results": {
                    "2608.99999": {
                        "status": "completed",
                        "screenAnalysis": valid_analysis,
                        "finalAnalysis": valid_analysis,
                    }
                },
            },
            {
                **pipeline._new_checkpoint(TARGET, fingerprint),
                "results": {
                    arxiv_id: {
                        "status": "awaiting_pdf",
                        "screenAnalysis": analysis(
                            importance=1,
                            classification="out_of_scope",
                            recommended=False,
                        ),
                        "finalAnalysis": None,
                    }
                },
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "checkpoint.json"
            for invalid in invalid_checkpoints:
                with self.subTest(invalid=invalid):
                    pipeline.atomic_write_json(checkpoint_path, invalid)
                    with self.assertRaises(pipeline.StateError):
                        pipeline._load_or_create_checkpoint(
                            checkpoint_path,
                            target=TARGET,
                            fingerprint=fingerprint,
                            candidate_keys=(arxiv_id,),
                            candidate_fingerprints={arxiv_id: pipeline._candidate_resume_fingerprint(candidate)},
                        )
                    self.assertEqual(
                        json.loads(checkpoint_path.read_text(encoding="utf-8")),
                        invalid,
                    )

    def test_completed_report_repairs_state_after_one_state_write_failure(self):
        arxiv_id = "2608.10001"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_save_state = pipeline.save_state
            completed_save_failed = False

            def fail_completed_save_once(path, state):
                nonlocal completed_save_failed
                if state["lastCompletedBatchDate"] == TARGET.isoformat() and not completed_save_failed:
                    completed_save_failed = True
                    raise OSError("simulated state replace failure")
                return real_save_state(path, state)

            with mock.patch.object(
                pipeline, "save_state", side_effect=fail_completed_save_once
            ):
                report = self._run_daily(
                    root,
                    (arxiv_id,),
                    FakeAnalyzer(),
                    run_config=config(),
                    monotonic_fn=lambda: 0.0,
                )

            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(completed_save_failed)
            self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(state["lastStatus"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(state["lastCompletedBatchDate"], TARGET.isoformat())
            self.assertIsNone(state["pendingBatchDate"])
            self.assertTrue(
                (root / "checkpoints" / "2026-08-28.json").exists()
            )

    def test_success_retains_decisions_to_prevent_later_reanalysis(self):
        arxiv_id = "2608.10001"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._run_daily(
                root,
                (arxiv_id,),
                FakeAnalyzer(),
                run_config=config(),
                monotonic_fn=lambda: 0.0,
            )

            self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
            self.assertTrue(
                (root / "checkpoints" / "2026-08-28.json").exists()
            )

    def test_retry_deadline_prevents_another_attempt(self):
        attempts = []
        sleeps = []

        def fail():
            attempts.append("attempt")
            raise RuntimeError("transient")

        with self.assertRaises(pipeline.WorkBudgetExceeded):
            pipeline._retry(
                fail,
                retries=3,
                sleep_fn=sleeps.append,
                deadline=1.0,
                monotonic_fn=SequenceClock((0.0, 1.0)),
            )

        self.assertEqual(attempts, ["attempt"])
        self.assertEqual(sleeps, [])

    def test_runtime_limits_reserve_ten_minutes_before_workflow_timeout(self):
        unsafe = config(daily_time_budget=2_400.0, openai_timeout=600.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                pipeline.ConfigurationError,
                "leave ten minutes",
            ):
                pipeline.run_daily(
                    unsafe,
                    state_path=root / "state.json",
                    output_dir=root / "daily",
                    checked_at=CHECKED_AT,
                )
            self.assertFalse((root / "state.json").exists())

    def test_openai_client_disables_sdk_retries_and_sets_timeout(self):
        constructor_calls = []

        def fake_openai(**kwargs):
            constructor_calls.append(kwargs)
            return SimpleNamespace(responses=SimpleNamespace(create=None))

        with mock.patch.dict(
            sys.modules,
            {"openai": SimpleNamespace(OpenAI=fake_openai)},
        ):
            pipeline.ResponsesAnalyzer(config(openai_timeout=37.5))

        self.assertEqual(
            constructor_calls,
            [{"timeout": 37.5, "max_retries": 0}],
        )


if __name__ == "__main__":
    unittest.main()
