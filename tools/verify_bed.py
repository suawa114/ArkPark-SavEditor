"""Protected single-bed experiment; never included in the application flow."""
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.gvas import Document, Reader
from backend.storage import Store


def empty_bed_record(doc, entry, prefix):
    """Copy class/transform only; do not duplicate an occupied bed's inventory."""
    record = bytearray(doc.data[entry['start']:entry['end']])
    r = Reader(record)
    pieces = []
    while True:
        start = r.position
        name = r.string()
        if name == 'None':
            pieces.append(bytes(record[start:r.position]))
            break
        r.tree()
        size = r.unpack('i')
        flags = r.unpack('B')
        if flags & ~0x1F:
            raise ValueError('Unexpected record flags')
        if flags & 1:
            r.read(4)
        if flags & 2:
            r.read(16)
        r.read(size)
        if name.startswith('类_') or name.startswith('位置_'):
            if name.startswith('位置_'):
                x = next(f for f in doc.fields if f.path[:2] == prefix and f.path[-2:] == ['Translation', 'X'])
                struct.pack_into('<d', record, x.offset-entry['start'], x.value+200)
            pieces.append(bytes(record[start:r.position]))
    assert r.position == len(record) and len(pieces) == 3
    return b''.join(pieces)


def main():
    root = Path(__file__).resolve().parents[1]
    store = Store(root / 'release' / 'legacy' / 'data')
    with store.lock:
        store.writable()
        directory = store.save_dir()
        before = store.snapshot(directory)
        name = 'AT_Home1.sav'
        doc = Document(before[name])
        a = next(a for a in doc.arrays if a['path'] == ['S_建造系统'])
        source = next(s for s in doc.strings if 'BP_家具_床.BP_家具_床_C' in s['value'])
        prefix = source['path'][:2]
        assert not any(o['path'][:2] == prefix for o in doc.opaque)
        index = int(prefix[1][1:-1])
        record = empty_bed_record(doc, a['entries'][index], prefix)
        records = [doc.data[e['start']:e['end']] for e in a['entries']]
        records.append(record)
        output = doc.replace_top_array('S_建造系统', records)
        added = Document(output)
        new_prefix = ['S_建造系统', f'[{a["count"]}]']
        assert len([f for f in added.fields if f.path[:2] == new_prefix]) == 10
        assert len([s for s in added.strings if s['path'][:2] == new_prefix]) == 1
        backup = store.create_backup('单床舒适度实验前 · 已验证唱片机版本')
        after = dict(before)
        after[name] = output
        recovery = store.apply('edit', directory, before, after,
            {'file': name, 'experiment': '新增1张空床，不复制床位库存，X平移200cm'})
        report = {'backup_id': backup['id'], 'recovery_id': recovery,
                  'before_count': a['count'], 'after_count': len(records),
                  'source_index': index, 'normal_delta': 7, 'double_delta': 14}
        (root / 'build' / 'verification' / 'bed-test.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
