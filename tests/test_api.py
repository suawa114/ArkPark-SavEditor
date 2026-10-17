from fastapi.testclient import TestClient
from backend.api import create_app
from backend.storage import Store
from conftest import fixture_save


def test_api_security_and_roundtrip(tmp_path):
    save, game = tmp_path/'saves', tmp_path/'game'
    save.mkdir();game.mkdir()
    (save/'AT_ArkPark1.sav').write_bytes(fixture_save())
    store = Store(tmp_path/'data',process_probe=lambda:False)
    store.set_settings({'save_dir':str(save),'game_dir':str(game)})
    client = TestClient(create_app(store.root,store=store))
    token = client.get('/api/session').json()['token']
    headers = {'x-editor-token':token}
    assert client.get('/api/status',headers={'host':'evil.example'}).status_code == 403
    assert client.post('/api/backups',json={'name':'test'}).status_code == 403
    assert client.post('/api/backups',headers={**headers,'origin':'https://evil.example'},json={'name':'test'}).status_code == 403
    doc = client.get('/api/saves/AT_ArkPark1.sav').json()
    money = next(f for f in doc['fields'] if f['shortcut']=='money')
    assert isinstance(money['maximum'],str)
    result = client.post('/api/saves/AT_ArkPark1.sav/edit',headers=headers,json={'version':doc['version'],'changes':{'货币/Y':'900719925474099100000'}})
    assert result.status_code == 200
    assert result.json()['applied'][0]['value']=='9007199254740991.0'
    assert result.json()['applied'][0]['clamped']
    conflict = client.post('/api/saves/AT_ArkPark1.sav/edit',headers=headers,json={'version':doc['version'],'changes':{'货币/Y':'1'}})
    assert conflict.status_code == 409
    assert len(client.get('/api/backups').json()) == 1
