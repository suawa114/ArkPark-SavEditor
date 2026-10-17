import struct

from fastapi.testclient import TestClient
import pytest

from backend import gameplay as gp
from backend.api import create_app
from backend.gvas import Document
from backend.storage import Store
from conftest import fixture_save, fstring, node, tag


def array(name, entries, inner=None):
    return tag(name, node('ArrayProperty', inner or node('NameProperty')),
               struct.pack('<i', len(entries)) + b''.join(entries))


def record(name, value):
    return tag(name, node('NameProperty'), fstring(value)) + fstring('None')


def save(extra=b''):
    return fixture_save(extra).replace(fstring('/Game/SG_C'), fstring(gp.MAIN_CLASS), 1)


def home(count):
    return save(array('S_建造系统', [fstring('None')] * count, node('StructProperty', node('S_建造保存'))))


def collection_save():
    struct_type = node('StructProperty', node('Record'))
    extra = array('已上架的菜谱', [record('名字', '面'), record('名字', '橙汁')], struct_type)
    extra += array('未上架的菜谱', [record('名字', '面')], struct_type)
    extra += array('店员BB', [fstring('None')] * 2, struct_type)
    extra += array('休假的店员BB', [], struct_type)
    extra += array('养殖小动物', [record('小动物', a) for a in ['鹿', '鹿', '熊']], struct_type)
    extra += tag('S_先行者套装', struct_type,
                 array('拥有的大脑袋', [fstring(a) for a in ['红帽', '红帽', '蓝帽']])
                 + array('拥有的衣服', [fstring('衣服')]) + fstring('None'))
    extra += array('C_解锁的船只', [fstring('船')])
    extra += array('认识的发射器', [fstring(a) for a in ['M134', 'M134', 'SVD']])
    # Same skin and encounter name in different parent records count separately.
    extra += array('S_已解锁的发射器配件', [array('已解锁的皮肤', [fstring('默认')]) + fstring('None')] * 2, struct_type)
    extra += array('档案', [fstring('None')], struct_type)
    extra += array('S_地区信息', [array('见闻', [fstring('初见')]) + fstring('None')] * 2, struct_type)
    extra += array('S_背包物资', [record('表格索引', a) for a in ['2', '2', '18']], struct_type)
    return save(extra)


def metrics(doc, home_doc=None):
    return {s['label']: s for s in gp.collection_statistics(doc, home_doc)}


def test_counts_deduplicate_collection_ids_but_keep_parent_scopes():
    original = collection_save()
    doc = Document(original)
    result = metrics(doc, Document(home(5)))
    assert {k: v['value'] for k, v in result.items()} == {
        '已上架菜谱': '2', '未上架菜谱': '1', '菜谱总种类': '2',
        '店员列表人数': '2', '休假店员人数': '0', '养殖动物数量': '3',
        '养殖动物种类': '2', '已有大脑袋数量': '2', '已有衣服数量': '1',
        '已解锁船只数量': '1', '已认识发射器数量': '2', '发射器皮肤记录数': '2',
        '收集档案数量': '1', '地区见闻记录数': '2', '背包物品种类': '2', '家园建造记录数': '5',
    }
    assert all(v['note'] for v in result.values())
    assert doc.patch({})[0] == original


def test_missing_empty_and_incomplete_collections_are_distinct():
    missing = metrics(Document(save()))
    assert len(missing) == 16 and all(v['value'] == '未记录' for v in missing.values())
    struct_type = node('StructProperty', node('Record'))
    extra = array('已上架的菜谱', [], struct_type)
    extra += array('养殖小动物', [], struct_type)
    extra += array('S_已解锁的发射器配件', [fstring('None')], struct_type)
    extra += array('S_地区信息', [array('见闻', []) + fstring('None'), fstring('None')], struct_type)
    extra += array('S_背包物资', [record('表格索引', '2'), fstring('None')], struct_type)
    result = metrics(Document(save(extra)), Document(home(0)))
    assert result['已上架菜谱']['value'] == result['养殖动物种类']['value'] == '0'
    assert result['家园建造记录数']['value'] == '0'
    for name in ['菜谱总种类', '发射器皮肤记录数', '地区见闻记录数', '背包物品种类']:
        assert result[name]['value'] == '未记录'


@pytest.mark.parametrize('home_state', ['valid', 'missing', 'broken', 'wrong_class'])
def test_api_pairs_home_with_selected_slot_and_never_writes(tmp_path, monkeypatch, home_state):
    monkeypatch.setattr(gp, 'catalog_reason', lambda _: '目录未核实')
    directory = tmp_path / '存档'
    directory.mkdir()
    original = collection_save()
    for index in range(2):
        (directory / f'AT_ArkPark{index}.sav').write_bytes(original)
    (directory / 'AT_Home.sav').write_bytes(home(3))
    if home_state != 'missing':
        (directory / 'AT_Home1.sav').write_bytes(
            home(7) if home_state == 'valid' else b'broken' if home_state == 'broken' else fixture_save())
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    store = Store(tmp_path / 'data', process_probe=lambda: False)
    store.set_settings({'save_dir': str(directory), 'game_dir': str(tmp_path)})
    with TestClient(create_app(store.root, store=store)) as client:
        for index, expected in [(0, '3'), (1, '7' if home_state == 'valid' else '未记录')]:
            response = client.get(f'/api/saves/AT_ArkPark{index}.sav')
            assert response.status_code == 200
            stats = {s['label']: s for s in response.json()['gameplay']['statistics']}
            assert stats['家园建造记录数']['value'] == expected
            assert stats['菜谱总种类']['value'] == '2'
            assert stats['已有大脑袋数量']['value'] == '2'
            assert all('field_id' not in s for s in stats.values())
    assert not store.list_backups()
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
