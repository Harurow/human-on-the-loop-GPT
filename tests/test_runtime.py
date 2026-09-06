import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skills/hotl-gpt/scripts/hotl.py"
spec = importlib.util.spec_from_file_location("hotl", RUNTIME)
hotl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hotl)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.store = hotl.Store(self.root)
        self.run_command("init", request="Create a local counter")
        self.source_number = 0

    def run_command(self, command, **payload):
        return self.store.execute(command, payload)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def receive(self, *kinds):
        self.source_number += 1
        return self.run_command("receive", source_key="message-" + str(self.source_number),
                                message="A real fixture message: " + ", ".join(kinds),
                                intents=[dict(kind=k, text="User says " + k) for k in kinds])["result"]

    def present(self):
        self.run_command("transition", phase="requirements")
        self.write("docs/requirements.md", "# Requirements\nR-1: Count characters\n")
        return self.run_command("present", summary="Count local characters")["result"]["sha256"]

    def develop(self):
        sha = self.present()
        item = self.receive("approval")[0]
        self.run_command("approve", input_id=item, by="fixture-user", sha256=sha)
        self.write("docs/spec.md", "S-1 counts input; R-1\n")
        self.run_command("transition", phase="design")
        self.write("docs/design.md", "One Python function for S-1\n")
        self.run_command("transition", phase="development")
        self.run_command("work", actor="implementation-context", detail="Implement counter")
        self.write("counter.py", "def count(text):\n    return len(text)\n")
        self.write("docs/tasks.md", "- [x] T-1 Implement counter\n- [x] T-2 [acceptance] Verify\n")

    def review(self, role="acceptance", reviewer="independent-context", result="pass"):
        snapshot = self.run_command("snapshot")["result"]
        evidence = "docs/reviews/" + role + ".md"
        self.write(evidence, "Fixture verification evidence; counter empty=0, abc=3\n")
        return self.run_command("review", role=role, reviewer=reviewer, result=result,
                                snapshot=snapshot, evidence=evidence)

    def finish(self):
        self.review("code")
        self.review()
        return self.run_command("complete")

    def editorial(self, **overrides):
        evidence = "docs/reviews/editorial.md"
        self.write(evidence, "README spelling corrected; cumulative diff inspected; no behavior change.")
        payload = dict(role="editorial", reviewer="implementation-context", result="pass",
                       snapshot=self.run_command("snapshot")["result"], evidence=evidence,
                       no_behavior_change=True, reason="Spelling only")
        payload.update(overrides)
        return self.run_command("review", **payload)

    def reopen_editorial(self):
        bug = self.receive("bug")[0]
        self.run_command("reopen", input_id=bug)
        self.write("docs/tasks.md", "- [x] T-1 Implement\n- [ ] T-3 Fix typo\n- [ ] T-2 [acceptance] Verify\n")
        self.run_command("resolve", input_id=bug, outcome="task", task_id="T-3", detail="Typo task")
        self.run_command("work", actor="implementation-context", detail="Fix typo")
        self.write("README.md", "A character counter.\n")
        self.write("docs/tasks.md", "- [x] T-1 Implement\n- [x] T-3 Fix typo\n- [x] T-2 [acceptance] Verify\n")

    def test_editorial_completion_preserves_independent_baseline(self):
        self.develop()
        self.finish()
        baseline = self.store.read()["review_baseline"]
        self.reopen_editorial()
        self.editorial()
        self.assertEqual(self.run_command("complete")["phase"], "done")
        self.assertEqual(self.store.read()["review_baseline"], baseline)
        self.reopen_editorial()
        self.write("README.md", "A local character counter.\n")
        self.editorial()
        self.assertEqual(self.run_command("complete")["phase"], "done")

    def test_editorial_requires_baseline_and_explicit_attestation(self):
        self.develop()
        self.write("README.md", "Counter")
        with self.assertRaisesRegex(hotl.WorkflowError, "baseline"):
            self.editorial()
        self.finish()
        self.reopen_editorial()
        with self.assertRaisesRegex(hotl.WorkflowError, "Confirm"):
            self.editorial(no_behavior_change=False)
        with self.assertRaises(hotl.WorkflowError):
            self.editorial(reason="")

    def test_editorial_rejects_code_contract_config_and_instruction_changes(self):
        self.develop()
        self.finish()
        self.reopen_editorial()
        for name in ("counter.py", "docs/spec.md", "docs/design.md", "config.json",
                     "docs/guide/AGENTS.md", "docs/help/SKILL.md"):
            with self.subTest(name=name):
                path = self.root / name
                previous = path.read_bytes() if path.exists() else None
                self.write(name, "Changed")
                with self.assertRaisesRegex(hotl.WorkflowError, "cannot cover"):
                    self.editorial()
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)

    def test_editorial_rejects_stale_snapshot_and_modified_evidence(self):
        self.develop()
        self.finish()
        self.reopen_editorial()
        self.editorial()
        self.write("README.md", "Another wording")
        with self.assertRaisesRegex(hotl.WorkflowError, "outdated"):
            self.run_command("complete")
        self.editorial()
        self.write("docs/reviews/editorial.md", "Changed evidence")
        with self.assertRaisesRegex(hotl.WorkflowError, "evidence changed"):
            self.run_command("complete")

    def test_editorial_checks_cumulative_code_changes_at_completion(self):
        self.develop()
        self.finish()
        self.reopen_editorial()
        self.editorial()
        self.run_command("complete")
        self.reopen_editorial()
        self.editorial()
        self.write("counter.py", "def count(text):\n    return 0\n")
        with self.assertRaisesRegex(hotl.WorkflowError, "cannot cover: counter.py"):
            self.run_command("complete")

    def test_editorial_rejects_document_deletion_and_executable(self):
        self.develop()
        self.write("docs/help/start.md", "Counter help")
        self.finish()
        self.reopen_editorial()
        help_path = self.root / "docs/help/start.md"
        help_path.unlink()
        with self.assertRaisesRegex(hotl.WorkflowError, "cannot cover"):
            self.editorial()
        self.write("docs/help/start.md", "Counter help")
        (self.root / "README.md").chmod(0o755)
        with self.assertRaisesRegex(hotl.WorkflowError, "cannot cover"):
            self.editorial()

    def test_editorial_cannot_bypass_failure_or_requirement_reset(self):
        self.develop()
        self.finish()
        self.reopen_editorial()
        self.review(result="fail")
        self.run_command("work", actor="implementation-context", detail="Retry")
        with self.assertRaisesRegex(hotl.WorkflowError, "baseline"):
            self.editorial()
        self.finish()
        self.run_command("reset", reason="New requirements", phase="requirements")
        self.assertNotIn("review_baseline", self.store.read())

    def test_editorial_cannot_bypass_pending_inputs_tasks_or_work_invalidation(self):
        self.develop()
        self.finish()
        self.reopen_editorial()
        self.editorial()
        self.run_command("work", actor="implementation-context", detail="More editing")
        with self.assertRaisesRegex(hotl.WorkflowError, "Independent"):
            self.run_command("complete")
        self.editorial()
        pending = self.receive("question")[0]
        with self.assertRaisesRegex(hotl.WorkflowError, "Unresolved"):
            self.run_command("complete")
        self.run_command("resolve", input_id=pending, outcome="answered", detail="Answered")
        self.write("docs/tasks.md", "- [ ] T-2 [acceptance] Verify\n")
        self.editorial()
        with self.assertRaisesRegex(hotl.WorkflowError, "Incomplete"):
            self.run_command("complete")

    def test_full_lifecycle_and_bug_reopen(self):
        self.develop()
        self.assertEqual(self.finish()["phase"], "done")
        bug = self.receive("bug")[0]
        self.run_command("reopen", input_id=bug)
        self.write("docs/tasks.md", "- [x] T-1 Implement\n- [ ] T-3 Fix bug\n- [ ] T-2 [acceptance] Verify\n")
        self.run_command("resolve", input_id=bug, outcome="task", task_id="T-3", detail="Registered")
        self.assertEqual(self.run_command("status")["phase"], "development")
        self.assertTrue(self.run_command("status")["approval_valid"])
        self.assertEqual(self.run_command("status")["reviews"], [])

    def test_mixed_approval_does_not_resolve_bug(self):
        sha = self.present()
        approval, bug = self.receive("approval", "bug")
        self.run_command("approve", input_id=approval, by="user", sha256=sha)
        self.assertEqual([i["id"] for i in self.run_command("status")["pending"]], [bug])

    def test_mixed_change_blocks_approval(self):
        sha = self.present()
        approval, change = self.receive("approval", "change")
        with self.assertRaisesRegex(hotl.WorkflowError, "Unresolved requirement change"):
            self.run_command("approve", input_id=approval, by="user", sha256=sha)
        self.assertEqual(len(self.run_command("status")["pending"]), 2)

    def test_presented_version_cannot_change(self):
        sha = self.present()
        approval = self.receive("approval")[0]
        self.write("docs/requirements.md", "Changed requirements")
        with self.assertRaisesRegex(hotl.WorkflowError, "differ"):
            self.run_command("approve", input_id=approval, by="user", sha256=sha)

    def test_old_approval_cannot_approve_new_presentation(self):
        self.present()
        approval = self.receive("approval")[0]
        sha = self.run_command("present", summary="Re-presented version")["result"]["sha256"]
        with self.assertRaisesRegex(hotl.WorkflowError, "predates"):
            self.run_command("approve", input_id=approval, by="user", sha256=sha)
        self.run_command("dismiss", input_id=approval, outcome="superseded", detail="Re-presented to user")
        self.assertEqual(self.run_command("status")["pending"], [])

    def test_resume_is_not_approval(self):
        sha = self.present()
        item = self.receive("resume")[0]
        self.run_command("resume", input_id=item)
        self.assertEqual(self.run_command("status")["phase"], "awaiting_approval")
        with self.assertRaises(hotl.WorkflowError):
            self.run_command("approve", input_id=item, by="user", sha256=sha)

    def test_tampering_readonly_status_and_persistent_reset(self):
        self.develop()
        self.write("docs/requirements.md", "Changed after approval")
        before = self.store.path.read_bytes()
        self.assertFalse(self.run_command("status")["approval_valid"])
        self.assertEqual(before, self.store.path.read_bytes())
        with self.assertRaisesRegex(hotl.WorkflowError, "invalidated"):
            self.run_command("check")
        state = self.store.read()
        self.assertIsNone(state["approval"])
        self.assertEqual(state["phase"], "requirements")
        self.assertEqual(state["events"][-1]["kind"], "approval-reset")

    def test_stop_is_recorded_even_when_approved_document_changed(self):
        self.develop()
        self.write("docs/requirements.md", "Changed after approval")
        self.receive("stop")
        self.assertTrue(self.run_command("status")["paused"])
        with self.assertRaisesRegex(hotl.WorkflowError, "invalidated"):
            self.run_command("check")
        self.assertTrue(self.run_command("status")["paused"])

    def test_required_documents_remain_in_snapshot_when_ignored(self):
        self.develop()
        self.write(".gitignore", "docs/\n")
        before = self.run_command("snapshot")["result"]
        self.write("docs/tasks.md", "- [ ] T-1 Fix\n- [ ] T-2 [acceptance] Verify\n")
        self.assertNotEqual(before, self.run_command("snapshot")["result"])

    def test_duplicate_message_is_idempotent(self):
        payload = dict(source_key="stable-message", message="stop and question", intents=[
            dict(kind="stop", text="stop"), dict(kind="question", text="status?")])
        first = self.run_command("receive", **payload)
        second = self.run_command("receive", **payload)
        self.assertEqual(first, second)
        payload["message"] = "different"
        with self.assertRaisesRegex(hotl.WorkflowError, "different content"):
            self.run_command("receive", **payload)

    def test_individual_resolution_and_stop(self):
        self.develop()
        question, instruction, stop = self.receive("question", "instruction", "stop")
        self.run_command("resolve", input_id=question, outcome="answered", detail="Answered")
        self.assertEqual([i["id"] for i in self.run_command("status")["pending"]], [instruction])
        with self.assertRaisesRegex(hotl.WorkflowError, "Paused"):
            self.run_command("work", actor="implementation-context", detail="Must not run")
        item = self.receive("resume")[0]
        self.run_command("resume", input_id=item)
        self.assertFalse(self.run_command("status")["paused"])

    def test_old_resume_cannot_override_new_stop(self):
        old_resume = self.receive("resume")[0]
        self.receive("stop")
        with self.assertRaisesRegex(hotl.WorkflowError, "predates the latest stop"):
            self.run_command("resume", input_id=old_resume)
        self.assertTrue(self.run_command("status")["paused"])
        self.run_command("dismiss", input_id=old_resume, outcome="superseded", detail="Later stop wins")
        current_resume = self.receive("resume")[0]
        self.run_command("resume", input_id=current_resume)
        self.assertFalse(self.run_command("status")["paused"])

    def test_process_failure_before_replace_preserves_all_state(self):
        before = self.store.path.read_bytes()
        with patch.object(hotl.os, "replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                self.receive("approval", "bug")
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertFalse(list(self.store.docs.glob(".hotl-tmp-*")))

    def test_process_crash_after_replace_keeps_valid_canonical_state(self):
        code = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('runtime', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
original = m.os.replace
def crash(source, target):
    original(source, target)
    os._exit(77)
m.os.replace = crash
m.Store(sys.argv[2]).execute('note', {'kind':'decision','text':'durable event'})
"""
        result = subprocess.run([sys.executable, "-c", code, str(RUNTIME), str(self.root)])
        self.assertEqual(result.returncode, 77)
        self.assertEqual(self.store.read()["events"][-1]["text"], "durable event")
        self.assertTrue(self.run_command("status")["log_stale"])
        self.run_command("sync")
        self.assertFalse(self.run_command("status")["log_stale"])
        self.run_command("note", kind="report", text="Lock released after crash")

    def test_projection_failure_repaired_without_changing_state(self):
        original = hotl.atomic_write
        def fail_log(path, data):
            if path.name == "log.md":
                raise OSError("disk failure")
            original(path, data)
        with patch.object(hotl, "atomic_write", side_effect=fail_log):
            self.run_command("note", kind="report", text="Persist once")
        before = self.store.path.read_bytes()
        self.assertTrue(self.run_command("status")["log_stale"])
        self.run_command("sync")
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertFalse(self.run_command("status")["log_stale"])

    def test_stale_revision_and_concurrent_writer_rejected(self):
        revision = self.store.read()["revision"]
        self.run_command("note", kind="report", text="Newer update")
        with self.assertRaisesRegex(hotl.WorkflowError, "Stale revision"):
            self.store.execute("note", dict(kind="report", text="Stale"), revision)
        with self.store.lock():
            result = subprocess.run([sys.executable, str(RUNTIME), "--project", str(self.root), "check"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"Another workflow command", result.stderr)

    def test_complete_rejects_missing_or_self_reviews(self):
        self.develop()
        with self.assertRaisesRegex(hotl.WorkflowError, "Independent"):
            self.run_command("complete")
        with self.assertRaisesRegex(hotl.WorkflowError, "also implemented"):
            self.review(reviewer="implementation-context")

    def test_complete_rejects_stale_code_and_evidence(self):
        self.develop()
        self.review("code")
        self.review()
        self.write("counter.py", "broken = True\n")
        with self.assertRaisesRegex(hotl.WorkflowError, "outdated"):
            self.run_command("complete")
        self.review("code")
        self.review()
        self.write("docs/reviews/acceptance.md", "changed evidence")
        with self.assertRaisesRegex(hotl.WorkflowError, "evidence changed"):
            self.run_command("complete")

    def test_final_task_and_pending_input_gates(self):
        self.develop()
        self.receive("bug")
        with self.assertRaisesRegex(hotl.WorkflowError, "Unresolved"):
            self.run_command("complete")
        with self.assertRaisesRegex(hotl.WorkflowError, "unfinished task"):
            self.run_command("resolve", input_id="I-2", outcome="task", task_id="T-1", detail="Already finished")
        self.write("docs/tasks.md", "- [ ] T-1 Investigate bug\n- [ ] T-2 [acceptance] Verify\n")
        self.run_command("resolve", input_id="I-2", outcome="task", task_id="T-1", detail="Reopened task investigates behavior")
        self.write("docs/tasks.md", "- [x] T-1 Implement\n- [ ] T-2 [acceptance] Verify\n")
        with self.assertRaisesRegex(hotl.WorkflowError, "Incomplete"):
            self.run_command("complete")
        self.write("docs/tasks.md", "- [x] T-2 [acceptance] Verify\n- [x] T-3 More code\n")
        with self.assertRaisesRegex(hotl.WorkflowError, "Last active"):
            self.run_command("complete")

    def test_failed_latest_review_blocks_done(self):
        self.develop()
        self.review("code")
        self.review()
        self.review(result="fail")
        with self.assertRaisesRegex(hotl.WorkflowError, "Failed"):
            self.run_command("complete")

    def test_work_invalidates_previous_reviews(self):
        self.develop()
        self.review("code")
        self.run_command("work", actor="second-implementation-context", detail="A repair")
        self.assertEqual(self.store.read()["reviews"], [])
        with self.assertRaisesRegex(hotl.WorkflowError, "also implemented"):
            self.review(reviewer="second-implementation-context")

    def test_rejects_existing_original_state_and_corruption(self):
        self.store.path.write_text(json.dumps(dict(framework="human-on-the-loop", version=1)))
        with self.assertRaisesRegex(hotl.WorkflowError, "Unsupported"):
            self.run_command("status")
        self.store.path.write_text("broken")
        with self.assertRaises(ValueError):
            self.run_command("status")
        self.assertEqual(self.store.path.read_text(), "broken")

    def test_existing_docs_and_workspace_are_not_initialized(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "docs").mkdir()
            (root / "docs/requirements.md").write_text("User content")
            with self.assertRaisesRegex(hotl.WorkflowError, "Existing workflow"):
                hotl.Store(root).execute("init", {"request": "new"})
            self.assertEqual((root / "docs/requirements.md").read_text(), "User content")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "child/docs").mkdir(parents=True)
            (root / "child/docs/hotl.state.json").write_text("{}")
            with self.assertRaisesRegex(hotl.WorkflowError, "Workspace root"):
                hotl.Store(root).execute("init", {"request": "new"})

    def test_secrets_must_be_excluded_and_links_rejected(self):
        self.develop()
        self.write(".env", "SECRET=fixture-only")
        with self.assertRaisesRegex(hotl.WorkflowError, "Secret environment"):
            self.run_command("snapshot")
        self.write(".gitignore", ".env\n")
        self.run_command("snapshot")
        (self.root / "linked.py").symlink_to(self.root / "counter.py")
        with self.assertRaisesRegex(hotl.WorkflowError, "symlink"):
            self.run_command("snapshot")

    def test_noop_status_is_readonly_and_cli_errors_are_clear(self):
        state = copy.deepcopy(self.store.read())
        self.run_command("status")
        self.assertEqual(self.store.read(), state)
        result = subprocess.run([sys.executable, str(RUNTIME), "--project", str(self.root), "receive", "--input", "-"],
                                input=b"[]", capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"JSON object", result.stderr)


if __name__ == "__main__":
    unittest.main()
