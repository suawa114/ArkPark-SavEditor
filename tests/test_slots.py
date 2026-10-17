import struct

import pytest
from fastapi.testclient import TestClient

from backend import gameplay, slots
from backend.gvas import Document
from backend.api import create_app
from backend.storage import Store
from conftest import fixture_save, fstring, node, tag
from test_gameplay import sample


def main_save(money=10771.621658259623):
    extra = b''.join(tag(name, node('IntProperty'), struct.pack('<i', value)) for name, value in
                     [('世界岛日期', 18), ('世界岛星期', 4), ('拉面龙总营业额度', 10813)])
    data = sample().replace(fstring('None') + b'\0'*4, extra + fstring('None') + b'\0'*4)
    return data.replace(struct.pack('<d', 10771.621658259623), struct.pack('<d', money), 1)


def home_save():
    extra = tag('S_建造系统', node('ArrayProperty', node('StructProperty', node('S_建造系统'))),
                struct.pack('<i', 0))
    return fixture_save(extra).replace(fstring('/Game/SG_C'), fstring(gameplay.MAIN_CLASS), 1)


def main_without_optional_progress():
    data = main_save()
    doc = Document(data)
    omitted = {'剧情任务索引', '世界岛日期', '世界岛星期', '拉面龙总营业额度'}
    for prop in sorted(doc.top_properties, key=lambda p: p['offset'], reverse=True):
        if prop['name'] in omitted:
            start = prop['offset']
            data = data[:start] + data[start + prop['size']:]
    return data


@pytest.fixture
def mapped(monkeypatch):
    monkeypatch.setattr(gameplay, 'catalog_reason', lambda _: '')


def inspect(files):
    metadata = {name: {'modified_at': str(i), 'size': len(data)} for i, (name, data) in enumerate(files.items())}
    return slots.inspect(files, metadata, '')


def test_four_slots_map_special_first_home_and_summary(mapped):
    files = {'AT_ArkPark0.sav': main_save(), 'AT_Home.sav': home_save(),
             'AT_ArkPark1.sav': main_save(99856767.15521426), 'AT_Home1.sav': home_save()}
    result = inspect(files)
    assert [s['id'] for s in result['slots']] == [1, 2, 3, 4]
    assert [s['status'] for s in result['slots']] == ['ready', 'ready', 'empty', 'empty']
    assert result['selected'] == 2
    first, second = result['slots'][:2]
    assert first['home_file'] == 'AT_Home.sav' and first['summary']['money'] == '10772'
    assert second['summary']['money'] == '99856767'
    assert second['summary']['story']['name'] == 'AAA级拉面大王'
    assert '2500' in second['summary']['story']['objective']
    assert [w['name'] for w in second['summary']['weapons']] == ['M134', 'SVD『英雄』']
    assert second['summary']['world_day'] == '18' and second['summary']['world_weekday'] == '4'
    assert second['summary']['revenue'] == '10813' and second['summary']['cumulative_days'] is None


def test_prefer_settings_default_only_if_complete(mapped):
    settings = fixture_save(tag('游戏默认存档', node('IntProperty'), struct.pack('<i', 0)))
    files = {'AT_GameSettings.sav': settings, 'AT_ArkPark0.sav': main_save(), 'AT_Home.sav': home_save(),
             'AT_ArkPark1.sav': main_save(), 'AT_Home1.sav': home_save()}
    assert inspect(files)['selected'] == 1
    del files['AT_Home.sav']
    assert inspect(files)['selected'] == 2


def test_missing_orphan_anomaly_and_corrupt_files_are_isolated(mapped):
    result = inspect({'AT_ArkPark0.sav': main_save(), 'AT_Home1.sav': home_save(),
                      'AT_Home2..sav': home_save(), 'AT_ArkPark3.sav': b'broken', 'AT_Home3.sav': home_save()})
    a, b, c, d = result['slots']
    assert all(s['status'] == 'missing' for s in result['slots'])
    assert a['missing_files'] == ['AT_Home.sav'] and a['summary']
    assert b['missing_files'] == ['AT_ArkPark1.sav'] and b['summary'] is None
    assert c['missing_files'] == ['AT_ArkPark2.sav', 'AT_Home2.sav']
    assert '命名异常' in c['issues'][0] and c['summary'] is None
    assert d['issues'] and d['summary'] is None


def test_relocations_optional_but_orphan_indicates_missing(mapped):
    files = {'AT_ArkPark1.sav': main_save(), 'AT_Home1.sav': home_save(),
             'AT_Home1..sav': b'old anomaly does not override valid home',
             'AT_Home_Relocation_ID3_地图_A.sav': b'preserved opaque'}
    result = inspect(files)
    assert result['slots'][1]['status'] == 'ready'
    assert result['slots'][3]['status'] == 'missing'
    assert result['slots'][3]['related_files'] == ['AT_Home_Relocation_ID3_地图_A.sav']
    assert inspect({})['selected'] == 1


