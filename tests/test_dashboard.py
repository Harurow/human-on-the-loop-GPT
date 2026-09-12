"""Independent dashboard regression coverage using isolated canonical projects."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_runtime as runtime

hotl = runtime.hotl


class DashboardTests(unittest.TestCase):
    setUp = runtime.RuntimeTests.setUp
    run_command = runtime.RuntimeTests.run_command
    write = runtime.RuntimeTests.write
    receive = runtime.RuntimeTests.receive
    present = runtime.RuntimeTests.present
    develop = runtime.RuntimeTests.develop

    def config(self, **values):
        self.write("docs/dashboard.json", json.dumps(values))

    def canonical(self):
        return {name: (self.root / name).read_bytes() for name in
                ("docs/hotl.state.json", "docs/tasks.md", "docs/requirements.md")
                if (self.root / name).exists()}

    def page(self):
        result = self.run_command("dashboard")
        return Path(result["path"]).read_text()

    def test_dashboard_and_sync_preserve_approval_tasks_and_pending_inputs(self):
        self.develop()
        self.receive("instruction", "bug")
        before = self.canonical()
        for command in ("dashboard", "sync", "dashboard"):
            with self.subTest(command=command):
                result = self.run_command(command)
                self.assertEqual(self.canonical(), before)
                dashboard = result.get("dashboard", result)
                self.assertEqual(dashboard["source_sha256"], hashlib.sha256(before["docs/hotl.state.json"]).hexdigest())
                self.assertTrue(Path(dashboard["path"]).is_file())
        self.assertTrue(self.store.read()["approval"])

    def test_stale_approval_is_reported_without_invalidating_canonical_state(self):
        self.develop()
        self.write("docs/requirements.md", "R-1: Changed after approval\n")
        before = self.canonical()
        self.assertIn("承認版から変更", self.page())
        self.run_command("sync")
        self.assertEqual(before, self.canonical())

    def test_questions_are_first_and_tasks_keep_canonical_order(self):
        self.write("docs/tasks.md", "- [x] T-1 done\n- [>] T-2 CURRENT\n- [ ] T-4 NEXT_FIRST; 依存: T-1\n- [ ] T-3 NEXT_SECOND\n- [ ] T-5 HELD_DEPENDENCY; 依存: T-2\n- [ ] T-6 HELD_REASON; 保留: 資料待ち\n")
        for key, blocking in (("optional", False), ("blocking", True)):
            self.run_command("ask", key=key, kind="decision", title=key,
                             reason="Explain <script>alert(1)</script>", recommendation="Use A",
                             blocking=blocking, options=["A", "B"], related=["T-2"])
        before = self.canonical()
        page = self.page()
        self.assertLess(page.index('id="checks"'), page.index('id="current"'))
        self.assertLess(page.index('· blocking'), page.index('· optional'))
        current = page.split('id="current"')[1].split('</section>')[0]
        upcoming = page.split('id="next"')[1].split('</section>')[0]
        waiting = page.split('id="waiting"')[1].split('</section>')[0]
        self.assertIn("CURRENT", current)
        self.assertLess(upcoming.index("NEXT_FIRST"), upcoming.index("NEXT_SECOND"))
        self.assertNotIn("HELD_", upcoming)
        self.assertIn("HELD_DEPENDENCY", waiting)
        self.assertIn("HELD_REASON", waiting)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<form", page)
        self.assertEqual(before, self.canonical())

    def test_ui_false_and_configuration_overrides(self):
        self.config(project_name="Custom <name>", title="Delivery board", workbench_dir="local-review", ui=False)
        result = self.run_command("dashboard")
        page = Path(result["path"]).read_text()
        self.assertEqual(Path(result["path"]), self.store.root / "local-review/dashboard/index.html")
        self.assertIn("Custom &lt;name&gt;", page)
        self.assertIn("Delivery board", page)
        self.assertIn("UIなし", page)
        self.assertIn("/local-review/", (self.root / ".gitignore").read_text())
        self.assertIn("../local-review/dashboard/index.html", (self.root / "docs/handoff.md").read_text())

    def test_current_capture_is_separate_from_retained_history(self):
        self.write("workbench/captures/current.png", "current original")
        self.write("workbench/captures/old.png", "old original")
        self.write("workbench/captures/demo.mp4", "video original")
        self.write("docs/evidence.md", "Checked manually")
        self.config(screens=[dict(id="main", name="Main", current=["workbench/captures/current.png", "workbench/captures/demo.mp4"], captures=[
            dict(path="workbench/captures/current.png", kind="image", adoption="accepted", connected=True, verification="visual", evidence="docs/evidence.md"),
            dict(path="workbench/captures/demo.mp4", kind="video", adoption="candidate", connected=False, verification="device"),
            dict(path="workbench/captures/old.png", kind="image", adoption="archived", verification="test")])])
        page = self.page()
        self.assertIn('<img loading="lazy" src="../captures/current.png"', page)
        self.assertIn('<video controls preload="none" src="../captures/demo.mp4"', page)
        self.assertNotIn('src="../captures/old.png"', page)
        self.assertIn('href="../captures/old.png"', page)
        for text in ("過去・非表示資料", "採用", "本編接続済みの記録", "目視確認の記録", "本編未接続", "実機確認の記録"):
            self.assertIn(text, page)
        self.page()
        self.assertEqual((self.root / "workbench/captures/old.png").read_text(), "old original")

    def test_missing_capture_is_not_silently_claimed_as_present(self):
        self.config(screens=[dict(id="main", name="Main", current=["workbench/missing.png"], captures=[dict(path="workbench/missing.png", kind="image")])])
        page = self.page()
        self.assertIn("未作成・ローカル未配置", page)
        self.assertNotIn("<img ", page)
        self.assertIn("未検証", page)

    def test_project_renderer_preserves_custom_html_and_documents_command(self):
        self.write("workbench/own/index.html", "<!doctype html><h1>Custom</h1>")
        self.config(renderer="project", entrypoint="workbench/own/index.html", regenerate="python3 build.py")
        before = self.canonical()
        for command in ("dashboard", "sync"):
            result = self.run_command(command)
            self.assertFalse(result.get("dashboard", result)["generated"])
        self.assertEqual((self.root / "workbench/own/index.html").read_text(), "<!doctype html><h1>Custom</h1>")
        self.assertFalse((self.root / "workbench/dashboard/index.html").exists())
        self.assertIn("python3 build.py", (self.root / "docs/handoff.md").read_text())
        self.assertEqual(self.canonical(), before)

    def test_git_ignored_and_handoff_updates_are_idempotent(self):
        self.write(".gitignore", "existing/\n")
        self.write("docs/handoff.md", "# Existing handoff\nKeep this human text.\n")
        self.page()
        first = (self.root / "docs/handoff.md").read_bytes()
        self.page()
        self.assertEqual((self.root / "docs/handoff.md").read_bytes(), first)
        self.assertIn(b"Keep this human text.", first)
        self.assertEqual((self.root / ".gitignore").read_text(), "existing/\n/workbench/\n")
        result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "workbench/dashboard/index.html"], capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_existing_gitignore_negation_cannot_leave_dashboard_trackable(self):
        self.write(".gitignore", "/workbench/\n!/workbench/\n")
        self.page()
        result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "workbench/dashboard/index.html"], capture_output=True)
        self.assertEqual(result.returncode, 0, "Generated dashboard must be ignored despite earlier negations")
        status = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain", "--untracked-files=all"], text=True, capture_output=True, check=True)
        self.assertNotIn("workbench/dashboard/index.html", status.stdout)

    def test_existing_unowned_html_is_never_overwritten(self):
        self.write("workbench/dashboard/index.html", "human-authored HTML")
        before = self.canonical()
        with self.assertRaises(ValueError):
            self.page()
        self.assertEqual((self.root / "workbench/dashboard/index.html").read_text(), "human-authored HTML")
        self.assertFalse((self.root / ".gitignore").exists())
        self.assertEqual(before, self.canonical())

    def test_git_tracked_material_requires_explicit_migration(self):
        self.write("workbench/retained.md", "important source")
        subprocess.run(["git", "-C", str(self.root), "add", "workbench/retained.md"], check=True)
        with self.assertRaises(ValueError):
            self.page()
        self.assertEqual((self.root / "workbench/retained.md").read_text(), "important source")
        self.assertFalse((self.root / ".gitignore").exists())

    def test_corrupt_and_unsafe_configurations_leave_canonical_state_intact(self):
        before = self.canonical()
        cases = ["{broken", "[]", json.dumps({"workbench_dir": "../outside"}), json.dumps({"workbench_dir": "docs"}),
                 json.dumps({"unexpected": True}), json.dumps({"screens": [dict(id="x", name="X", details=".")]}), json.dumps({"screens": [dict(id="x", name="X", captures=[dict(path="../outside.png", kind="image")])]}),
                 json.dumps({"renderer": "project", "entrypoint": "docs/tasks.md", "regenerate": "build"}),
                 json.dumps({"screens": [dict(id="x", name="X", current=["workbench/old.png"], captures=[dict(path="workbench/old.png", kind="image", adoption="archived")])]})]
        for content in cases:
            with self.subTest(config=content):
                self.write("docs/dashboard.json", content)
                with self.assertRaises((ValueError, hotl.WorkflowError)):
                    self.run_command("dashboard")
                self.assertEqual(before, self.canonical())
                self.assertFalse((self.root / "workbench/dashboard/index.html").exists())

    def test_symlink_output_is_rejected_without_touching_target(self):
        self.write("outside.html", "preserve target")
        (self.root / "workbench/dashboard").mkdir(parents=True)
        (self.root / "workbench/dashboard/index.html").symlink_to(self.root / "outside.html")
        with self.assertRaises(ValueError):
            self.page()
        self.assertEqual((self.root / "outside.html").read_text(), "preserve target")

    def test_malformed_handoff_is_preserved(self):
        content = "Human notes\n<!-- HOTL dashboard link -->\nIncomplete block\n"
        self.write("docs/handoff.md", content)
        with self.assertRaises(ValueError):
            self.page()
        self.assertEqual((self.root / "docs/handoff.md").read_text(), content)
        self.assertFalse((self.root / ".gitignore").exists())

    def test_reversed_adjacent_handoff_markers_are_rejected(self):
        content = "Human notes\n<!-- /HOTL dashboard link --><!-- HOTL dashboard link -->\n"
        self.write("docs/handoff.md", content)
        with self.assertRaises(ValueError):
            self.page()
        self.assertEqual((self.root / "docs/handoff.md").read_text(), content)

    def test_interrupted_regeneration_keeps_previous_page_and_canonical_state(self):
        self.develop()
        self.page()
        previous = (self.root / "workbench/dashboard/index.html").read_bytes()
        before = self.canonical()
        original = hotl.atomic_write
        def interrupted(path, data):
            if str(path).endswith("dashboard/index.html"):
                raise OSError("Simulated interrupted rendering")
            return original(path, data)
        with patch.object(hotl, "atomic_write", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.run_command("sync")
        self.assertEqual(before, self.canonical())
        self.assertEqual(previous, (self.root / "workbench/dashboard/index.html").read_bytes())
        self.run_command("sync")
        self.assertEqual(before, self.canonical())


if __name__ == "__main__":
    unittest.main()
