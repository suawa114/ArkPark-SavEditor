"""Explicitly arranged player verification, protected by Store transactions."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.storage import Store

root = Path(__file__).resolve().parents[1]
store = Store(root / 'release' / 'legacy' / 'data')
with store.lock:
    store.writable()
    doc = store.read_save('AT_ArkPark1.sav')
    assert doc['comfort']['value'] == '2464'
    preview = store.preview_edit(doc['name'], doc['version'], {}, '2465', doc['group_version'])
    assert preview['comfort']['mutations'] == 1
    assert preview['comfort']['changes'] == [dict(action='add', label='拉面龙纸盒', count=1, each='1', source_index=320)]
    backup = store.create_backup('自动舒适度验证前 · 2464 · 单床已验证')
    result = store.edit(doc['name'], doc['version'], {}, '2465', doc['group_version'])
    report = {'backup_id': backup['id'], 'result': result, 'preview': preview}
    (root/'build/verification/comfort-api-test.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))
