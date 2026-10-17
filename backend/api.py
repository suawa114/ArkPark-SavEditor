from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .gvas import SaveError
from .storage import Store, ServiceError


class EditRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    version: str
    changes: dict[str, str] = Field(default_factory=dict, max_length=10000)
    comfort_target: str | None = Field(default=None, max_length=2048)
    group_version: str | None = None
    weapon_skills: dict[str, list[str]] = Field(default_factory=dict, max_length=64)


class NameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SettingsRequest(BaseModel):
    save_dir: str
    game_dir: str


def create_app(data_dir: Path, web_dir: Path | None = None, store: Store | None = None, shutdown=None):
    app = FastAPI(title='ArkPark2 存档修改器', docs_url=None, redoc_url=None)
    store = store or Store(data_dir)
    token = secrets.token_urlsafe(32)
    app.state.store = store

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost', 'testserver'):
            return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != 'http://' + request.headers.get('host', ''):
            return JSONResponse({'detail': '不允许跨站请求'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not secrets.compare_digest(request.headers.get('x-editor-token', ''), token):
            return JSONResponse({'detail': '会话已过期，请刷新页面'}, status_code=403)
        if any(request.url.path.startswith(prefix) for prefix in ('/api/saves', '/api/backups', '/api/history')) and store.settings().get('setup_complete') != 'true':
            return JSONResponse({'detail': '首次使用请先确认游戏目录和存档目录'}, status_code=409)
        response = await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=exc.status)

    @app.exception_handler(SaveError)
    async def save_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.exception_handler(OSError)
    async def io_error(request, exc):
        return JSONResponse({'detail': f'文件操作失败：{exc}'}, status_code=500)

    @app.get('/api/session')
    def session():
        return {'token': token}

    @app.get('/api/status')
    def status():
        return {'game_running': store.process_probe(), 'pending_recovery': len(store.pending()),
                'data_dir': str(store.root), 'comfort_verified': False}

    @app.get('/api/settings')
    def settings():
        return store.settings()

    @app.get('/api/setup')
    def setup():
        return store.setup()

    @app.post('/api/setup')
    def confirm_setup(values: SettingsRequest):
        return store.set_settings(values.model_dump())

    @app.put('/api/settings')
    def save_settings(values: SettingsRequest):
        return store.set_settings(values.model_dump())

    @app.get('/api/saves')
    def saves():
        return store.list_saves()

    @app.get('/api/saves/slots')
    def save_slots():
        return store.list_slots()

    @app.get('/api/saves/{name}')
    def read_save(name: str):
        return store.read_save(name)

    @app.post('/api/saves/{name}/edit')
    def edit(name: str, values: EditRequest):
        return store.edit(name, values.version, values.changes, values.comfort_target, values.group_version, values.weapon_skills)

    @app.post('/api/saves/{name}/preview')
    def preview(name: str, values: EditRequest):
        return store.preview_edit(name, values.version, values.changes, values.comfort_target, values.group_version, values.weapon_skills)

    @app.get('/api/backups')
    def backups(q: str = ''):
        return store.list_backups(q)

    @app.post('/api/backups')
    def create_backup(values: NameRequest):
        return store.create_backup(values.name)

    @app.patch('/api/backups/{key}')
    def rename_backup(key: str, values: NameRequest):
        store.rename_backup(key, values.name)
        return {'ok': True}

    @app.delete('/api/backups/{key}')
    def delete_backup(key: str):
        store.delete_backup(key)
        return {'ok': True}

    @app.post('/api/backups/{key}/restore')
    def restore(key: str):
        return store.restore(key)

    @app.post('/api/recovery')
    def recovery():
        return store.recover()

    @app.get('/api/history')
    def history():
        return store.history()

    @app.post('/api/shutdown')
    def stop():
        with store.lock:
            if shutdown:
                shutdown()
        return {'ok': True}

    if web_dir and web_dir.is_dir():
        app.mount('/', StaticFiles(directory=web_dir, html=True), name='web')
    return app
