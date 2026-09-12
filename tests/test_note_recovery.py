"""Malformed note inputs must not poison the canonical event history."""
import json
import unittest
import test_runtime as runtime


class NoteRecoveryTests(unittest.TestCase):
    setUp = runtime.RuntimeTests.setUp
    run_command = runtime.RuntimeTests.run_command

    def test_invalid_related_rejected_without_any_persistent_change(self):
        before = {p.name: p.read_bytes() for p in self.store.docs.iterdir() if p.is_file()}
        for value in (["T-1"], [], {}, 1, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(runtime.hotl.WorkflowError, "related must be"):
                    self.run_command("note", kind="finding", text="Must not save", related=value)
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.docs.iterdir() if p.is_file()})

    def test_legacy_array_history_recovers_without_rewriting_state(self):
        self.run_command("note", kind="finding", text="Preserve this historical text", related="T-1")
        state = self.store.read()
        state["events"][-1]["related"] = ["T-1", "日本語"]
        # Emulate the state already persisted by the previous CLI before projection failed.
        self.store.path.write_text(json.dumps(state, ensure_ascii=False))
        before = self.store.path.read_bytes()
        self.run_command("status")
        self.run_command("sync")
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertIn('Related: ["T-1", "日本語"]', (self.store.docs / 'log.md').read_text())
        self.run_command("note", kind="report", text="New valid note", related="T-2")
        after = self.store.read()
        self.assertEqual(state['events'], after['events'][:-1])
        self.assertFalse(self.run_command("status")['log_stale'])

    def test_optional_and_string_related_still_supported(self):
        for value in (None, "", "I-2"):
            self.run_command("note", kind="decision", text="Valid", related=value)
        self.run_command("note", kind="report", text="Omitted")
        self.assertFalse(self.run_command("status")['log_stale'])
