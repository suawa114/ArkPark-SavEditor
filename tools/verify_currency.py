"""Explicit, journalled in-game currency verification; never runs during build."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.storage import Store

root = Path(__file__).resolve().parents[1]
store = Store(root / 'release' / 'legacy' / 'data')
store.writable()
name = 'AT_ArkPark1.sav'
doc = store.read_save(name)
backup = store.create_backup('游戏验证前 · 货币10811 · 舒适度2207')
result = store.edit(name, doc['version'], {'货币/Y': '123456'})
report = {'backup_id': backup['id'], 'old_money': next(f['value'] for f in doc['fields'] if f['shortcut'] == 'money'), 'result': result}
(root / 'build' / 'verification' / 'currency-test.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
