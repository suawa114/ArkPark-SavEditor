"""Frozen EXE integration QA using disposable copies, never the player's saves."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

root = Path(__file__).resolve().parents[1]
qa = root / 'build' / ('新版独立验证-' + uuid.uuid4().hex[:8])
save = qa / '中文存档目录'
save.mkdir(parents=True)
source = Path(os.environ['LOCALAPPDATA']) / 'ArkPark2/Saved/SaveGames'
source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.sav')}
for path in source.glob('*.sav'):
    shutil.copy2(path, save/path.name)
assert (save/'AT_ArkPark1.sav').exists(), 'No real sample available'
exe = qa / 'ArkParkEditor.exe'
version = json.loads((root / 'frontend/package.json').read_text(encoding='utf-8-sig'))['version']
exe_source = Path(sys.argv[sys.argv.index('--exe')+1]) if '--exe' in sys.argv else root / 'release' / f'v{version}' / 'ArkParkEditor.exe'
shutil.copy2(exe_source, exe)
data = qa / 'data'
assert not data.exists()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env = dict(os.environ)
env['PATH'] = str(Path(os.environ['SYSTEMROOT']) / 'System32')
command = [str(exe), '--port', str(port)]
if '--browser' not in sys.argv:
    command.append('--no-browser')
proc = subprocess.Popen(command, env=env, cwd=qa, creationflags=subprocess.CREATE_NO_WINDOW)
url = f'http://127.0.0.1:{port}'
token = ''


def call(path, method='GET', body=None):
    raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = urllib.request.Request(url+'/api'+path, data=raw, method=method,
        headers={'Content-Type': 'application/json', 'X-Editor-Token': token})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in save.glob('*.sav')}


report = {'url': url, 'qa_dir': str(qa), 'python_in_path': False}
try:
    for _ in range(150):
        try:
            token = call('/session')['token']
            break
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError(f'EXE exited: {proc.returncode}')
            time.sleep(.2)
    else:
        raise RuntimeError('EXE startup timeout')
    assert data.is_dir() and (data/'editor.sqlite3').is_file()
    assert call('/setup')['required']
    try:
        call('/saves')
        raise AssertionError('First-run gate failed')
    except urllib.error.HTTPError as exc:
        assert exc.code == 409
    report['first_run_data_and_gate'] = 'passed'
    game_dir = r'D:\Tools\Steam\steamapps\common\ArkPark2'
    call('/setup', 'POST', {'save_dir': str(save), 'game_dir': game_dir})
    assert not call('/setup')['required']
    with urllib.request.urlopen(url, timeout=10) as response:
        assert b'<div id="app"></div>' in response.read()
    doc = call('/saves/AT_ArkPark1.sav')
    g = doc['gameplay']
    assert len(g['skill_options']) == 21
    assert len(g['weapons']) >= 2 and g['inventory']
    assert all(item['name'] and item['field_id'] for item in g['inventory'])
    assert g['story']['available'] and g['story']['stage']
    assert len(g['story_options']) == 70
    assert sum(t['selectable'] for t in g['story_options']) == 67
    assert len(g['statistics']) == 24
    assert len([s for s in g['statistics'] if s.get('note')]) == 16
    report['gameplay_views'] = {'weapons': len(g['weapons']), 'inventory': len(g['inventory']),
                               'story': g['story']['name'], 'statistics': len(g['statistics'])}
    report['fields'] = len(doc['fields'])
    report['statistics_by_slot'] = {}
    for index in (0, 1):
        statistics = call(f'/saves/AT_ArkPark{index}.sav')['gameplay']['statistics']
        assert len(statistics) == 24
        assert all(item['value'] != '未记录' for item in statistics if item.get('note'))
        report['statistics_by_slot'][str(index + 1)] = statistics
    slots = call('/saves/slots')
    assert [s['id'] for s in slots['slots']] == [1, 2, 3, 4]
    slot = slots['slots'][1]
    assert slot['status'] == 'ready' and slot['main_file'] == doc['name']
    assert slot['summary']['story'] == g['story']
    assert [w['name'] for w in slot['summary']['weapons']] == [w['name'] for w in g['weapons'][:2]]
    assert slot['summary']['cumulative_days'] is None
    home = save/slot['home_file']
    home_bytes = home.read_bytes()
    home.unlink()
    try:
        partial = call('/saves/slots')['slots'][1]
        assert partial['status'] == 'missing' and slot['home_file'] in partial['missing_files']
        assert partial['summary'] == slot['summary']
        missing_stats = call('/saves/AT_ArkPark1.sav')['gameplay']['statistics']
        assert next(s['value'] for s in missing_stats if s['label'] == '家园建造记录数') == '未记录'
        assert next(s['value'] for s in missing_stats if s['label'] == '菜谱总种类') != '未记录'
    finally:
        home.write_bytes(home_bytes)
    report['slot_summaries_and_missing_home'] = slots
    if call('/status')['game_running']:
        try:
            call('/backups', 'POST', {'name': '运行中应禁止'})
            raise AssertionError('Game guard failed')
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        report['game_guard'] = 'passed; write tests skipped'
    else:
        before = hashes()
        backup = call('/backups', 'POST', {'name': '中文名称 / 同名备份'})
        duplicate = call('/backups', 'POST', {'name': backup['name']})
        assert duplicate['id'] != backup['id']
        call('/backups/'+backup['id'], 'PATCH', {'name': '改名不改内容'})
        skills = [v['id'] for v in g['skill_options']] + [g['skill_options'][0]['id']]*3
        stage = next(t['stage'] for t in g['story_options'] if t['selectable'] and t['stage'] != g['story']['stage'])
        item = g['inventory'][0]
        payload = {'version': doc['version'], 'group_version': doc['group_version'],
                   'changes': {'货币/Y': '1e400', item['field_id']: '123', g['story']['field_id']: stage},
                   'weapon_skills': {'0': skills, '1': []}}
        preview = call('/saves/AT_ArkPark1.sav/preview', 'POST', payload)
        assert len(preview['weapon_skills']) == 2 and before == hashes()
        assert preview['story']['value']['stage'] == stage and preview['story']['warning']
        result = call('/saves/AT_ArkPark1.sav/edit', 'POST', payload)
        assert result['applied'][0]['value'] == '9007199254740991.0' and result['applied'][0]['clamped']
        assert result['weapon_skills'] == preview['weapon_skills']
        assert result['story'] == preview['story']
        edited = call('/saves/AT_ArkPark1.sav')['gameplay']
        assert [v['id'] for v in edited['weapons'][0]['skills']] == skills
        assert edited['story']['stage'] == stage and edited['weapons'][0]['skills_editable']
        assert edited['weapons'][1]['skills'] == [] and edited['inventory'][0]['quantity'] == '123'
        try:
            call('/saves/AT_ArkPark1.sav/edit', 'POST', payload)
            raise AssertionError('Version conflict not detected')
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        (save/'额外.sav').write_bytes((save/'AT_ArkPark1.sav').read_bytes())
        call('/backups/'+backup['id']+'/restore', 'POST')
        assert before == hashes()
        call('/backups/'+duplicate['id'], 'DELETE')
        report['combined_edit_preview_conflict_backup_restore'] = 'passed'
        report['story_and_expanded_skills'] = {'stage': stage, 'skills': len(skills)}
    if '--keep' in sys.argv:
        report['running_pid'] = proc.pid
        if '--first-run-ui' in sys.argv:
            import sqlite3
            with sqlite3.connect(data/'editor.sqlite3') as db:
                db.execute("UPDATE settings SET value='false' WHERE key='setup_complete'")
    else:
        call('/shutdown', 'POST')
        proc.wait(timeout=15)
        assert proc.returncode == 0
        report['shutdown'] = 'passed'
    assert source_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.sav')}
    report['player_saves_unchanged'] = True
    (qa/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
except BaseException:
    if proc.poll() is None:
        proc.terminate()
    raise
