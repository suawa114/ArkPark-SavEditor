import hashlib
from pathlib import Path
import struct

from fastapi.testclient import TestClient
import pytest

from backend import gameplay as gp
from backend.api import create_app
from backend.gvas import Document, SaveError
from backend.storage import Store
from conftest import fixture_save, fstring, node, tag

IDS = [f'E_发射器元素核心::NewEnumerator{i}' for i in (10, 17, 2)]
CORE = '携带的核心_44_42378C144D5A202F5425779A948C058A'


def sample(skills0=(), skills1=IDS, stage=54, skill_type='ByteProperty', item='2'):
    def weapon(name, skills):
        values = struct.pack('<i', len(skills)) + b''.join(fstring(v) for v in skills)
        return (tag('发射器ID', node('NameProperty'), fstring(name))
                + tag('增益', node('StructProperty', node('S_发射器增益减益')),
                      tag('火力', node('IntProperty'), struct.pack('<i', 3)) + fstring('None'))
                + tag(CORE, node('ArrayProperty', node(skill_type, node('E_发射器元素核心'))), values)
                + tag('保留未知', node('TextProperty'), b'\xffkeep\x00') + fstring('None'))
    extra = tag(gp.WEAPONS, node('ArrayProperty', node('StructProperty', node('S_发射器数据'))),
                struct.pack('<i', 2) + weapon('M134', skills0) + weapon('SVD『英雄』', skills1))
    inventory = (tag('表格索引', node('NameProperty'), fstring(item))
                 + tag('数量', node('IntProperty'), struct.pack('<i', 42)) + fstring('None'))
    extra += tag('S_背包物资', node('ArrayProperty', node('StructProperty', node('S_物资'))),
                 struct.pack('<i', 1) + inventory)
    extra += tag('剧情任务索引', node('IntProperty'), struct.pack('<i', stage))
    extra += tag('拾取次数', node('IntProperty'), struct.pack('<i', 1234))
    extra += tag('拉面龙开业时长', node('StructProperty', node('DateTime')), struct.pack('<q', 123456))
    return fixture_save(extra).replace(fstring('/Game/SG_C'), fstring(gp.MAIN_CLASS), 1)


@pytest.fixture
def mapped(monkeypatch):
    monkeypatch.setattr(gp, 'catalog_reason', lambda _: '')


def test_named_views_use_item_id_and_verified_enum_not_position(mapped):
    view = gp.inspect(Document(sample()), '')
    assert view['inventory'][0]['index'] == 0
    assert view['inventory'][0]['name'] == '木材'
    assert view['inventory'][0]['item_id'] == '2'
    assert view['inventory'][0]['quantity'] == '42'
    assert view['weapons'][0]['skills'] == []
    assert [v['name'] for v in view['weapons'][1]['skills']] == ['雷电2', '风暴3', '雷电1']
    assert gp.CATALOG['skills']['E_发射器元素核心::NewEnumerator7'] == {'name': '火焰3', 'ordinal': 14}
    assert view['story']['stage'] == '54' and view['story']['name'] == 'AAA级拉面大王'
    assert '2500' in view['story']['objective'] and '\\r\\n' not in view['story']['details']
    assert [s['label'] for s in view['statistics'] if 'field_id' in s] == ['拾取次数']
    assert view['statistics'][0]['value'] == '1234'
    assert view['weapons'][0]['fields'][0]['label'] == '火力'
    assert not view['weapons'][0]['zoom']['editable']


def test_unknown_item_stage_and_fileclass(mapped):
    view = gp.inspect(Document(sample(stage=999, item='987654')), '')
    assert view['inventory'][0]['name'] == '未识别物品 (987654)'
    assert not view['story']['available'] and view['story']['stage'] == '999'
    assert not gp.inspect(Document(fixture_save()), '')['available']


def test_version_guard_disables_mapping_and_skill_writes(tmp_path):
    assert gp.catalog_reason(str(tmp_path))
    for relative in gp.CATALOG['fingerprints']:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'new game version')
    assert '版本' in gp.catalog_reason(str(tmp_path))
    view = gp.inspect(Document(sample()), str(tmp_path))
    assert not view['skill_options'] and not view['weapons'][0]['skills_editable']
    assert view['inventory'][0]['name'].startswith('未识别')
    assert not view['story']['available'] and view['statistics']
    with pytest.raises(SaveError, match='版本'):
        gp.patch_skills(sample(), {'0': IDS}, str(tmp_path))


