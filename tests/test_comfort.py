import itertools
import struct
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend import comfort
from backend.api import create_app
from backend.gvas import Document, SaveError
from backend.storage import Store, ServiceError
from conftest import fixture_save, fstring, node, tag

PREFIX = '/Game/ArkPark/Blueprints/建造系统/家具/'
BOX = PREFIX+'BP_拉面龙纸盒'
BED = PREFIX+'BP_家具_床'
PLAYER = PREFIX+'BP_家具_唱片机'


def header():
    original = fixture_save()
    return original[:Document(original).top_properties[0]['offset']]


def home(records):
    value = struct.pack('<i', len(records))+b''.join(records)
    return header()+tag('S_建造系统', node('ArrayProperty', node('StructProperty', node('S_建造系统'))), value)+fstring('None')+b'\0'*4


def record(key, inventory=None):
    name = key.rsplit('/',1)[-1]
    cls = tag('类_54', node('ObjectProperty'), fstring(key+'.'+name+'_C'))
    transform = tag('Rotation', node('StructProperty',node('Quat')), struct.pack('<dddd',0,0,0,1))
    transform += tag('Translation', node('StructProperty',node('Vector')), struct.pack('<ddd',123,456,789))
    transform += tag('Scale3D', node('StructProperty',node('Vector')), struct.pack('<ddd',1,1,1))
    transform += fstring('None')
    result = cls+tag('位置_45',node('StructProperty',node('Transform')),transform)
    if inventory is not None:
        entries = b''.join(tag('数量',node('IntProperty'),struct.pack('<i',q))+fstring('None') for q in inventory)
        result += tag('物资_58',node('ArrayProperty',node('StructProperty',node('Stock'))),struct.pack('<i',len(inventory))+entries)
    return result+fstring('None')


def relocation():
    summon = PREFIX+'BP_家具_载具召唤装置.BP_家具_载具召唤装置_C'
    props = tag('MapName',node('StrProperty'),fstring('方舟大世界2'))
    props += tag('OwnerSaveId',node('StrProperty'),fstring('1'))
    props += tag('ActorClass',node('StrProperty'),fstring(summon))
    return header()+props+fstring('None')+b'\0'*4


@pytest.fixture
def saves():
    return {'AT_ArkPark1.sav':fixture_save(),
            'AT_Home1.sav':home([record(BOX,[]),record(BED,[]),record(PLAYER,[3])]),
            'AT_Home_Relocation_ID1_方舟大世界2_A.sav':relocation()}


def test_derived_total_protected_inventory_and_lossless_change(saves):
    current = comfort.inspect(saves,'AT_ArkPark1.sav','',verify=False)[0]
    assert current['value'] == str(296+1+7+250)
    assert current['reachable_minimum'] == '546'
    assert sum(a['comfort'] for a in current['map_breakdown']) == 296
    out, plan = comfort.plan(saves,'AT_ArkPark1.sav','','553',verify=False)
    assert plan['value']=='553' and plan['mutations']==1
    assert plan['changes'][0]['indices']==[0]
    doc = Document(out); a = next(a for a in doc.arrays if a['path']==['S_建造系统'])
    assert [out[e['start']:e['end']] for e in a['entries']] == [record(BED,[]),record(PLAYER,[3])]
    with pytest.raises(SaveError,match='最低可达到 546'):
        comfort.plan(saves,'AT_ArkPark1.sav','','0',verify=False)


def test_minimum_change_planning_matches_bruteforce(saves):
    original = int(comfort.inspect(saves,'AT_ArkPark1.sav','',verify=False)[0]['value'])
    for delta in [-8,-7,-6,-1,0,1,6,7,8,249,250,251]:
        target = original+delta
        _, plan = comfort.plan(saves,'AT_ArkPark1.sav','',str(target),verify=False)
        options = []
        for removed in itertools.product([0,1],repeat=2):
            for added in itertools.product(range(12),range(12),range(3)):
                actual = original-removed[0]-7*removed[1]+added[0]+7*added[1]+250*added[2]
                if actual <= target:
                    options.append((-actual,sum(removed)+sum(added)))
        assert (-int(plan['value']),plan['mutations']) == min(options)


def test_nearest_below_target_without_unit_coin(saves):
    saves['AT_Home1.sav']=home([record(BED,[])])
    _, plan = comfort.plan(saves,'AT_ArkPark1.sav','','310',verify=False)
    assert plan['value']=='310'
    _, plan = comfort.plan(saves,'AT_ArkPark1.sav','','309',verify=False)
    assert plan['value']=='303' and plan['nearest']


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity','abc',''])
def test_invalid_target(value):
    with pytest.raises(SaveError): comfort.parse_target(value)


