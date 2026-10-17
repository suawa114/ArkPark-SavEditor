from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid

import psutil

from .gvas import Document, SaveError
from . import comfort as comfort_service
from . import gameplay
from . import slots
from .paths import default_save_dir, detect_paths


class ServiceError(ValueError):
    def __init__(self, message: str, status=400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or Path(name).name != name or any(c in name for c in '/\\:') or not name.lower().endswith('.sav'):
        raise ServiceError('无效存档文件名')
    return name


class Store:
    def __init__(self, data_dir: Path, process_probe=None):
        self.root = data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.backup_root = self.root / 'backups'
        self.backup_root.mkdir(exist_ok=True)
        self.db_path = self.root / 'editor.sqlite3'
        self.lock = threading.RLock()
        self.process_probe = process_probe or self.game_running
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS backups (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    kind TEXT NOT NULL, manifest TEXT NOT NULL, source_dir TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    recovery_id TEXT, target_dir TEXT, details TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
            defaults = {'save_dir': str(default_save_dir()),
                        'game_dir': r'D:\Tools\Steam\steamapps\common\ArkPark2',
                        'setup_complete': 'false'}
            for key, value in defaults.items():
                db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, value))
        self.recover()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA synchronous=FULL')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def settings(self) -> dict:
        with self.db() as db:
            return dict(db.execute('SELECT key,value FROM settings').fetchall())

    def setup(self) -> dict:
        settings = self.settings()
        required = settings.get('setup_complete') != 'true'
        return {'required': required, **detect_paths(settings)}

    def set_settings(self, values: dict):
        with self.lock:
            if self.pending():
                raise ServiceError('请先完成中断操作的恢复', 409)
            if any(not isinstance(values.get(key), str) or not values[key].strip()
                   or '\0' in values[key] for key in ('save_dir', 'game_dir')):
                raise ServiceError('请填写存档目录和游戏目录')
            save = Path(os.path.expandvars(values['save_dir'].strip())).expanduser().resolve()
            game = Path(os.path.expandvars(values['game_dir'].strip())).expanduser().resolve()
            if not save.is_dir() or not game.is_dir():
                raise ServiceError('存档目录和游戏目录必须存在')
            if save == self.root or save in self.root.parents or self.root in save.parents:
                raise ServiceError('存档目录不能与修改器数据目录重叠')
            with self.db() as db:
                for key, value in [('save_dir', str(save)), ('game_dir', str(game))]:
                    db.execute('UPDATE settings SET value=? WHERE key=?', (value, key))
                db.execute("UPDATE settings SET value='true' WHERE key='setup_complete'")
            return self.settings()

    def save_dir(self) -> Path:
        settings = self.settings()
        if settings.get('setup_complete') != 'true':
            raise ServiceError('首次使用请先确认游戏目录和存档目录', 409)
        path = Path(settings['save_dir'])
        if not path.is_dir():
            raise ServiceError('存档目录不存在，请在设置中选择正确目录', 404)
        return path

    def game_running(self) -> bool:
        for process in psutil.process_iter(['name']):
            try:
                name = (process.info['name'] or '').lower()
                if name in ('arkpark2.exe', 'arkpark2-win64-shipping.exe'):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def pending(self) -> list[dict]:
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM operations WHERE status='applying' ORDER BY created_at")]

    def writable(self):
        if self.process_probe():
            raise ServiceError('请先退出 ArkPark2，再修改、备份或加载存档', 409)
        if self.pending():
            raise ServiceError('存在中断操作，请先执行恢复', 409)

    def files(self, directory: Path) -> dict[str, Path]:
        result = {}
        for path in directory.iterdir():
            if path.suffix.lower() != '.sav':
                continue
            safe_name(path.name)
            if path.is_symlink() or not path.is_file():
                raise ServiceError('存档必须是普通文件，不能使用链接或目录')
            result[path.name] = path
        return result

    def snapshot(self, directory: Path) -> dict[str, bytes]:
        paths = self.files(directory)
        result = {}
        for name, path in paths.items():
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ServiceError('读取期间存档发生变化，请重新读取', 409)
            result[name] = data
        if set(paths) != set(self.files(directory)):
            raise ServiceError('存档文件组发生变化，请重新读取', 409)
        return result

    def verify_snapshot(self, directory: Path, expected: dict[str, bytes]):
        current = self.snapshot(directory)
        if {k: digest(v) for k, v in current.items()} != {k: digest(v) for k, v in expected.items()}:
            raise ServiceError('存档已被其他程序修改，请重新读取', 409)

    def list_saves(self) -> dict:
        directory = self.save_dir()
        entries = []
        for name, path in self.files(directory).items():
            stat = path.stat()
            entries.append({'name': name, 'size': stat.st_size,
                            'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
        entries.sort(key=lambda x: x['modified_at'], reverse=True)
        playable = [x for x in entries if x['name'].startswith('AT_ArkPark')]
        selected = (playable or entries)[0]['name'] if entries else None
        return {'files': entries, 'selected': selected}

    def list_slots(self) -> dict:
        with self.lock:
            directory = self.save_dir()
            snapshot = self.snapshot(directory)
            metadata = {entry['name']: entry for entry in self.list_saves()['files']}
            result = slots.inspect(snapshot, metadata, self.settings()['game_dir'])
            self.verify_snapshot(directory, snapshot)
            return result

    def read_save(self, name: str) -> dict:
        name = safe_name(name)
        path = self.save_dir() / name
        if not path.is_file() or path.is_symlink():
            raise ServiceError('存档不存在', 404)
        snapshot = self.snapshot(self.save_dir())
        doc = Document(snapshot[name])
        home_name = {f'AT_ArkPark{i}.sav': 'AT_Home.sav' if i == 0 else f'AT_Home{i}.sav'
                     for i in range(4)}.get(name)
        home = None
        if home_name in snapshot:
            try:
                home = Document(snapshot[home_name])
            except SaveError:
                pass  # A broken home must not fabricate zero or hide main-save statistics.
        try:
            comfort = comfort_service.inspect(snapshot, name, self.settings()['game_dir'])[0]
        except SaveError as exc:
            comfort = {'available': False, 'reason': str(exc)}
        self.verify_snapshot(self.save_dir(), snapshot)
        return {'name': name, 'version': doc.version, 'engine': doc.branch,
                'group_version': comfort_service.group_version(snapshot),
                'fields': [f.public() for f in doc.fields], 'opaque': doc.opaque,
                'comfort': comfort, 'gameplay': gameplay.inspect(doc, self.settings()['game_dir'], home)}

    @staticmethod
    def manifest(snapshot: dict[str, bytes]) -> list[dict]:
        return [{'name': name, 'size': len(data), 'sha256': digest(data)} for name, data in sorted(snapshot.items())]

    def backup_from(self, snapshot: dict[str, bytes], name: str, kind: str, source: Path) -> dict:
        name = name.strip()
        if not name or len(name) > 200:
            raise ServiceError('备份名称需为 1–200 个字符')
        if not snapshot and kind != 'automatic':
            raise ServiceError('没有可备份的 .sav 文件')
        required = sum(len(data) for data in snapshot.values())
        if shutil.disk_usage(self.root).free < required + 1024 * 1024:
            raise ServiceError('备份磁盘空间不足', 507)
        key = uuid.uuid4().hex
        staging = self.backup_root / (key + '.tmp')
        destination = self.backup_root / key
        manifest = self.manifest(snapshot)
        try:
            staging.mkdir()
            for entry in manifest:
                path = staging / safe_name(entry['name'])
                with path.open('xb') as f:
                    f.write(snapshot[entry['name']])
                    f.flush()
                    os.fsync(f.fileno())
                if digest(path.read_bytes()) != entry['sha256']:
                    raise ServiceError('备份文件校验失败')
            with (staging / 'manifest.json').open('x', encoding='utf-8') as f:
                f.write(json.dumps(manifest, ensure_ascii=False, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(staging, destination)
            created = now()
            with self.db() as db:
                db.execute('INSERT INTO backups VALUES (?,?,?,?,?,?)',
                           (key, name, created, kind, json.dumps(manifest, ensure_ascii=False), str(source)))
            return {'id': key, 'name': name, 'created_at': created, 'kind': kind,
                    'files': manifest, 'size': required, 'source_dir': str(source)}
        except BaseException:
            # Only task-owned temporary directories are removed; committed snapshots remain recoverable.
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def create_backup(self, name: str) -> dict:
        with self.lock:
            self.writable()
            directory = self.save_dir()
            snapshot = self.snapshot(directory)
            self.verify_snapshot(directory, snapshot)
            self.writable()
            result = self.backup_from(snapshot, name, 'manual', directory)
            self.log('backup', 'complete', {'backup_id': result['id']})
            return result

    def list_backups(self, query='') -> list[dict]:
        with self.db() as db:
            rows = db.execute('SELECT * FROM backups ORDER BY created_at DESC').fetchall()
        result = []
        for row in rows:
            if query.casefold() not in row['name'].casefold():
                continue
            manifest = json.loads(row['manifest'])
            result.append({'id': row['id'], 'name': row['name'], 'created_at': row['created_at'],
                           'kind': row['kind'], 'files': manifest, 'size': sum(x['size'] for x in manifest),
                           'source_dir': row['source_dir']})
        return result

    def backup_data(self, key: str) -> dict[str, bytes]:
        with self.db() as db:
            row = db.execute('SELECT manifest FROM backups WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ServiceError('备份不存在', 404)
        manifest = json.loads(row['manifest'])
        snapshot = {}
        for entry in manifest:
            name = safe_name(entry['name'])
            path = self.backup_root / key / name
            if path.is_symlink() or not path.is_file():
                raise ServiceError('备份文件缺失或无效')
            data = path.read_bytes()
            if len(data) != entry['size'] or digest(data) != entry['sha256']:
                raise ServiceError('备份校验失败，未覆盖游戏存档')
            snapshot[name] = data
        return snapshot

    def rename_backup(self, key: str, name: str):
        name = name.strip()
        if not name or len(name) > 200:
            raise ServiceError('备份名称需为 1–200 个字符')
        with self.lock, self.db() as db:
            if db.execute('UPDATE backups SET name=? WHERE id=?', (name, key)).rowcount == 0:
                raise ServiceError('备份不存在', 404)

    def delete_backup(self, key: str):
        with self.lock:
            if any(row['recovery_id'] == key for row in self.pending()):
                raise ServiceError('该备份用于未完成操作的恢复，暂不能删除', 409)
            with self.db() as db:
                row = db.execute('SELECT id FROM backups WHERE id=?', (key,)).fetchone()
                if row is None:
                    raise ServiceError('备份不存在', 404)
                # ID is taken from SQLite, never an unchecked client filesystem path.
                path = self.backup_root / row['id']
                if path.exists():
                    shutil.rmtree(path)
                db.execute('DELETE FROM backups WHERE id=?', (key,))
            self.log('delete_backup', 'complete', {'backup_id': key})

    def log(self, kind, status, details, recovery_id=None, target_dir=None) -> str:
        key = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)',
                       (key, kind, status, now(), now(), recovery_id, str(target_dir) if target_dir else None,
                        json.dumps(details, ensure_ascii=False)))
        return key

    def finish(self, key: str, status: str, error=''):
        with self.db() as db:
            if error:
                details = json.loads(db.execute('SELECT details FROM operations WHERE id=?', (key,)).fetchone()[0])
                details['error'] = error
                db.execute('UPDATE operations SET details=? WHERE id=?', (json.dumps(details, ensure_ascii=False), key))
            db.execute('UPDATE operations SET status=?,updated_at=? WHERE id=?', (status, now(), key))

    def replace_exact(self, directory: Path, snapshot: dict[str, bytes], expected_before: dict[str, bytes] | None = None):
        required = sum(len(v) for v in snapshot.values())
        if shutil.disk_usage(directory).free < required + 1024 * 1024:
            raise ServiceError('存档磁盘空间不足', 507)
        staged = []
        changed = False
        try:
            for name, data in snapshot.items():
                temp = directory / ('.arkeditor-' + uuid.uuid4().hex + '.tmp')
                staged.append((temp, directory / safe_name(name)))
                with temp.open('xb') as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                if digest(temp.read_bytes()) != digest(data):
                    raise ServiceError('临时文件校验失败')
            if expected_before is not None:
                self.verify_snapshot(directory, expected_before)
            for temp, destination in staged:
                if self.process_probe():
                    raise ServiceError('游戏启动，操作暂停；退出游戏后执行恢复', 409)
                if destination.is_symlink():
                    raise ServiceError('检测到存档链接，拒绝覆盖')
                # Preserve unrelated files and their timestamps when their contents match.
                if destination.is_file() and digest(destination.read_bytes()) == digest(snapshot[destination.name]):
                    continue
                os.replace(temp, destination)
                changed = True
            for name, path in self.files(directory).items():
                if name not in snapshot:
                    if self.process_probe():
                        raise ServiceError('游戏启动，操作暂停；退出游戏后执行恢复', 409)
                    path.unlink()
                    changed = True
            self.verify_snapshot(directory, snapshot)
        except Exception as exc:
            exc.save_changed = changed
            raise
        finally:
            for temp, _ in staged:
                temp.unlink(missing_ok=True)

    def apply(self, kind: str, directory: Path, before: dict[str, bytes], after: dict[str, bytes], details: dict) -> str:
        recovery = self.backup_from(before, '自动恢复点 · ' + ('修改前' if kind == 'edit' else '加载前') + ' · ' + now(), 'automatic', directory)
        self.verify_snapshot(directory, before)
        self.writable()
        operation = self.log(kind, 'applying', details, recovery['id'], directory)
        try:
            self.replace_exact(directory, after, expected_before=before)
            self.finish(operation, 'complete')
        except Exception as exc:
            if not getattr(exc, 'save_changed', True):
                self.finish(operation, 'aborted', str(exc))
            elif not self.process_probe():
                try:
                    self.replace_exact(directory, self.backup_data(recovery['id']))
                    self.finish(operation, 'rolled_back', str(exc))
                except Exception as rollback_error:
                    raise ServiceError(f'写入失败，恢复未完成；恢复点已保留：{rollback_error}', 500) from exc
            raise
        return recovery['id']

    def prepare_edit(self, snapshot, name, changes, comfort_target=None, weapon_skills=None):
        after = dict(snapshot)
        applied = []
        story = None
        if changes:
            after[name], applied, story = gameplay.patch_numbers(snapshot[name], changes, self.settings()['game_dir'])
        after[name], skills = gameplay.patch_skills(after[name], weapon_skills or {}, self.settings()['game_dir'])
        preview = None
        if comfort_target is not None:
            output, preview = comfort_service.plan(after, name, self.settings()['game_dir'], comfort_target)
            after[preview['home_file']] = output
        return after, applied, preview, skills, story

    def preview_edit(self, name, version, changes, comfort_target=None, group_version=None, weapon_skills=None):
        with self.lock:
            snapshot = self.snapshot(self.save_dir())
            self.check_edit_version(snapshot, name, version, comfort_target, group_version)
            _, applied, preview, skills, story = self.prepare_edit(snapshot, name, changes, comfort_target, weapon_skills)
            return {'applied': applied, 'comfort': preview, 'weapon_skills': skills, 'story': story,
                    'group_version': comfort_service.group_version(snapshot)}

    @staticmethod
    def check_edit_version(snapshot, name, version, comfort_target, group_version):
        safe_name(name)
        if name not in snapshot:
            raise ServiceError('存档不存在', 404)
        if digest(snapshot[name]) != version:
            raise ServiceError('存档版本已变化，请重新读取后修改', 409)
        if comfort_target is not None and group_version != comfort_service.group_version(snapshot):
            raise ServiceError('家园或搬迁存档组已变化，请重新读取后修改', 409)

    def edit(self, name: str, version: str, changes: dict[str, str], comfort_target=None, group_version=None, weapon_skills=None) -> dict:
        with self.lock:
            self.writable()
            directory = self.save_dir()
            before = self.snapshot(directory)
            name = safe_name(name)
            self.check_edit_version(before, name, version, comfort_target, group_version)
            if not changes and comfort_target is None and not weapon_skills:
                raise ServiceError('没有待保存的修改')
            after, applied, preview, skills, story = self.prepare_edit(before, name, changes, comfort_target, weapon_skills)
            recovery = self.apply('edit', directory, before, after, {'file': name, 'changes': applied, 'comfort': preview, 'weapon_skills': skills, 'story': story})
            return {'version': digest(after[name]), 'applied': applied, 'comfort': preview, 'weapon_skills': skills, 'story': story, 'recovery_id': recovery}

    def restore(self, key: str) -> dict:
        with self.lock:
            self.writable()
            after = self.backup_data(key)
            if not after:
                raise ServiceError('备份为空，拒绝恢复')
            directory = self.save_dir()
            before = self.snapshot(directory)
            recovery = self.apply('restore', directory, before, after, {'backup_id': key})
            return {'recovery_id': recovery, 'files': len(after)}

    def recover(self) -> dict:
        with self.lock:
            recovered = []
            if self.process_probe():
                return {'recovered': [], 'pending': len(self.pending())}
            for operation in self.pending():
                try:
                    directory = Path(operation['target_dir'])
                    if not directory.is_dir():
                        raise ServiceError('原存档目录不存在')
                    self.replace_exact(directory, self.backup_data(operation['recovery_id']))
                    self.finish(operation['id'], 'rolled_back', '中断操作已恢复')
                    recovered.append(operation['id'])
                except Exception:
                    break
            return {'recovered': recovered, 'pending': len(self.pending())}

    def history(self) -> list[dict]:
        with self.db() as db:
            rows = db.execute('SELECT * FROM operations ORDER BY created_at DESC LIMIT 100').fetchall()
        return [{**dict(row), 'details': json.loads(row['details'])} for row in rows]
