"""Bounded, read-only discovery of local ArkPark2 and Steam directories."""
from __future__ import annotations

import os
from pathlib import Path
import re


def default_save_dir() -> Path:
    local = os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))
    return Path(local) / 'ArkPark2' / 'Saved' / 'SaveGames'


def _vdf(path: Path) -> dict:
    """Read Valve's quoted key/value format, including escaped Windows paths."""
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return {}
        source = path.read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        return {}
    tokens = re.findall(r'"((?:\\.|[^"\\])*)"|([{}])', source)
    index = 0

    def parse(depth=0):
        nonlocal index
        result = {}
        if depth > 16:
            return result
        while index < len(tokens):
            key, brace = tokens[index]
            index += 1
            if brace == '}':
                return result
            if brace or index >= len(tokens):
                continue
            value, brace = tokens[index]
            index += 1
            key = re.sub(r'\\([\\"])', r'\1', key)
            if brace == '{':
                result[key] = parse(depth + 1)
            elif not brace:
                result[key] = re.sub(r'\\([\\"])', r'\1', value)
        return result

    return parse()


def _registry_steam_paths() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for hive, name, value in (
        (winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Valve\Steam', 'InstallPath'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\Valve\Steam', 'InstallPath'),
    ):
        try:
            with winreg.OpenKey(hive, name) as key:
                path, _ = winreg.QueryValueEx(key, value)
                if isinstance(path, str) and path:
                    found.append(Path(path))
        except OSError:
            continue
    return found


def steam_roots() -> list[Path]:
    roots = _registry_steam_paths()
    for variable in ('ProgramFiles(x86)', 'ProgramFiles', 'PROGRAMW6432'):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]) / 'Steam')
    if os.name == 'nt':
        for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            roots.extend((Path(f'{letter}:/Steam'), Path(f'{letter}:/SteamLibrary'),
                          Path(f'{letter}:/Tools/Steam')))
    return _unique(roots)


def _unique(paths) -> list[Path]:
    result, seen = [], set()
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _is_game(path: Path) -> bool:
    return (path / 'ArkPark2.exe').is_file() or (
        path / 'ArkPark2' / 'Binaries' / 'Win64' / 'ArkPark2-Win64-Shipping.exe').is_file()


def detect_game_dir(preferred: str = '') -> Path | None:
    if preferred and _is_game(Path(preferred)):
        return Path(preferred)
    roots = steam_roots()
    libraries = list(roots)
    for root in roots:
        tree = _vdf(root / 'steamapps' / 'libraryfolders.vdf')
        folders = tree.get('libraryfolders', tree.get('LibraryFolders', {}))
        if isinstance(folders, dict):
            for key, entry in folders.items():
                value = entry.get('path') if isinstance(entry, dict) else entry
                if key.isdigit() and isinstance(value, str) and value:
                    libraries.append(Path(value))
    for library in _unique(libraries):
        steamapps = library / 'steamapps'
        candidate = steamapps / 'common' / 'ArkPark2'
        if _is_game(candidate):
            return candidate
        try:
            manifests = steamapps.glob('appmanifest_*.acf')
            for count, manifest in enumerate(manifests):
                if count >= 2000:
                    break
                state = _vdf(manifest).get('AppState', {})
                if not isinstance(state, dict):
                    continue
                install = state.get('installdir', '')
                if not isinstance(install, str) or not install or Path(install).name != install:
                    continue
                candidate = steamapps / 'common' / install
                if _is_game(candidate):
                    return candidate
        except OSError:
            continue
    return None


def detect_paths(settings: dict) -> dict:
    preferred_save = Path(settings.get('save_dir') or default_save_dir())
    default_save = default_save_dir()
    save = preferred_save if preferred_save.is_dir() else default_save
    game = detect_game_dir(settings.get('game_dir', ''))
    return {
        'save_dir': str(save),
        'game_dir': str(game) if game else settings.get('game_dir', ''),
        'save_detected': save.is_dir(),
        'game_detected': game is not None,
    }
