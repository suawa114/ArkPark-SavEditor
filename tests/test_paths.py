from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.api import create_app
from backend import paths
from backend.storage import Store, ServiceError


def test_detects_steam_secondary_library_and_manifest(tmp_path, monkeypatch):
    steam, library = tmp_path / 'Steam', tmp_path / 'Other library'
    (steam / 'steamapps').mkdir(parents=True)
    (library / 'steamapps').mkdir(parents=True)
    game = library / 'steamapps' / 'common' / 'custom-install-name'
    game.mkdir(parents=True)
    (game / 'ArkPark2.exe').write_bytes(b'test executable')
    escaped = str(library).replace('\\', '\\\\')
    (steam / 'steamapps' / 'libraryfolders.vdf').write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{escaped}" }} }}', encoding='utf-8')
    (library / 'steamapps' / 'appmanifest_123.acf').write_text(
        '"AppState" { "name" "ArkPark 2" "installdir" "custom-install-name" }', encoding='utf-8')
    monkeypatch.setattr(paths, 'steam_roots', lambda: [steam])
    assert paths.detect_game_dir() == game


def test_vdf_escapes_and_invalid_files(tmp_path):
    target = tmp_path / 'libraryfolders.vdf'
    target.write_text(r'"libraryfolders" { "0" { "path" "D:\\Games\\Steam" } }', encoding='utf-8')
    assert paths._vdf(target)['libraryfolders']['0']['path'] == r'D:\Games\Steam'
    assert paths._vdf(tmp_path / 'missing') == {}


def test_setup_is_required_once_and_suggestions_do_not_change_settings(tmp_path, monkeypatch):
    local = tmp_path / 'Local'
    save = local / 'ArkPark2' / 'Saved' / 'SaveGames'
    game = tmp_path / 'Game'
    save.mkdir(parents=True)
    game.mkdir()
    (game / 'ArkPark2.exe').write_bytes(b'test executable')
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    monkeypatch.setattr(paths, 'detect_game_dir', lambda preferred='': game)
    store = Store(tmp_path / 'data', process_probe=lambda: False)
    before = store.settings()
    assert store.setup() == {
        'required': True, 'save_dir': str(save), 'game_dir': str(game),
        'save_detected': True, 'game_detected': True,
    }
    assert store.settings() == before
    with pytest.raises(ServiceError, match='首次使用'):
        store.list_saves()
    store.set_settings({'save_dir': str(save), 'game_dir': str(game)})
    assert not store.setup()['required']
    assert not Store(store.root).setup()['required']


def test_failed_confirmation_does_not_finish_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'steam_roots', lambda: [])
    store = Store(tmp_path / 'data')
    with pytest.raises(ServiceError, match='请填写'):
        store.set_settings({'save_dir': ' ', 'game_dir': str(tmp_path)})
    with pytest.raises(ServiceError, match='必须存在'):
        store.set_settings({'save_dir': str(tmp_path / 'missing'), 'game_dir': str(tmp_path)})
    assert store.setup()['required']
    assert not paths.detect_paths({'save_dir': str(tmp_path / 'missing'), 'game_dir': ''})['game_detected']


def test_setup_api_session_protection_and_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'steam_roots', lambda: [])
    store = Store(tmp_path / 'data')
    client = TestClient(create_app(store.root, store=store))
    assert client.get('/api/setup').json()['required']
    for endpoint in ('/api/saves', '/api/backups', '/api/history'):
        assert client.get(endpoint).status_code == 409
    values = {'save_dir': str(tmp_path), 'game_dir': str(tmp_path)}
    assert client.post('/api/setup', json=values).status_code == 403
    token = client.get('/api/session').json()['token']
    headers = {'x-editor-token': token}
    assert client.post('/api/setup', headers=headers, json=values).status_code == 400  # overlaps data
    save, game = tmp_path / 'saves', tmp_path / 'game'
    save.mkdir()
    game.mkdir()
    values = {'save_dir': str(save), 'game_dir': str(game)}
    assert client.post('/api/setup', headers=headers, json=values).status_code == 200
    assert not client.get('/api/setup').json()['required']
    assert client.get('/api/saves').status_code == 200