def test_wrong_role_and_unknown_mapping_do_not_fabricate_data(monkeypatch):
    monkeypatch.setattr(gameplay, 'catalog_reason', lambda _: '版本不符')
    result = inspect({'AT_ArkPark0.sav': main_save(), 'AT_Home.sav': main_save()})['slots'][0]
    assert result['status'] == 'missing' and result['issues']
    assert result['summary']['story']['stage'] == '54'
    assert not result['summary']['story']['available']
    assert not result['summary']['story'].get('name')
    assert result['summary']['money'] == '10772'
    assert inspect({'AT_ArkPark0.sav': home_save(), 'AT_Home.sav': home_save()})['slots'][0]['summary'] is None


@pytest.mark.parametrize('value,expected', [('1.5', '2'), ('1.49', '1'), ('9007199254740991', '9007199254740991'),
                                         ('1e308', '1' + '0'*308), ('NaN', None), ('Infinity', None)])
def test_money_rounds_decimal_without_browser_precision_loss(value, expected):
    assert slots.rounded_money(value) == expected


def test_slot_route_precedes_filename_route_and_setup_gate(tmp_path, mapped):
    save, game = tmp_path/'保存', tmp_path/'游戏'
    save.mkdir(); game.mkdir()
    (save/'AT_ArkPark0.sav').write_bytes(main_save())
    (save/'AT_Home.sav').write_bytes(home_save())
    store = Store(tmp_path/'data', process_probe=lambda: False)
    client = TestClient(create_app(store.root, store=store))
    assert client.get('/api/saves/slots').status_code == 409
    store.set_settings({'save_dir': str(save), 'game_dir': str(game)})
    before = store.snapshot(save)
    response = client.get('/api/saves/slots')
    assert response.status_code == 200
    assert response.json()['slots'][0]['status'] == 'ready'
    assert client.get('/api/saves/AT_ArkPark0.sav').status_code == 200
    assert store.snapshot(save) == before


def test_missing_optional_progress_does_not_block_readable_slot(mapped):
    original = main_without_optional_progress()
    slot = inspect({'AT_ArkPark0.sav': original, 'AT_Home.sav': home_save()})['slots'][0]
    assert slot['status'] == 'ready' and not slot['issues'] and not slot['missing_files']
    summary = slot['summary']
    assert summary['money'] == '10772'
    assert summary['world_day'] is None and summary['world_weekday'] is None and summary['revenue'] is None
    assert not summary['story']['available'] and not summary['story']['editable']
    assert 'stage' not in summary['story'] and '未记录' in summary['story']['reason']
    view = gameplay.inspect(Document(original), '')
    assert not view['story_options'] and view['weapons'] and view['inventory']
    assert inspect({'AT_ArkPark0.sav': original})['slots'][0]['status'] == 'missing'


def test_missing_story_allows_existing_edits_but_rejects_invented_field(tmp_path, mapped):
    save, game = tmp_path/'保存', tmp_path/'游戏'
    save.mkdir(); game.mkdir()
    original = main_without_optional_progress()
    (save/'AT_ArkPark0.sav').write_bytes(original)
    (save/'AT_Home.sav').write_bytes(home_save())
    store = Store(tmp_path/'data', process_probe=lambda: False)
    store.set_settings({'save_dir': str(save), 'game_dir': str(game)})
    before = store.snapshot(save)
    with TestClient(create_app(store.root, store=store)) as client:
        headers = {'X-Editor-Token': client.get('/api/session').json()['token']}
        path = '/api/saves/AT_ArkPark0.sav'
        doc = client.get(path).json()
        view = doc['gameplay']
        assert client.get('/api/saves/slots').json()['slots'][0]['status'] == 'ready'
        payload = {'version': doc['version'], 'group_version': doc['group_version'],
                   'changes': {'剧情任务索引': '1'}}
        for route in ['/preview', '/edit']:
            assert client.post(path+route, json=payload, headers=headers).status_code == 422
        assert store.snapshot(save) == before and not store.list_backups()
        weapon = view['weapons'][0]['fields'][0]['field_id']
        quantity = view['inventory'][0]['field_id']
        changes = {'货币/Y': '12345', weapon: '99', quantity: '88'}
        payload['changes'] = changes
        preview = client.post(path+'/preview', json=payload, headers=headers)
        assert preview.status_code == 200 and preview.json()['story'] is None
        assert store.snapshot(save) == before
        result = client.post(path+'/edit', json=payload, headers=headers)
        assert result.status_code == 200
        after = store.snapshot(save)
        assert after['AT_Home.sav'] == before['AT_Home.sav']
        assert after['AT_ArkPark0.sav'] == Document(original).patch(changes)[0]
        updated = client.get(path).json()
        assert not updated['gameplay']['story']['editable']
        assert not any(f['id'] == '剧情任务索引' for f in updated['fields'])
        backups = client.get('/api/backups').json()
        assert len(backups) == 1
        assert client.post('/api/backups/'+backups[0]['id']+'/restore', headers=headers).status_code == 200
        assert store.snapshot(save) == before
