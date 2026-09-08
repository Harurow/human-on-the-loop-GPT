"""Derive a current requirements/specification/task view without duplicating their text."""
import hashlib
import re
from pathlib import Path

ID = re.compile(r'\b(?:NR|R|S|T)-[1-9][0-9]*\b')
DEFINITION = re.compile(r'^(?:#{1,6}\s+|[-*]\s+)?((?:NR|R|S)-[1-9][0-9]*)(?=\s|[:：])')
TASK = re.compile(r'^- \[([ x>\-])\] (T-[1-9][0-9]*) (.+)$')
FILES = ('requirements.md', 'spec.md', 'tasks.md')


def inspect_documents(docs):
    """参照構造だけを検査する。網羅率から承認や文章の意味的完全性を推測しない。"""
    items, hashes, issues = {}, {}, []
    for filename in FILES:
        path = Path(docs) / filename
        if not path.is_file() or path.is_symlink():
            issues.append('Missing regular document: ' + filename)
            continue
        raw = path.read_bytes()
        hashes[filename] = hashlib.sha256(raw).hexdigest()
        current, fence = None, None
        for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
            if fence:
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                    fence = None
                continue
            if marker:
                fence = (marker[1][0], len(marker[1]))
                continue
            task = TASK.fullmatch(line) if filename == 'tasks.md' else None
            definition = DEFINITION.match(line) if filename != 'tasks.md' else None
            if filename == 'tasks.md' and line.startswith('- [') and not task:
                issues.append('Invalid task row at tasks.md:' + str(number))
            if task or definition:
                ident = task[2] if task else definition[1]
                valid = ident.startswith(('R-', 'NR-')) if filename == 'requirements.md' else ident.startswith('S-') if filename == 'spec.md' else True
                if not valid:
                    issues.append('Definition in wrong document: ' + ident)
                if ident in items:
                    issues.append('Duplicate definition: ' + ident)
                    current = None
                    continue
                current = {'id': ident, 'file': filename, 'line': number, 'refs': [],
                           'progress': {' ': 'not_started', '>': 'in_progress', 'x': 'implemented', '-': 'cancelled'}[task[1]] if task else None}
                items[ident] = current
            elif line.startswith('#') or (filename == 'tasks.md' and line.strip() and not line.startswith((' ', '\t'))):
                current = None
            if current:
                current['refs'] = sorted(set(current['refs']) | (set(ID.findall(line)) - {current['id']}))
        if not any(item['file'] == filename for item in items.values()):
            issues.append('No ID definitions: ' + filename)
    for ident, item in items.items():
        for target in item['refs']:
            if target not in items:
                issues.append('Unknown reference: ' + ident + ' -> ' + target)
        if ident.startswith('S-') and not any(ref.startswith(('R-', 'NR-')) and ref in items for ref in item['refs']):
            issues.append('Specification has no requirement: ' + ident)
        if ident.startswith('T-') and item['progress'] != 'cancelled' and not any(ref.startswith(('S-', 'R-', 'NR-')) and ref in items for ref in item['refs']):
            issues.append('Task has no requirement/specification: ' + ident)
    for ident in items:
        if ident.startswith(('R-', 'NR-')) and not any(node['id'].startswith('S-') and ident in node['refs'] for node in items.values()):
            issues.append('Requirement has no specification: ' + ident)
        if ident.startswith('S-') and not any(node['id'].startswith('T-') and node['progress'] != 'cancelled' and ident in node['refs'] for node in items.values()):
            issues.append('Specification has no active task: ' + ident)
    return {'documents': hashes, 'items': list(items.values()), 'issues': sorted(set(issues))}
