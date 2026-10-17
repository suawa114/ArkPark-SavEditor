import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.storage import Store, ServiceError, digest
from conftest import fixture_save


@pytest.fixture
def workspace(tmp_path):
    save = tmp_path / '游戏存档'
    game = tmp_path / '游戏目录'
    save.mkdir(); game.mkdir()
    (save / 'AT_ArkPark1.sav').write_bytes(fixture_save())
    (save / '家园.sav').write_bytes(fixture_save())
    (save / 'steam_autocloud.vdf').write_bytes(b'cloud metadata')
    store = Store(tmp_path / 'data', process_probe=lambda: False)
    store.set_settings({'save_dir': str(save), 'game_dir': str(game)})
    return store, save


def test_backup_names_and_exact_restore(workspace):
    store, save = workspace
    before = store.snapshot(save)
    backup = store.create_backup('第一份 / 备份:😀')
    duplicate = store.create_backup(backup['name'])
    assert backup['id'] != duplicate['id']
    store.rename_backup(backup['id'], '中文新名称')
    assert store.backup_data(backup['id']) == before
    (save/'extra.sav').write_bytes(fixture_save())
    (save/'家园.sav').write_bytes(b'changed')
    restored = store.restore(backup['id'])
    assert store.snapshot(save) == before
    assert 'extra.sav' in store.backup_data(restored['recovery_id'])
    assert (save/'steam_autocloud.vdf').read_bytes() == b'cloud metadata'
    store.delete_backup(duplicate['id'])
    assert len(store.list_backups()) == 2


def test_edit_version_and_auto_backup(workspace):
    store, save = workspace
    original = store.snapshot(save)
    version = digest(original['AT_ArkPark1.sav'])
    result = store.edit('AT_ArkPark1.sav', version, {'货币/Y':'1e1000'})
    assert result['applied'][0]['clamped']
    assert store.backup_data(result['recovery_id']) == original
    assert store.read_save('AT_ArkPark1.sav')['version'] == result['version']
    with pytest.raises(ServiceError, match='版本已变化'):
        store.edit('AT_ArkPark1.sav', version, {'货币/Y':'1'})


def test_game_running_blocks_mutations(workspace):
    store, save = workspace
    doc = store.read_save('AT_ArkPark1.sav')
    backup = store.create_backup('test')
    store.process_probe = lambda: True
    assert store.read_save('AT_ArkPark1.sav')
    for action in [lambda:store.create_backup('blocked'), lambda:store.restore(backup['id']), lambda:store.edit(doc['name'],doc['version'],{'货币/Y':'1'})]:
        with pytest.raises(ServiceError, match='退出'): action()


def test_corrupt_backup_never_overwrites(workspace):
    store, save = workspace
    original = store.snapshot(save)
    backup = store.create_backup('test')
    (store.backup_root / backup['id'] / '家园.sav').write_bytes(b'corrupt')
    with pytest.raises(ServiceError, match='校验失败'): store.restore(backup['id'])
    assert store.snapshot(save) == original


def test_midway_failure_rolls_back(workspace, monkeypatch):
    store, save = workspace
    original = store.snapshot(save)
    backup = store.create_backup('test')
    (save / 'AT_ArkPark1.sav').write_bytes(b'changed first')
    (save / '家园.sav').write_bytes(b'changed')
    before_restore = store.snapshot(save)
    real_replace = os.replace
    triggered = False
    def fail_once(src, dst):
        nonlocal triggered
        if Path(dst) == save / '家园.sav' and not triggered:
            triggered = True
            raise OSError('simulated write failure')
        return real_replace(src,dst)
    monkeypatch.setattr(os, 'replace', fail_once)
    with pytest.raises(OSError):store.restore(backup['id'])
    assert triggered and store.snapshot(save) == before_restore
    assert not store.pending()
    assert store.history()[0]['status'] == 'rolled_back'


def test_external_change_during_staging_is_never_rolled_back(workspace, monkeypatch):
    store, save = workspace
    before = store.snapshot(save)
    original_verify = store.verify_snapshot
    calls = 0
    def changed_during_staging(directory, expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            (save / '家园.sav').write_bytes(b'new progress')
        return original_verify(directory, expected)
    monkeypatch.setattr(store, 'verify_snapshot', changed_during_staging)
    with pytest.raises(ServiceError, match='其他程序修改'):
        store.edit('AT_ArkPark1.sav', digest(before['AT_ArkPark1.sav']), {'货币/Y':'123'})
    assert (save / '家园.sav').read_bytes() == b'new progress'
    assert (save / 'AT_ArkPark1.sav').read_bytes() == before['AT_ArkPark1.sav']
    assert not store.pending()
    assert store.history()[0]['status'] == 'aborted'


def test_restore_into_empty_directory(workspace):
    store, save = workspace
    before = store.snapshot(save)
    backup = store.create_backup('test')
    for path in store.files(save).values(): path.unlink()
    result = store.restore(backup['id'])
    assert store.snapshot(save) == before
    assert store.backup_data(result['recovery_id']) == {}


def test_restart_recovers_journal_and_protects_recovery_backup(workspace):
    store, save = workspace
    original = store.snapshot(save)
    recovery = store.create_backup('recovery')
    store.log('edit','applying',{},recovery['id'],save)
    (save/'家园.sav').write_bytes(b'partial')
    with pytest.raises(ServiceError):store.delete_backup(recovery['id'])
    restarted = Store(store.root, process_probe=lambda: False)
    assert restarted.snapshot(save) == original and not restarted.pending()


def test_low_disk_and_external_changes(workspace, monkeypatch):
    store, save = workspace
    original = store.snapshot(save)
    monkeypatch.setattr('backend.storage.shutil.disk_usage',lambda p:SimpleNamespace(free=0))
    with pytest.raises(ServiceError, match='空间不足'):store.create_backup('test')
    assert store.snapshot(save) == original
    (save/'新增.sav').write_bytes(fixture_save())
    with pytest.raises(ServiceError, match='其他程序修改'):store.verify_snapshot(save, original)


def test_paths_and_settings(workspace):
    store, save = workspace
    for name in ['../evil.sav', r'..\evil.sav', 'C:bad.sav', 'manifest.json']:
        with pytest.raises(ServiceError):store.read_save(name)
    with pytest.raises(ServiceError):store.set_settings({'save_dir':str(store.root),'game_dir':str(save)})


def test_game_start_mid_restore_defers_rollback_until_exit(workspace, monkeypatch):
    store, save = workspace
    backup = store.create_backup('previous')
    for path in store.files(save).values():
        path.write_bytes(b'new progress')
    before = store.snapshot(save)
    running = False
    real_replace = os.replace
    def start_after_first_save(src, dst):
        nonlocal running
        result = real_replace(src, dst)
        if Path(dst).parent == save:
            running = True
        return result
    monkeypatch.setattr(os, 'replace', start_after_first_save)
    store.process_probe = lambda: running
    with pytest.raises(ServiceError, match='游戏启动'):
        store.restore(backup['id'])
    assert len(store.pending()) == 1
    assert store.recover()['pending'] == 1
    running = False
    monkeypatch.setattr(os, 'replace', real_replace)
    assert store.recover()['pending'] == 0
    assert store.snapshot(save) == before
