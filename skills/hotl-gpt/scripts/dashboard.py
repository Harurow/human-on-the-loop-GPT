"""Rebuild a local, read-only development dashboard from HOTL canonical files."""
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

MARKER = '<!-- HOTL development dashboard: generated -->'
START = '<!-- HOTL dashboard link -->'
END = '<!-- /HOTL dashboard link -->'


def escape(value):
    return html.escape(str(value), quote=True)


def local(root, value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Dashboard paths must be nonempty project-relative paths')
    p = Path(value)
    if not p.parts or p.is_absolute() or '..' in p.parts or p.parts[0] in {'.git', '.agents', '.env', '.auth'}:
        raise ValueError('Unsafe dashboard path: ' + value)
    result = root / p
    if not result.resolve().is_relative_to(root) or any(x.is_symlink() for x in [result, *result.parents] if x != root.parent):
        raise ValueError('Dashboard paths must stay inside the project without symlinks: ' + value)
    return result


def config_for(root):
    path = local(root, 'docs/dashboard.json')
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict) or set(data) - {'version', 'project_name', 'title', 'workbench_dir', 'ui', 'screens', 'renderer', 'entrypoint', 'regenerate'}:
        raise ValueError('Unknown dashboard configuration fields')
    if data.get('version', 1) != 1 or type(data.get('ui', True)) is not bool:
        raise ValueError('Unsupported dashboard configuration version or ui value')
    for key in ('project_name', 'title'):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ValueError('Invalid dashboard ' + key)
    folder = data.get('workbench_dir', 'workbench')
    # A dedicated top-level output directory, never source/configuration roots.
    if not isinstance(folder, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', folder) or folder in {'docs', 'src', 'assets', 'scripts', 'tests', 'skills', 'public', 'node_modules'}:
        raise ValueError('workbench_dir must be a dedicated top-level output directory')
    local(root, folder)
    if data.get('renderer', 'builtin') not in {'builtin', 'project'}:
        raise ValueError('Unknown dashboard renderer')
    if data.get('renderer') == 'project':
        entry = data.get('entrypoint')
        local(root, entry)
        if not entry.startswith(folder + '/') or not entry.endswith('.html'):
            raise ValueError('Project dashboard entrypoint must be HTML inside workbench_dir')
        if not isinstance(data.get('regenerate'), str) or not data['regenerate'].strip():
            raise ValueError('Project renderer requires a documented regenerate command')
    elif 'entrypoint' in data or 'regenerate' in data:
        raise ValueError('entrypoint/regenerate require renderer=project')
    screens = data.get('screens', [])
    if not isinstance(screens, list):
        raise ValueError('screens must be a list')
    seen = set()
    for screen in screens:
        if not isinstance(screen, dict) or set(screen) - {'id', 'name', 'tasks', 'details', 'current', 'captures'}:
            raise ValueError('Invalid screen fields')
        sid = screen.get('id')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', sid) or sid in seen:
            raise ValueError('Screen IDs must be unique')
        seen.add(sid)
        if not isinstance(screen.get('name'), str):
            raise ValueError('Screen name required')
        if not isinstance(screen.get('tasks', []), list) or any(not re.fullmatch(r'T-[1-9][0-9]*', str(t)) for t in screen.get('tasks', [])):
            raise ValueError('Invalid screen task references')
        if 'details' in screen:
            local(root, screen['details'])
        captures = screen.get('captures', [])
        if not isinstance(captures, list):
            raise ValueError('captures must be a list')
        paths = set()
        for c in captures:
            if not isinstance(c, dict) or set(c) - {'path', 'kind', 'adoption', 'connected', 'verification', 'evidence', 'recorded_at', 'retention'}:
                raise ValueError('Invalid capture fields')
            p = c.get('path')
            local(root, p)
            if p in paths:
                raise ValueError('Duplicate capture path')
            paths.add(p)
            if c.get('kind') not in {'image', 'video'} or c.get('adoption', 'candidate') not in {'accepted', 'candidate', 'rejected', 'archived'}:
                raise ValueError('Invalid capture kind/adoption')
            if c.get('connected') is not None and type(c['connected']) is not bool:
                raise ValueError('connected must be true, false or null')
            if c.get('verification', 'not_checked') not in {'test', 'visual', 'device', 'not_checked'} or c.get('retention', 'keep') not in {'keep', 'reproducible'}:
                raise ValueError('Invalid verification/retention')
            if 'evidence' in c:
                local(root, c['evidence'])
        current = screen.get('current', [])
        if not isinstance(current, list) or len(current) != len(set(current)) or any(p not in paths for p in current):
            raise ValueError('current must reference unique capture paths')
        if any(c['path'] in current and c.get('adoption') in {'rejected', 'archived'} for c in captures):
            raise ValueError('Rejected/archived captures cannot be current')
    return data


def render(store, state, config, stamp):
    root = store.root
    output = root / config.get('workbench_dir', 'workbench') / 'dashboard' / 'index.html'
    warnings = []

    def link(path, label=None):
        target = local(root, path)
        if not target.is_file() and path != 'docs/handoff.md':
            return '<span class="missing">' + escape(label or path) + '（未作成・ローカル未配置）</span>'
        url = quote(os.path.relpath(target, output.parent).replace(os.sep, '/'), safe='/')
        return '<a href="' + url + '">' + escape(label or path) + '</a>'

    tasks_path = local(root, 'docs/tasks.md')
    tasks_text = tasks_path.read_text() if tasks_path.exists() else ''
    try:
        tasks = store.tasks() if tasks_text.strip() else []
    except Exception as exc:
        tasks = []
        warnings.append('タスクを解釈できません。正本を確認してください: ' + str(exc))
    task_ids = {t[1] for t in tasks}
    progress_names = {' ': '未着手', '>': '実行中の記録', 'x': '完了の記録', '-': '取り下げ'}
    task_map = {tid: progress_names[status] for status, tid, text in tasks}

    def task_list(rows):
        def items(subset):
            return ''.join('<li id="task-' + tid + '"><strong>' + tid + '</strong> ' + escape(text) + '</li>' for status, tid, text in subset)
        if not rows:
            return '<p class="muted">登録なし</p>'
        content = '<ol>' + items(rows[:5]) + '</ol>'
        if len(rows) > 5:
            content += '<details><summary>残り ' + str(len(rows) - 5) + '件</summary><ol start="6">' + items(rows[5:]) + '</ol></details>'
        return content

    checks = sorted([q for q in store.questions(state) if q['status'] == 'open'], key=lambda q: not q['blocking'])
    check_html = ''
    for q in checks:
        refs = list(dict.fromkeys(list(q.get('targets', {})) + [p for p in q.get('related', []) if '/' in p]))
        check_html += '<article class="question"><h3>' + escape(q['id'] + ' · ' + q['title']) + '</h3><p>' + escape(q['reason']) + '</p><p><b>おすすめ：</b>' + escape(q['recommendation']) + '</p>'
        if q.get('options'):
            check_html += '<p>選択肢：' + escape(' / '.join(q['options'])) + '</p>'
        check_html += '<p class="badge">' + ('対象変更済み・再提示が必要' if q['stale'] else '回答待ち') + (' · 関連作業を保留' if q['blocking'] else ' · 他の作業は続行可') + '</p><p>' + ' / '.join(link(p) for p in refs) + '</p></article>'
    if not checks:
        check_html = '<p class="clear">現在、登録されたユーザー確認事項はありません。</p>'
    check_html += '<p class="muted">回答はチャットで項目IDとともに伝えてください。このページから承認や状態は変更しません。</p>'
    if state.get('approval') and not store.approval_valid(state):
        warnings.append('要件が承認版から変更されています。checkで整合性を確認してください。')
    next_tasks, held = [], []
    completed = {tid for status, tid, text in tasks if status == 'x'}
    for status, tid, text in tasks:
        if status != ' ':
            continue
        dependency = re.search(r'依存\s*[:：]\s*([^;；]+)', text)
        dependencies = re.findall(r'T-[1-9][0-9]*', dependency.group(1)) if dependency else []
        reason = re.search(r'(?:待ち|保留)\s*[:：]\s*([^;；]+)', text)
        if reason or any(d not in completed for d in dependencies):
            held.append((status, tid, text))
        else:
            next_tasks.append((status, tid, text))
    # Canonical order is tasks.md order; dependency/hold metadata does not create a new state.
    current_html = task_list([t for t in tasks if t[0] == '>'])
    waiting = ('<p class="badge">停止指示あり。再開の入力が必要です。</p>' if state['paused'] else '<p>停止指示なし（プロセスの稼働確認ではありません）。</p>')
    waiting += '<p>ユーザー確認待ち：' + str(len(checks)) + '件 / AIの未処理入力：' + str(sum(i['outcome'] is None for i in state['inputs'])) + '件</p>' + task_list(held)
    resume = re.search(r'^## 再開\s*\n(.*?)(?=^## |\Z)', tasks_text, re.M | re.S)
    if resume:
        waiting += '<details><summary>再開欄の記録</summary><pre>' + escape(resume.group(1).strip()) + '</pre></details>'
    screens_html = ''
    for screen in config.get('screens', []) if config.get('ui', True) else []:
        refs = screen.get('tasks', [])
        missing = [tid for tid in refs if tid not in task_ids]
        if missing:
            warnings.append(screen['name'] + ': 不明なタスク参照 ' + ', '.join(missing))
        screens_html += '<article class="screen" id="screen-' + screen['id'] + '"><h3>' + escape(screen['name']) + '</h3><p>' + ' / '.join(escape(tid + ': ' + task_map.get(tid, '不明')) for tid in refs) + '</p>'
        if screen.get('details'):
            screens_html += '<p>' + link(screen['details'], '画面の仕様・詳細') + '</p>'
        captures = screen.get('captures', [])
        current = screen.get('current', [])
        for c in [c for c in captures if c['path'] in current]:
            target = local(root, c['path'])
            screens_html += '<figure>'
            if target.is_file():
                uri = quote(os.path.relpath(target, output.parent).replace(os.sep, '/'), safe='/')
                if c['kind'] == 'image':
                    screens_html += '<a href="' + uri + '"><img loading="lazy" src="' + uri + '" alt="' + escape(screen['name']) + '"></a>'
                else:
                    screens_html += '<video controls preload="none" src="' + uri + '"></video>'
            screens_html += '<figcaption>' + link(c['path'], '原資料を開く') + ' · ' + escape({'accepted':'採用', 'candidate':'未採用候補', 'rejected':'不採用', 'archived':'過去資料'}[c.get('adoption', 'candidate')]) + ' · ' + {True:'本編接続済みの記録', False:'本編未接続', None:'本編接続未確認'}[c.get('connected')] + ' · ' + {'test':'自動テストの記録', 'visual':'目視確認の記録', 'device':'実機確認の記録', 'not_checked':'未検証'}[c.get('verification', 'not_checked')] + ' · 記録日 ' + escape(c.get('recorded_at', '未記入'))
            if c.get('evidence'):
                screens_html += ' · ' + link(c['evidence'], '検証記録')
            screens_html += ' · ' + ('保持対象' if c.get('retention', 'keep') == 'keep' else '再生成可能（削除許可ではありません）') + '</figcaption></figure>'
        if not current:
            screens_html += '<p class="muted">現在のキャプチャは未登録です。</p>'
        old = [c for c in captures if c['path'] not in current]
        if old:
            screens_html += '<details><summary>過去・非表示資料（保持） ' + str(len(old)) + '件</summary><ul>' + ''.join('<li>' + link(c['path']) + '</li>' for c in old) + '</ul></details>'
        screens_html += '</article>'
    if not screens_html:
        screens_html = '<p>' + ('UIなしの案件です。画面キャプチャは不要です。' if not config.get('ui', True) else 'UI一覧は未登録です。UIがある場合だけ設定に画面と資料を登録してください。') + '</p>'
    records = '<p>' + ' / '.join(link('docs/' + filename, label) for filename, label in [('requirements.md','要件'),('spec.md','仕様'),('design.md','設計'),('tasks.md','タスク正本'),('user-checks.md','確認事項の一覧'),('hotl.state.json','状態正本'),('log.md','判断ログ'),('handoff.md','固定引継ぎ入口')]) + '</p>'
    records += '<ul>' + ''.join('<li>' + escape(r['role'] + ' / ' + r['result']) + ' · ' + link(r['evidence'], '検証証拠') + '（過去の登録。現在の有効性はtraceで確認）</li>' for r in state['reviews'][-10:]) + '</ul>'
    records += '<p class="muted">採用・本編接続・検証種別は資料の記録です。タスク完了や要件承認を代行せず、古い記録を現在の検証合格とは扱いません。</p>'
    alert = ''.join('<p class="warning">' + escape(w) + '</p>' for w in warnings)
    name = config.get('project_name', state['project'])
    title = config.get('title', '開発ダッシュボード')
    body = '<header><h1>' + escape(name) + '</h1><p>' + escape(title) + '</p></header><main><section class="checks" id="checks"><h2>今、確認してほしいこと</h2>' + alert + check_html + '</section>'
    body += '<p class="meta">更新 ' + escape(stamp) + ' · state revision ' + str(state['revision']) + ' · ' + escape(state['phase']) + ' · 区切り時点の保存内容です。ライブ監視ではありません。</p>'
    body += '<nav aria-label="ダッシュボード内の移動"><a href="#current">現在の作業</a> · <a href="#next">今後の順番</a> · <a href="#waiting">待ち・保留</a> · <a href="#screens">UI一覧</a> · <a href="#records">詳細と記録</a></nav><div class="columns"><section id="current"><h2>現在の作業</h2>' + current_html + '</section><section id="next"><h2>今後の順番</h2>' + task_list(next_tasks) + '</section><section id="waiting"><h2>待ち・保留</h2>' + waiting + '</section></div><section id="screens"><h2>UI一覧・画面資料</h2><div class="gallery">' + screens_html + '</div></section><section id="records"><h2>詳細と記録</h2>' + records + '</section></main>'
    css = (Path(__file__).parent.parent / 'assets/dashboard.css').read_text()
    page = '<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\'; media-src \'self\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><meta name="referrer" content="no-referrer"><title>' + escape(name + ' · ' + title) + '</title><style>' + css + '</style></head><body>' + MARKER + body + '</body></html>\n'
    return page.encode(), warnings


def generate(store, state, atomic_write, stamp):
    root = store.root
    config = config_for(root)
    folder = config.get('workbench_dir', 'workbench')
    custom = config.get('renderer') == 'project'
    entry = config['entrypoint'] if custom else folder + '/dashboard/index.html'
    output = local(root, entry)
    gitignore = local(root, '.gitignore')
    handoff = local(root, 'docs/handoff.md')
    # Validate every destination before changing any files.
    if not custom and output.exists() and (not output.is_file() or MARKER not in output.read_text()):
        raise ValueError('Existing dashboard is not HOTL-generated; preserve it and select another workbench_dir')
    tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--', folder], capture_output=True)
    if tracked.returncode == 0 and tracked.stdout.strip():
        raise ValueError('workbench_dir contains Git-tracked files. Move retained sources explicitly; no automatic removal.')
    ignore_text = gitignore.read_text() if gitignore.exists() else ''
    ignore_line = '/' + folder + '/'
    effective_lines = [line.strip() for line in ignore_text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    ignore_new = ignore_text.rstrip('\n') + '\n' + ignore_line + '\n' if not effective_lines or effective_lines[-1] != ignore_line else ignore_text
    handoff_text = handoff.read_text() if handoff.exists() else '# 引き継ぎ\n\n正本は [タスク](tasks.md) と [状態](hotl.state.json)。再開時に状態とGit差分を確認してください。\n'
    instructions = ('プロジェクトの生成手順: ' + config['regenerate']) if custom else '最新化は HOTL CLI の `dashboard` または `sync`。'
    block = START + '\n[開発ダッシュボード](../' + entry + ') — 生成ビュー。' + instructions + '\n' + END
    if (START in handoff_text) != (END in handoff_text) or handoff_text.count(START) > 1 or handoff_text.count(END) > 1:
        raise ValueError('Malformed dashboard handoff markers; preserve and repair the existing entry')
    if START in handoff_text:
        start, end = handoff_text.index(START), handoff_text.index(END) + len(END)
        if handoff_text.index(END) < start:
            raise ValueError('Malformed dashboard handoff markers')
        handoff_new = handoff_text[:start] + block + handoff_text[end:]
    else:
        handoff_new = handoff_text.rstrip() + '\n\n' + block + '\n'
    if custom:
        page = None
        warnings = ['独自ビューは更新していません。記載の生成手順を実行してください。']
        if not output.is_file():
            warnings.append('独自ビューは未生成です。')
    else:
        page, warnings = render(store, state, config, stamp)
    output.parent.mkdir(parents=True, exist_ok=True)
    if ignore_new != ignore_text:
        atomic_write(gitignore, ignore_new.encode())
    if handoff_new != handoff_text:
        atomic_write(handoff, handoff_new.encode())
    if page is not None:
        atomic_write(output, page)
    return dict(path=str(output), generated=not custom, updated_at=stamp if not custom else None, revision=state['revision'], warnings=warnings,
                source_sha256=hashlib.sha256(store.path.read_bytes()).hexdigest())