def test_type_bound_and_fractional():
    assert comfort.parse_target('1e2000') == (16777215, True, False)
    assert comfort.parse_target('-1e2000') == (0, True, False)
    assert comfort.parse_target('123.99') == (123, False, True)
    with pytest.raises(SaveError): comfort.parse_target('1'*2049)


def test_unknown_profile_class_and_version_fail_closed(saves,tmp_path):
    with pytest.raises(SaveError,match='游戏文件缺失'):comfort.verify_game(tmp_path)
    saves['AT_Home1.sav']=home([record('/Game/Unknown')])
    with pytest.raises(SaveError,match='未验证的家具类'):comfort.inspect(saves,'AT_ArkPark1.sav','',verify=False)
    saves.pop('AT_Home_Relocation_ID1_方舟大世界2_A.sav')
    with pytest.raises(SaveError,match='搬迁记录'):comfort.inspect(saves,'AT_ArkPark1.sav','',verify=False)


def test_player_added_bed_and_table_are_calculated(saves):
    saves['AT_Home1.sav']=home([record(BOX,[]),record(BED,[]),record(PLAYER,[3]),
                              record(BED,[]),record(PREFIX+'BP_家具_桌子6',[])])
    value=comfort.inspect(saves,'AT_ArkPark1.sav','',verify=False)[0]
    assert value['value']=='566'
    table=next(x for x in value['breakdown'] if x['label']=='家具_桌子6')
    assert table['each']=='5' and table['count']==1


def test_combined_api_transaction_and_group_conflict(saves,tmp_path,monkeypatch):
    monkeypatch.setattr(comfort,'verify_game',lambda path:None)
    folder=tmp_path/'saves';folder.mkdir();game=tmp_path/'game';game.mkdir()
    for name,data in saves.items():(folder/name).write_bytes(data)
    store=Store(tmp_path/'data',process_probe=lambda:False)
    store.set_settings({'save_dir':str(folder),'game_dir':str(game)})
    client=TestClient(create_app(store.root,store=store))
    headers={'x-editor-token':client.get('/api/session').json()['token']}
    doc=client.get('/api/saves/AT_ArkPark1.sav').json()
    payload={'version':doc['version'],'group_version':doc['group_version'],
             'changes':{'货币/Y':'1000'},'comfort_target':'555'}
    preview=client.post('/api/saves/AT_ArkPark1.sav/preview',headers=headers,json=payload)
    assert preview.status_code==200 and preview.json()['comfort']['value']=='555'
    assert store.snapshot(folder)==saves and store.list_backups()==[]
    result=client.post('/api/saves/AT_ArkPark1.sav/edit',headers=headers,json=payload)
    assert result.status_code==200 and result.json()['comfort']==preview.json()['comfort']
    assert store.backup_data(result.json()['recovery_id'])==saves
    changed=store.read_save('AT_ArkPark1.sav')
    assert changed['comfort']['value']=='555'
    assert next(f for f in changed['fields'] if f['shortcut']=='money')['value']=='1000.0'
    original=store.snapshot(folder)
    (folder/'AT_Home1.sav').write_bytes(home([record(BED,[])]))
    with pytest.raises(ServiceError,match='存档组已变化'):
        store.edit(changed['name'],changed['version'],{},'560',changed['group_version'])
    assert len(store.list_backups())==1
    store.process_probe=lambda:True
    with pytest.raises(ServiceError,match='退出'):store.edit(changed['name'],changed['version'],{},'560',changed['group_version'])


def test_real_save_total_and_group_unchanged():
    directory=Path(__file__).resolve().parents[1]/'build/verification/original-saves'
    if not directory.is_dir():pytest.skip('local read-only fixture unavailable')
    snapshot={p.name:p.read_bytes() for p in directory.glob('*.sav')}
    try:
        current=comfort.inspect(snapshot,'AT_ArkPark1.sav','',verify=False)[0]
    except SaveError:pytest.skip('fixture has no relocation profile')
    assert current['value']=='2207' and current['placed']=='1911'
    out, result=comfort.plan(snapshot,'AT_ArkPark1.sav','','2208',verify=False)
    assert result['mutations']==1 and result['value']=='2208'
    original=Document(snapshot['AT_Home1.sav']); patched=Document(out)
    a=next(a for a in original.arrays if a['path']==['S_建造系统'])
    b=next(a for a in patched.arrays if a['path']==['S_建造系统'])
    assert a['count']+1==b['count']
    assert all(original.data[x['start']:x['end']]==patched.data[y['start']:y['end']]
               for x,y in zip(a['entries'],b['entries']))