def test_skill_resize_preserves_siblings_opaque_and_roundtrips(mapped):
    original = sample()
    output, changes = gp.patch_skills(original, {'0': IDS}, '')
    doc, checked = Document(original), Document(output)
    assert gp._skill_state(checked, 0)[1] == IDS
    assert changes == [{'index': 0, 'old': [], 'value': IDS}]
    assert checked.opaque == doc.opaque
    def sibling(d):
        a = gp._array(d, [gp.WEAPONS])['entries'][1]
        return d.data[a['start']:a['end']]
    assert sibling(checked) == sibling(doc)
    assert [(f.id, f.value) for f in checked.fields] == [(f.id, f.value) for f in doc.fields]
    assert gp.patch_skills(output, {'0': []}, '')[0] == original
    assert gp.patch_skills(original, {'0': []}, '') == (original, [])
    assert gp.patch_skills(output, {'0': IDS}, '') == (output, [])


def test_multiple_skill_edits_preserve_order_duplicates(mapped):
    values = [IDS[1], IDS[0], IDS[1]]
    data, _ = gp.patch_skills(sample(), {'0': values, '1': []}, '')
    doc = Document(data)
    assert gp._skill_state(doc, 0)[1] == values
    assert gp._skill_state(doc, 1)[1] == []
    assert gp.patch_skills(data, {'0': [], '1': IDS}, '')[0] == sample()


@pytest.mark.parametrize('changes', [{'2': []}, {'-1': []}, {'00': []},
                                     {'0': ['injected']}, {'0': [1]}, {'0': 'bad'}])
def test_invalid_skill_edits_rejected(mapped, changes):
    with pytest.raises(SaveError):
        gp.patch_skills(sample(), changes, '')


@pytest.mark.parametrize('data', [sample(skills0=['unknown']),
                                 sample(skill_type='EnumProperty'), fixture_save()])
def test_unverified_original_schema_is_readonly(mapped, data):
    view = gp.inspect(Document(data), '')
    if view['weapons']:
        assert not view['weapons'][0]['skills_editable']
    with pytest.raises(SaveError):
        gp.patch_skills(data, {'0': IDS}, '')


def test_api_combined_atomic_edit_preview_conflict_backup_restore(mapped, tmp_path):
    save, game = tmp_path/'saves', tmp_path/'game'
    save.mkdir(); game.mkdir()
    original = sample()
    (save/'AT_ArkPark1.sav').write_bytes(original)
    (save/'AT_Home1.sav').write_bytes(fixture_save())
    store = Store(tmp_path/'data', process_probe=lambda: False)
    store.set_settings({'save_dir': str(save), 'game_dir': str(game)})
    with TestClient(create_app(store.root, store=store)) as client:
        headers = {'X-Editor-Token': client.get('/api/session').json()['token']}
        path = '/api/saves/AT_ArkPark1.sav'
        version = hashlib.sha256(original).hexdigest()
        payload = {'version': version, 'changes': {'货币/Y': '1e999'}, 'weapon_skills': {'0': IDS, '1': []}}
        preview = client.post(path+'/preview', json=payload, headers=headers)
        assert preview.status_code == 200 and preview.json()['weapon_skills'][0]['value'] == IDS
        assert (save/'AT_ArkPark1.sav').read_bytes() == original and not store.list_backups()
        invalid = {**payload, 'weapon_skills': {'0': ['unknown']}}
        assert client.post(path+'/edit', json=invalid, headers=headers).status_code == 422
        assert (save/'AT_ArkPark1.sav').read_bytes() == original and not store.list_backups()
        result = client.post(path+'/edit', json=payload, headers=headers)
        assert result.status_code == 200
        result = result.json()
        assert result['weapon_skills'] == preview.json()['weapon_skills']
        checked = Document((save/'AT_ArkPark1.sav').read_bytes())
        assert checked.by_id['货币/Y'].value == 2**53-1
        assert gp._skill_state(checked, 0)[1] == IDS and gp._skill_state(checked, 1)[1] == []
        assert client.post(path+'/edit', json=payload, headers=headers).status_code == 409
        assert store.backup_data(result['recovery_id'])['AT_ArkPark1.sav'] == original
        store.restore(result['recovery_id'])
        assert (save/'AT_ArkPark1.sav').read_bytes() == original
        # No unknown request fields may be silently ignored.
        assert client.post(path+'/preview', json={**payload, 'zoom': '5'}, headers=headers).status_code == 422


