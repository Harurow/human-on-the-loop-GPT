"""Dashboard projection regression tests in isolated project repositories."""
import hashlib
import json
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

    def page(self):
        return (self.root / 'workbench/development-dashboard/index.html').read_text()

    def catalog(self, **changes):
        screen = dict(name='Home <script>', integration=['T-1'], media=[])
        screen.update(changes)
        self.write('docs/ui-catalog.json', json.dumps({'screens': [screen]}))

    def test_no_ui_default_and_deterministic_sync_without_state_mutation(self):
        self.assertIn('UI未登録', self.page())
        before = self.store.path.read_bytes()
        page = self.page()
        self.run_command('sync')
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(page, self.page())
        self.assertFalse(self.run_command('status')['dashboard']['stale'])

    def test_tasks_progress_resume_and_stale_projection(self):
        self.write('docs/tasks.md', '- [>] T-1 Home\n- [-] T-2 Device\n## 再開\n現在: T-1\n次の順番: T-2\n待ち: device\n## 履歴\nobsolete history\n')
        self.assertTrue(self.run_command('status')['dashboard']['stale'])
        self.run_command('sync')
        self.assertIn('T-1 · 進行中', self.page())
        self.assertIn('T-2 · 保留', self.page())
        self.assertIn('待ち: device', self.page())
        self.assertNotIn('obsolete history', self.page())
        self.assertFalse(self.run_command('status')['dashboard']['stale'])

    def test_open_questions_only_and_changed_approval_target(self):
        self.present()
        self.assertIn('提示した要件の承認', self.page())
        self.write('docs/requirements.md', 'changed')
        self.run_command('sync')
        self.assertIn('対象変更済み', self.page())
        q = self.run_command('ask', key='layout', kind='decision', title='old-choice', reason='choose', recommendation='a', blocking=False, options=['a', 'b'], related=['R-1'])['result']
        self.run_command('withdraw', question_id=q, detail='obsolete')
        self.assertNotIn('old-choice', self.page())
        self.assertEqual(self.store.read()['questions'][0]['title'], 'old-choice')

    def test_ui_media_freshness_missing_escape_and_no_fake_pass(self):
        self.write('src/home.py', 'original')
        self.write('artifacts/home.png', 'fixture')
        self.catalog(media=[dict(path='artifacts/home.png', captured_at='2026-09-12', targets={'src/home.py': hashlib.sha256(b'original').hexdigest()})])
        self.run_command('sync')
        self.assertIn('Home &lt;script&gt;', self.page())
        self.assertIn('<img ', self.page())
        self.assertIn('ハッシュ一致', self.page())
        self.write('src/home.py', 'changed')
        self.assertTrue(self.run_command('status')['dashboard']['stale'])
        self.run_command('sync')
        self.assertIn('古い資料', self.page())
        (self.root / 'artifacts/home.png').unlink()
        self.run_command('sync')
        self.assertNotIn('<img ', self.page())
        self.assertIn('リンク切れ', self.page())

    def test_bad_sources_preserve_state_and_previous_html_then_repair(self):
        old_page = self.page()
        self.write('docs/ui-catalog.json', '{broken')
        self.assertIn('error', self.run_command('status')['dashboard'])
        with patch('sys.stderr'):
            self.run_command('note', kind='decision', text='safe note')
        self.assertEqual(self.store.read()['events'][-1]['text'], 'safe note')
        self.assertEqual(self.page(), old_page)
        self.catalog()
        self.run_command('sync')
        self.assertFalse(self.run_command('status')['dashboard']['stale'])

    def test_unmanaged_output_and_unsafe_paths_are_not_overwritten(self):
        self.write('workbench/development-dashboard/index.html', 'user-authored')
        with self.assertRaises(hotl.WorkflowError):
            self.run_command('sync')
        self.assertEqual(self.page(), 'user-authored')
        (self.root / 'workbench/development-dashboard/index.html').unlink()
        for path in ('../outside.png', '/tmp/outside.png', 'https://example.com/a.png'):
            self.catalog(media=[dict(path=path)])
            with self.subTest(path=path), self.assertRaises(hotl.WorkflowError):
                self.run_command('sync')
        (self.root / 'linked').symlink_to('/tmp')
        self.catalog(media=[dict(path='linked/outside.png')])
        with self.assertRaises(hotl.WorkflowError):
            self.run_command('sync')

    def test_generated_html_does_not_invalidate_snapshot(self):
        for name in ('requirements.md', 'spec.md', 'design.md', 'tasks.md'):
            self.write('docs/' + name, '- [ ] T-1 Home\n' if name == 'tasks.md' else name)
        before = self.store.snapshot()
        self.run_command('sync')
        self.assertEqual(before, self.store.snapshot())
        self.catalog()
        self.assertNotEqual(before, self.store.snapshot())

    def test_interrupted_projection_is_repairable(self):
        original = hotl.atomic_write
        def fail_html(path, data):
            if path.name == 'index.html':
                raise OSError('interrupted')
            return original(path, data)
        with patch.object(hotl, 'atomic_write', side_effect=fail_html), patch('sys.stderr'):
            self.run_command('note', kind='decision', text='persisted')
        self.assertEqual(self.store.read()['events'][-1]['text'], 'persisted')
        self.assertTrue(self.run_command('status')['dashboard']['stale'])
        self.run_command('sync')
        self.assertFalse(self.run_command('status')['dashboard']['stale'])

    def test_project_configuration_overrides_presentation_and_catalog(self):
        self.catalog()
        (self.root / 'docs/ui-catalog.json').rename(self.root / 'docs/screens.json')
        self.write('docs/dashboard.json', json.dumps(dict(title='My Tool', sections=['checks', 'ui', 'progress', 'tasks'], ui_catalog='docs/screens.json')))
        self.run_command('sync')
        page = self.page()
        self.assertIn('My Tool', page)
        self.assertLess(page.index('id="ui"'), page.index('id="progress"'))
        self.assertIn('Home &lt;script&gt;', page)
        self.write('docs/dashboard.json', '{"sections": ["ui"]}')
        with self.assertRaises(hotl.WorkflowError):
            self.run_command('sync')

    def test_output_parent_symlink_rejected(self):
        import shutil
        shutil.rmtree(self.root / 'workbench')
        (self.root / 'workbench').symlink_to('/tmp')
        with self.assertRaises(hotl.WorkflowError):
            self.run_command('sync')

    def test_ignore_and_handoff_generated_without_overwriting_existing(self):
        import subprocess
        result = subprocess.run(['git', '-C', str(self.root), 'check-ignore', 'workbench/development-dashboard/index.html'], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('workbench/development-dashboard/index.html', (self.root / 'docs/handoff.md').read_text())
        self.write('docs/handoff.md', 'Existing handoff')
        self.write('.gitignore', 'private-data/\n')
        self.run_command('sync')
        self.assertEqual((self.root / 'docs/handoff.md').read_text(), 'Existing handoff')
        self.assertEqual((self.root / '.gitignore').read_text(), 'private-data/\n/workbench/\n')
