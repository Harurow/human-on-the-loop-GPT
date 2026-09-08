"""Isolated fixture coverage for human decisions and their generated projection."""
import json
import unittest
from unittest.mock import patch

import test_runtime as runtime

hotl = runtime.hotl


class UserCheckTests(unittest.TestCase):
    setUp = runtime.RuntimeTests.setUp
    run_command = runtime.RuntimeTests.run_command
    write = runtime.RuntimeTests.write
    receive = runtime.RuntimeTests.receive
    present = runtime.RuntimeTests.present
    develop = runtime.RuntimeTests.develop
    review = runtime.RuntimeTests.review
    finish = runtime.RuntimeTests.finish

    def payload(self, **changes):
        result = dict(key="fixture-layout", kind="decision", title="Choose layout",
                      reason="Two layouts meet the requirements", recommendation="Use compact",
                      blocking=True, options=["compact", "wide"], related=["R-1"])
        result.update(changes)
        return result

    def ask(self, **changes):
        return self.run_command("ask", **self.payload(**changes))["result"]

    def rows(self):
        return self.run_command("questions")["questions"]

    def answer(self, question_id, **changes):
        payload = dict(question_id=question_id, input_id=self.receive("instruction")[0],
                       answer="Use compact", outcome="accepted")
        payload.update(changes)
        return self.run_command("answer", **payload)

    def test_ask_is_canonical_and_duplicate_key_does_not_create_new_question(self):
        first = self.ask()
        self.assertEqual(self.ask(), first)
        self.assertEqual(len(self.store.read()["questions"]), 1)
        self.assertEqual(len([e for e in self.store.read()["events"] if e["kind"] == "question"]), 1)
        with self.assertRaisesRegex(hotl.WorkflowError, "different content"):
            self.ask(title="Different question")
        self.assertIn(first, (self.root / "docs/user-checks.md").read_text())
        before = self.store.path.read_bytes()
        self.rows()
        self.assertEqual(before, self.store.path.read_bytes())

    def test_required_fields_and_valid_kinds(self):
        for field in ("key", "kind", "title", "reason", "recommendation", "blocking"):
            payload = self.payload()
            del payload[field]
            with self.subTest(field=field), self.assertRaises(hotl.WorkflowError):
                self.run_command("ask", **payload)
        for field, value in (("blocking", 1), ("kind", "requirements_approval"),
                             ("options", "yes"), ("related", [""]), ("targets", "file")):
            with self.subTest(field=field), self.assertRaises(hotl.WorkflowError):
                self.ask(**{field: value})
        for kind in ("permission", "visual_check", "decision"):
            self.ask(key=kind, kind=kind)
        self.assertEqual(len(self.rows()), 3)

    def test_answer_closes_only_one_question_and_one_input(self):
        first, second = self.ask(), self.ask(key="another")
        answer, bug = self.receive("instruction", "bug")
        self.run_command("answer", question_id=first, input_id=answer, answer="compact", outcome="accepted")
        self.assertEqual([(q["id"], q["status"]) for q in self.rows()], [(first, "accepted"), (second, "open")])
        self.assertEqual([i["id"] for i in self.run_command("status")["pending"]], [bug])
        self.assertIsNone(self.store.read()["approval"])
        with self.assertRaises(hotl.WorkflowError):
            self.run_command("answer", question_id=second, input_id=answer, answer="same", outcome="accepted")
        self.assertEqual(self.rows()[1]["status"], "open")

    def test_answer_requires_new_instruction_and_explicit_answer_outcome(self):
        old = self.receive("instruction")[0]
        question = self.ask()
        for payload in (
            dict(input_id=old, answer="yes", outcome="accepted"),
            dict(input_id=self.receive("approval")[0], answer="yes", outcome="accepted"),
            dict(input_id=self.receive("instruction")[0], outcome="accepted"),
            dict(input_id=self.receive("instruction")[0], answer="yes"),
            dict(input_id=self.receive("instruction")[0], answer="yes", outcome="approved"),
            dict(answer="yes", outcome="accepted"),
        ):
            before = self.store.path.read_bytes()
            with self.subTest(payload=payload), self.assertRaises(hotl.WorkflowError):
                self.run_command("answer", question_id=question, **payload)
            self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(self.rows()[0]["status"], "open")

    def test_target_change_rejects_answer_and_duplicate_reuse(self):
        self.write("preview.txt", "Version one")
        question = self.ask(targets=["preview.txt"])
        self.write("preview.txt", "Version two")
        self.assertTrue(self.rows()[0]["stale"])
        with self.assertRaisesRegex(hotl.WorkflowError, "Target changed"):
            self.answer(question)
        with self.assertRaisesRegex(hotl.WorkflowError, "different content"):
            self.ask(targets=["preview.txt"])
        self.run_command("withdraw", question_id=question, detail="Preview superseded")
        new_question = self.ask(key="preview-v2", targets=["preview.txt"])
        self.assertNotEqual(question, new_question)
        self.assertFalse(self.rows()[1]["stale"])

    def test_target_deletion_and_unsafe_paths_rejected(self):
        self.write("preview.txt", "Fixture")
        question = self.ask(targets=["preview.txt"])
        (self.root / "preview.txt").unlink()
        with self.assertRaisesRegex(hotl.WorkflowError, "Target changed"):
            self.answer(question)
        self.write(".env", "FIXTURE_ONLY=true")
        self.write("plain.txt", "Fixture")
        (self.root / "linked.txt").symlink_to(self.root / "plain.txt")
        for target in ("../outside", ".env", "missing.txt", "linked.txt", str(self.root / "plain.txt")):
            with self.subTest(target=target), self.assertRaises(hotl.WorkflowError):
                self.ask(key="unsafe", targets=[target])

    def test_withdraw_requires_reason_and_does_not_accept_or_close_other_question(self):
        first, second = self.ask(), self.ask(key="second")
        with self.assertRaises(hotl.WorkflowError):
            self.run_command("withdraw", question_id=first)
        self.run_command("withdraw", question_id=first, detail="No longer applicable")
        self.assertEqual([q["status"] for q in self.rows()], ["withdrawn", "open"])
        self.assertIsNone(self.store.read()["approval"])
        self.assertEqual(self.ask(), first)
        self.assertEqual(self.rows()[0]["status"], "withdrawn")

    def test_presented_approval_is_derived_and_approve_only(self):
        sha = self.present()
        derived = self.rows()[0]
        self.assertEqual(derived["kind"], "requirements_approval")
        self.assertEqual(self.store.read().get("questions", []), [])
        with self.assertRaises(hotl.WorkflowError):
            self.answer(derived["id"])
        with self.assertRaises(hotl.WorkflowError):
            self.run_command("withdraw", question_id=derived["id"], detail="Wrong command")
        approval = self.receive("approval")[0]
        self.run_command("approve", input_id=approval, by="fixture-user", sha256=sha)
        self.assertEqual(self.rows(), [])

    def test_unanswered_blocking_prevents_completion_and_nonblocking_does_not(self):
        self.develop()
        first = self.ask()
        self.ask(key="optional", blocking=False)
        self.review("code")
        self.review()
        with self.assertRaisesRegex(hotl.WorkflowError, "Blocking"):
            self.run_command("complete")
        self.assertEqual([q["status"] for q in self.rows()], ["open", "open"])
        self.answer(first, outcome="declined", answer="Do not use compact")
        self.assertEqual(self.run_command("complete")["phase"], "done")
        self.assertEqual(self.rows()[1]["status"], "open")

    def test_old_state_without_questions_supports_projection_and_new_question(self):
        state = self.store.read()
        state.pop("questions", None)
        self.store.path.write_text(json.dumps(state))
        self.assertEqual(self.rows(), [])
        self.run_command("sync")
        self.ask()
        self.assertEqual(len(self.rows()), 1)

    def test_projection_failure_leaves_canonical_question_repairable(self):
        original = hotl.atomic_write
        def fail_projection(path, data):
            if path.name == "user-checks.md":
                raise OSError("fixture projection failure")
            original(path, data)
        with patch.object(hotl, "atomic_write", side_effect=fail_projection):
            question = self.ask()
        self.assertEqual(self.rows()[0]["id"], question)
        before = self.store.path.read_bytes()
        self.run_command("sync")
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertIn(question, (self.root / "docs/user-checks.md").read_text())

    def test_projection_preserves_existing_manual_document(self):
        self.write("docs/user-checks.md", "Manual fixture content")
        self.ask()
        self.assertEqual((self.root / "docs/user-checks.md").read_text(), "Manual fixture content")
        with self.assertRaisesRegex(hotl.WorkflowError, "not a generated file"):
            self.run_command("sync")
        self.assertEqual(len(self.rows()), 1)


if __name__ == "__main__":
    unittest.main()