@pytest.mark.parametrize('count', [4, 21, 22, 300])
def test_skill_counts_above_normal_generation_are_editable_and_roundtrip(mapped, count):
    values = (list(gp.CATALOG['skills']) * (count//21 + 1))[:count]
    original = sample()
    output, applied = gp.patch_skills(original, {'0': values}, '')
    view = gp.inspect(Document(output), '')
    assert view['weapons'][0]['skills_editable']
    assert len(view['weapons'][0]['skills']) == count
    assert applied[0]['value'] == values
    assert gp.patch_skills(output, {'0': []}, '')[0] == original
    assert gp.patch_skills(output, {'0': values}, '') == (output, [])


def test_all_story_records_have_explicit_empty_stages_and_normalized_text(mapped):
    view = gp.inspect(Document(sample()), '')
    assert [q['stage'] for q in view['story_options']] == [str(i) for i in range(70)]
    assert [q['stage'] for q in view['story_options'] if not q['selectable']] == ['0', '68', '69']
    assert view['story']['editable'] and view['story']['field_id'] == '剧情任务索引'
    assert all('\r' not in q.get('details', '') for q in view['story_options'])


@pytest.mark.parametrize('target', ['0', '68', '69', '999', '-1', '1e400', 'NaN', '54.5'])
def test_story_rejects_empty_unknown_and_invalid_targets(mapped, target):
    with pytest.raises(SaveError):
        gp.patch_numbers(sample(), {'剧情任务索引': target}, '')


def test_story_patch_only_changes_target_bytes_and_can_restore(mapped):
    original = sample()
    output, applied, story = gp.patch_numbers(original, {'剧情任务索引': '55'}, '')
    offset = Document(original).by_id['剧情任务索引'].offset
    assert output[:offset] == original[:offset] and output[offset+4:] == original[offset+4:]
    assert applied[0]['value'] == '55'
    assert story['old']['name'] == 'AAA级拉面大王' and story['value']['name'] == '只是小插曲'
    assert story['value']['objective'] == '打晕荒木工业的打手与增援'
    assert gp.patch_numbers(output, {'剧情任务索引': '54'}, '')[0] == original


def test_story_version_guard_keeps_raw_view_but_blocks_edit(monkeypatch):
    monkeypatch.setattr(gp, 'catalog_reason', lambda _: '版本不符')
    view = gp.inspect(Document(sample()), '')
    assert not view['story_options'] and not view['story']['editable']
    assert view['story']['stage'] == '54'
    with pytest.raises(SaveError, match='版本不符'):
        gp.patch_numbers(sample(), {'剧情任务索引': '55'}, '')


def test_story_and_unrestricted_skills_share_transaction_backup_conflict_restore(mapped, tmp_path):
    save, game = tmp_path/'保存', tmp_path/'游戏'
    save.mkdir(); game.mkdir()
    original = sample(skills0=IDS*2)
    (save/'AT_ArkPark1.sav').write_bytes(original)
    (save/'AT_Home1.sav').write_bytes(fixture_save())
    store = Store(tmp_path/'data', process_probe=lambda: False)
    store.set_settings({'save_dir': str(save), 'game_dir': str(game)})
    with TestClient(create_app(store.root, store=store)) as client:
        headers = {'X-Editor-Token': client.get('/api/session').json()['token']}
        path = '/api/saves/AT_ArkPark1.sav'
        payload = {'version': hashlib.sha256(original).hexdigest(),
                   'changes': {'剧情任务索引': '55', '货币/Y': '500'}, 'weapon_skills': {'0': IDS*8, '1': []}}
        before = store.snapshot(save)
        preview = client.post(path+'/preview', json=payload, headers=headers)
        assert preview.status_code == 200 and preview.json()['story']['value']['stage'] == '55'
        assert store.snapshot(save) == before and not store.list_backups()
        invalid = {**payload, 'changes': {'剧情任务索引': '68'}}
        assert client.post(path+'/edit', json=invalid, headers=headers).status_code == 422
        assert store.snapshot(save) == before and not store.list_backups()
        result = client.post(path+'/edit', json=payload, headers=headers)
        assert result.status_code == 200 and result.json()['story'] == preview.json()['story']
        checked = Document((save/'AT_ArkPark1.sav').read_bytes())
        assert checked.by_id['剧情任务索引'].value == 55 and checked.by_id['货币/Y'].value == 500
        assert gp._skill_state(checked, 0)[1] == IDS*8
        assert client.post(path+'/edit', json=payload, headers=headers).status_code == 409
        assert store.backup_data(result.json()['recovery_id']) == before
        assert store.history()[0]['details']['story']['value']['stage'] == '55'
        store.restore(result.json()['recovery_id'])
        assert store.snapshot(save) == before
