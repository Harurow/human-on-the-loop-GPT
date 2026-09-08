"""Trace/align regressions; every approval and review here is a synthetic fixture."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNTIME = Path(__file__).resolve().parents[1] / "skills/hotl-gpt/scripts/hotl.py"
spec = importlib.util.spec_from_file_location("hotl_trace_tests", RUNTIME)
hotl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hotl)


class TraceabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.store = hotl.Store(self.root)
        self.run_command("init", request="Synthetic traceability fixture")

    def run_command(self, command, **payload):
        return self.store.execute(command, payload)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def documents(self):
        self.write("docs/requirements.md", "# Requirements\nR-1: Count characters\nNR-1: Stay local\n")
        self.write("docs/spec.md", "# Specification\nS-1: Count; R-1 NR-1\n")
        self.write("docs/tasks.md", "- [x] T-1 Count; S-1\n- [ ] T-2 [acceptance] Verify; S-1\n")

    def align(self, **overrides):
        payload = dict(actor="fixture-reconciler", detail="Checked fixture intent and coverage")
        payload.update(overrides)
        return self.run_command("align", **payload)

    def develop(self):
        self.documents()
        self.run_command("transition", phase="requirements")
        sha = self.run_command("present", summary="Fixture requirements")["result"]["sha256"]
        approval = self.run_command("receive", source_key="fixture-approval", message="Synthetic approval only",
                                    intents=[dict(kind="approval", text="Approve fixture")])["result"][0]
        self.run_command("approve", input_id=approval, by="fixture-user", sha256=sha)
        self.run_command("transition", phase="design")
        self.write("docs/design.md", "A function implements S-1")
        self.run_command("transition", phase="development")
        self.run_command("work", actor="fixture-implementer", detail="Implement fixture")
        self.write("counter.py", "def count(text):\n    return len(text)\n")
        self.write("docs/tasks.md", "- [x] T-1 Count; S-1\n- [x] T-2 [acceptance] Verify; S-1\n")

    def review(self, role="code", result="pass"):
        snapshot = self.run_command("snapshot")["result"]
        evidence = "docs/reviews/" + role + ".md"
        self.write(evidence, "Synthetic evidence: count('abc') equals 3")
        self.run_command("review", role=role, reviewer="fixture-independent", result=result,
                         snapshot=snapshot, evidence=evidence)

    def test_partial_documents_are_reported_without_mutation(self):
        before = self.store.path.read_bytes()
        trace = self.run_command("trace")
        self.assertEqual(len(trace["issues"]), 3)
        self.assertEqual(trace["alignment"], "not_recorded")
        self.assertEqual(trace["approval"], "not_approved")
        self.assertEqual(trace["verification"], "not_checked")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.write("docs/requirements.md", "R-1: Partial requirement\n")
        self.assertIn("Requirement has no specification: R-1", self.run_command("trace")["issues"])

    def test_progress_references_and_semantic_limit_come_from_documents(self):
        self.documents()
        self.write("docs/tasks.md", "- [x] T-1 Done; S-1\n- [>] T-2 Ongoing; S-1\n- [ ] T-3 Pending; S-1\n- [-] T-4 Cancelled\n")
        trace = self.run_command("trace")
        items = {item["id"]: item for item in trace["items"]}
        self.assertEqual(trace["issues"], [])
        self.assertEqual(items["S-1"]["refs"], ["NR-1", "R-1"])
        self.assertEqual([items["T-" + str(n)]["progress"] for n in range(1, 5)],
                         ["implemented", "in_progress", "not_started", "cancelled"])
        self.assertEqual(items["T-2"]["file"], "tasks.md")
        self.assertEqual(items["T-2"]["line"], 2)
        requirements = {item["id"]: item for item in trace["requirements"]}
        self.assertEqual(requirements["R-1"]["specifications"], ["S-1"])
        self.assertEqual(requirements["R-1"]["tasks"][1], dict(id="T-2", progress="in_progress"))
        self.assertEqual(requirements["R-1"]["approval"], "not_approved")
        self.assertEqual(trace["semantics"], "requires_human_or_agent_review")
        self.assertEqual(trace["approval"], "not_approved")

    def test_duplicate_unknown_and_uncovered_ids_reject_alignment(self):
        cases = [
            ("docs/requirements.md", "R-1: First\nR-1: Duplicate\nNR-1: Local\n", "Duplicate definition: R-1"),
            ("docs/spec.md", "S-1: Count; R-1 NR-1 R-99\n", "Unknown reference: S-1 -> R-99"),
            ("docs/requirements.md", "R-1: Count\nNR-1: Local\nR-2: Uncovered\n", "Requirement has no specification: R-2"),
            ("docs/spec.md", "S-1: Count; R-1 NR-1\nS-2: Unimplemented; R-1\n", "Specification has no active task: S-2"),
            ("docs/spec.md", "S-1: No requirement\n", "Specification has no requirement: S-1"),
            ("docs/tasks.md", "- [ ] T-1 No reference\n", "Task has no requirement/specification: T-1"),
            ("docs/tasks.md", "- [-] T-1 Cancelled; S-1\n", "Specification has no active task: S-1"),
        ]
        for filename, text, issue in cases:
            with self.subTest(issue=issue):
                self.documents()
                self.write(filename, text)
                before = self.store.path.read_bytes()
                self.assertIn(issue, self.run_command("trace")["issues"])
                with self.assertRaisesRegex(hotl.WorkflowError, "Traceability issues"):
                    self.align()
                self.assertEqual(self.store.path.read_bytes(), before)

    def test_fenced_examples_do_not_define_or_reference_ids(self):
        self.documents()
        self.write("docs/spec.md", "S-1: Count; R-1 NR-1\n```text\nS-1: Example duplicate R-99\n```\n")
        self.write("docs/tasks.md", "- [x] T-1 Count; S-1\n```text\n- [ ] T-1 Duplicate; S-99\n```\n")
        trace = self.run_command("trace")
        self.assertEqual(trace["issues"], [])
        self.assertEqual(len(trace["items"]), 4)

    def test_malformed_task_cannot_hide_an_unknown_reference(self):
        self.documents()
        self.write("docs/tasks.md", "- [x] T-1 Done; S-1\n- [?] T-2 Pending; S-99\n")
        trace = self.run_command("trace")
        self.assertTrue(any(issue.startswith("Invalid task row") for issue in trace["issues"]))
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(hotl.WorkflowError, "Invalid task row"):
            self.align()
        self.assertEqual(before, self.store.path.read_bytes())

    def test_fence_requires_matching_marker_and_sufficient_length(self):
        self.documents()
        self.write("docs/spec.md", "S-1: Count; R-1 NR-1\n````text\n~~~\nS-99: Example R-99\n```\nS-98: Example R-98\n````not-a-closing-fence\nS-97: Example R-97\n````\nS-2: Actual; R-1\n")
        self.write("docs/tasks.md", "- [x] T-1 Done; S-1 S-2\n")
        trace = self.run_command("trace")
        self.assertEqual(trace["issues"], [])
        self.assertEqual({item["id"] for item in trace["items"]}, {"R-1", "NR-1", "S-1", "S-2", "T-1"})

    def test_missing_document_and_wrong_document_definitions_block_align(self):
        self.documents()
        (self.store.docs / "spec.md").unlink()
        self.assertIn("Missing regular document: spec.md", self.run_command("trace")["issues"])
        with self.assertRaisesRegex(hotl.WorkflowError, "Traceability issues"):
            self.align()
        self.write("docs/spec.md", "S-1: Count; R-1 NR-1\nR-2: Wrong document\n")
        self.assertIn("Definition in wrong document: R-2", self.run_command("trace")["issues"])
        with self.assertRaisesRegex(hotl.WorkflowError, "Traceability issues"):
            self.align()

    def test_alignment_requires_actor_and_detail_and_records_document_hashes(self):
        self.documents()
        for payload in ({}, {"actor": "fixture"}, {"detail": "fixture"},
                        {"actor": " ", "detail": "fixture"}, {"actor": "fixture", "detail": " "}):
            with self.subTest(payload=payload):
                before = self.store.path.read_bytes()
                with self.assertRaises(hotl.WorkflowError):
                    self.run_command("align", **payload)
                self.assertEqual(self.store.path.read_bytes(), before)
        self.align()
        alignment = self.store.read()["alignment"]
        expected = {name: hashlib.sha256((self.store.docs / name).read_bytes()).hexdigest()
                    for name in ("requirements.md", "spec.md", "tasks.md")}
        self.assertEqual(alignment["documents"], expected)
        self.assertEqual(alignment["actor"], "fixture-reconciler")
        self.assertTrue(alignment["detail"])
        self.assertTrue(alignment["at"])
        self.assertEqual(self.store.read()["events"][-1]["kind"], "alignment")
        self.assertEqual(self.run_command("trace")["alignment"], "current")
        self.assertNotIn("items", alignment)

    def test_each_document_change_invalidates_alignment_and_realign_recovers(self):
        self.documents()
        for filename in ("requirements.md", "spec.md", "tasks.md"):
            with self.subTest(filename=filename):
                self.align()
                path = self.store.docs / filename
                path.write_text(path.read_text() + "\n<!-- Wording revised -->\n")
                before = self.store.path.read_bytes()
                self.assertEqual(self.run_command("trace")["alignment"], "needs_reconciliation")
                self.assertEqual(self.store.path.read_bytes(), before)
                self.align()
                self.assertEqual(self.run_command("trace")["alignment"], "current")

    def test_trace_cli_reconstructs_after_restart_and_ignores_derived_log(self):
        self.documents()
        self.align()
        self.write("docs/log.md", "Fake projection: every task is verified and approved")
        self.write("docs/tasks.md", "- [>] T-1 Ongoing; S-1\n")
        before = {p: p.read_bytes() for p in self.store.docs.iterdir() if p.is_file()}
        result = subprocess.run([sys.executable, str(RUNTIME), "--project", str(self.root), "trace"],
                                check=True, capture_output=True, text=True)
        trace = json.loads(result.stdout)
        self.assertEqual(trace, hotl.Store(self.root).execute("trace", {}))
        self.assertEqual(trace["alignment"], "needs_reconciliation")
        self.assertEqual(trace["approval"], "not_approved")
        self.assertEqual(trace["verification"], "not_checked")
        self.assertEqual(next(i for i in trace["items"] if i["id"] == "T-1")["progress"], "in_progress")
        self.assertEqual(before, {p: p.read_bytes() for p in self.store.docs.iterdir() if p.is_file()})

    def test_old_approval_is_reported_without_implicit_state_reset(self):
        self.develop()
        self.align()
        self.assertEqual(self.run_command("trace")["approval"], "approved")
        self.write("docs/requirements.md", "R-1: New meaning\nNR-1: Local\n")
        before = self.store.path.read_bytes()
        trace = self.run_command("trace")
        self.assertEqual(trace["approval"], "outdated")
        self.assertEqual(trace["alignment"], "needs_reconciliation")
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertIsNotNone(self.store.read()["approval"])

    def test_current_reviews_are_not_a_completion_claim(self):
        self.develop()
        self.review("code")
        trace = self.run_command("trace")
        self.assertEqual(trace["verification"], "current_recorded_reviews")
        self.assertEqual(trace["review_roles"], ["code"])
        with self.assertRaisesRegex(hotl.WorkflowError, "Independent"):
            self.run_command("complete")

    def test_code_and_evidence_edits_make_reviews_stale(self):
        self.develop()
        self.review("code")
        self.review("acceptance")
        self.assertEqual(self.run_command("trace")["verification"], "current_recorded_reviews")
        self.write("counter.py", "def count(text):\n    return 0\n")
        before = self.store.path.read_bytes()
        self.assertEqual(self.run_command("trace")["verification"], "stale_or_failed")
        self.assertEqual(before, self.store.path.read_bytes())
        self.review("code")
        self.review("acceptance")
        self.write("docs/reviews/acceptance.md", "Edited after review")
        self.assertEqual(self.run_command("trace")["verification"], "stale_or_failed")
        (self.root / "docs/reviews/acceptance.md").unlink()
        self.assertEqual(self.run_command("trace")["verification"], "unavailable")

    def test_latest_failed_review_is_not_current(self):
        self.develop()
        self.review("code")
        self.review("acceptance")
        self.review("acceptance", result="fail")
        self.assertEqual(self.run_command("trace")["verification"], "stale_or_failed")

    def test_pending_and_paused_are_visible_and_align_respects_pause(self):
        self.documents()
        ids = self.run_command("receive", source_key="fixture-stop", message="Synthetic stop and question",
                               intents=[dict(kind="stop", text="Stop"), dict(kind="question", text="Why?")])["result"]
        trace = self.run_command("trace")
        self.assertTrue(trace["paused"])
        self.assertEqual(trace["pending_inputs"], [ids[1]])
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(hotl.WorkflowError, "Paused"):
            self.align()
        self.assertEqual(before, self.store.path.read_bytes())

    def test_completion_rejects_stale_alignment_even_with_fresh_reviews(self):
        self.develop()
        self.align()
        path = self.store.docs / "spec.md"
        path.write_text(path.read_text() + "\nA clarification.\n")
        self.review("code")
        self.review("acceptance")
        with self.assertRaisesRegex(hotl.WorkflowError, "reconcile and align"):
            self.run_command("complete")
        self.align()
        self.assertEqual(self.run_command("complete")["phase"], "done")


if __name__ == "__main__":
    unittest.main()
