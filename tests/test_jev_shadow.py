import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from scripts import jev_shadow as s


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for d in ("config", "research/checkpoints", "research/listings"):
            (self.root / d).mkdir(parents=True)
        (self.root / "config/research.json").write_text('{"categories":["q-fin.TR"]}')
        self.state = s.initial()
        self.entries = {}
        self.saved = []
        self.save = lambda state: self.saved.append(copy.deepcopy(state))
        self.fetch = lambda ids: {k: self.entries[k] for k in ids}
        self.judge = Mock(return_value=self.verdict())

    @staticmethod
    def verdict(rates=.05, execution=.02, practical=.05):
        return s.parse_answer({"model": s.MODEL, "usage": {"input_tokens": 123, "output_tokens": 3},
                               "answers": {k: {"type":"noul","noul": v} for k,v in
                                           zip(s.QUESTIONS, (rates, execution, practical))}})

    def batch(self, day, numbers):
        cp = {"batchDate":day, "candidateFingerprints":{}, "results":{}, "usage":[]}
        items = []
        for i in numbers:
            k = f"2611.{i:05d}"
            kind = "cross" if i % 2 else "new"
            items.append({"arxivId":k, "listingType":kind})
            dt = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
            e = s.research.digest.AtomEntry(k+"v1", "Test title", ("An Author",), dt, dt, ("q-fin.TR",), "Test abstract")
            self.entries[k] = e
            cp["candidateFingerprints"][k] = s.research._candidate_resume_fingerprint(
                s.research.PaperCandidate(e, (kind,), ("q-fin.TR",)))
            cp["results"][k] = {"screenAnalysis":{"classification":"rates" if i%2 else "out_of_scope",
                "importance":4, "recommended":False}, "finalAnalysis":{"classification":"out_of_scope"}}
            cp["usage"].append({"stage":"screen","outcome":"completed","paperIds":[e.arxiv_id],
                                "model":"gpt-6-luna","effort":"low"})
        items.append({"arxivId":"2601.00008","listingType":"replacement"})
        (self.root / f"research/checkpoints/{day}.json").write_text(json.dumps(cp))
        (self.root / f"research/listings/{day}.json").write_text(json.dumps({
            "schemaVersion":1,"batchDate":day,"capturedAt":day+"T12:00:00Z",
            "pages":[{"category":"q-fin.TR","items":items}]}))

    def test_cohort_cutoff_new_cross_cap_dedup_and_no_key(self):
        self.batch("2026-11-12", [900])
        self.batch("2026-11-13", range(1,62))
        self.batch("2026-11-16", range(40,130))
        s.run(self.state,self.root,self.save,"",Mock(side_effect=AssertionError),self.judge)
        self.judge.assert_not_called()
        self.assertEqual(list(self.state["rows"]), [f"2611.{i:05d}" for i in range(1,101)])
        # An overlap is still owned by the first batch, not a replacement.
        self.assertEqual(self.state["rows"]["2611.00040"]["batch"], "2026-11-13")
        self.assertEqual(self.state, self.saved[-1])

    def test_precommit_before_call_no_leakage_and_no_replay(self):
        self.batch("2026-11-13", [1,2])
        before = {p:p.read_bytes() for p in self.root.rglob("*.json")}
        def judge(key, e):
            self.assertEqual(key, "fixture")
            self.assertEqual(self.saved[-1]["rows"][e.arxiv_id[:-2]]["status"], "attempted")
            return self.verdict()
        s.run(self.state,self.root,self.save,"fixture",self.fetch,judge)
        s.run(self.state,self.root,self.save,"fixture",Mock(side_effect=AssertionError),self.judge)
        self.judge.assert_not_called()
        self.assertEqual(before, {p:p.read_bytes() for p in self.root.rglob("*.json")})
        text = s.audit(self.state)
        self.assertIn("2611.00001v1", text.split("Jevが落とす候補")[1].split("##")[0])
        self.assertIn("2611.00002v1", text.split("双方が除外：")[1])
        self.assertNotIn("Test abstract", str(self.state))

    def test_transport_and_input_failures_are_not_comparisons(self):
        self.batch("2026-11-13", [1,2,3])
        self.entries["2611.00001"] = self.entries["2611.00003"]
        err = Mock(side_effect=urllib.error.HTTPError("unused",402,"secret error text",{},None))
        s.run(self.state,self.root,self.save,"fixture",self.fetch,err)
        self.assertEqual([r["status"] for r in self.state["rows"].values()], ["input_mismatch","error","waiting"])
        s.run(self.state,self.root,self.save,"fixture",self.fetch,err)
        self.assertEqual(err.call_count,1)
        self.assertNotIn("secret error text",json.dumps(self.state))
        self.assertIn("比較済み **0**",s.audit(self.state))
        s.run(self.state, self.root, self.save, "fixture", self.fetch, self.judge, resume=True)
        self.judge.assert_called_once_with("fixture", self.entries["2611.00003"])
        self.assertIn("http_402", s.audit(self.state))

    def test_successful_trial_stops_at_exactly_100_even_after_more_listings(self):
        self.batch("2026-11-13", range(1, 110))
        s.run(self.state, self.root, lambda _: None, "fixture", self.fetch, self.judge)
        self.batch("2026-11-16", range(110, 120))
        never_fetch = Mock(side_effect=AssertionError)
        reason = s.run(self.state, self.root, self.save, "fixture", never_fetch, self.judge)
        self.assertIn("100 slots closed", reason)
        never_fetch.assert_not_called()
        self.assertEqual(self.judge.call_count, 100)
        self.assertEqual(len(self.state["rows"]), 100)

    def test_request_contract_uses_only_public_inputs(self):
        self.batch("2026-11-13", [1])
        response = {"model": s.MODEL, "usage": {"input_tokens": 24, "output_tokens": 3},
                    "answers": {k: {"type": "noul", "noul": .9} for k in s.QUESTIONS}}
        with patch.object(s.urllib.request, "build_opener") as factory:
            factory.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            result = s.ask("fixture", self.entries["2611.00001"])
        self.assertIsInstance(factory.call_args.args[0], s.NoRedirect)
        req = factory.return_value.open.call_args.args[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(req.get_header("Authorization"), "Bearer fixture")
        body = json.loads(req.data)
        self.assertEqual(set(body), {"model", "state", "questions"})
        self.assertEqual(set(body["questions"]), set(s.QUESTIONS))
        self.assertEqual(body["state"], {"title": "Test title", "abstract": "Test abstract", "categories": ["q-fin.TR"]})
        self.assertEqual(result["route"], "include")

    def test_ambiguous_post_not_retried_and_failed_reservation_never_sends(self):
        self.batch("2026-11-13", [1])
        def fail_on_attempt(state):
            if list(state["rows"].values())[0]["status"] == "attempted":
                raise OSError("durability failure")
        with self.assertRaises(OSError):
            s.run(self.state,self.root,fail_on_attempt,"fixture",self.fetch,self.judge)
        self.judge.assert_not_called()
        self.assertEqual(self.state["rows"]["2611.00001"]["status"], "attempted")
        s.run(self.state,self.root,self.save,"fixture",self.fetch,self.judge)
        self.judge.assert_not_called()

    def test_boundaries_and_malformed_answer(self):
        self.assertEqual(self.verdict(execution=.8)["route"],"include")
        self.assertEqual(self.verdict(practical=.8)["route"],"uncertain")
        self.assertEqual(self.verdict(rates=.2)["route"],"uncertain")
        for invalid in (-1, 1.1, float("nan"), True, "0.1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.verdict(rates=invalid)
