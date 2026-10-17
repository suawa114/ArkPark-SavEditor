"""One protected furniture duplication experiment; not part of normal builds."""
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.gvas import Document
from backend.storage import Store

root = Path(__file__).resolve().parents[1]
store = Store(root / 'release' / 'legacy' / 'data')
with store.lock:
    store.writable()
    directory = store.save_dir()
    before = store.snapshot(directory)
    name = 'AT_Home1.sav'
    doc = Document(before[name])
    array = next(a for a in doc.arrays if a['path'] == ['S_建造系统'])
    index = 57
    path = ['S_建造系统', f'[{index}]']
    strings = [s for s in doc.strings if s['path'][:2] == path]
    assert len(strings) == 1 and 'BP_家具_唱片机.' in strings[0]['value']
    assert not any(o['path'][:2] == path for o in doc.opaque)
    fields = [f for f in doc.fields if f.path[:2] == path]
    assert len(fields) == 10 and all(f.path[-2] in ('Rotation', 'Translation', 'Scale3D') for f in fields)
    entry = array['entries'][index]
    record = bytearray(doc.data[entry['start']:entry['end']])
    x = next(f for f in fields if f.path[-2:] == ['Translation', 'X'])
    struct.pack_into('<d', record, x.offset-entry['start'], x.value+100)
    records = [doc.data[e['start']:e['end']] for e in array['entries']]
    records.append(bytes(record))
    output = doc.replace_top_array('S_建造系统', records)
    checked = Document(output)
    assert len(checked.fields) == len(doc.fields)+10
    backup = store.create_backup('舒适度验证前 · 2207 · 未添加唱片机')
    after = dict(before)
    after[name] = output
    recovery = store.apply('edit', directory, before, after, {'file': name, 'experiment': '新增1个唱片机，X平移100cm，预计舒适度+250'})
    report = {'backup_id': backup['id'], 'recovery_id': recovery, 'before_count': array['count'], 'after_count': len(records), 'expected_comfort': 2457}
    (root / 'build' / 'verification' / 'comfort-test.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
