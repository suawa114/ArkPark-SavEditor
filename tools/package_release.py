"""Package only this project's source and executable; exclude player data/assets."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
version = json.loads((root / 'frontend' / 'package.json').read_text(encoding='utf-8-sig'))['version']
dist = Path(sys.argv[sys.argv.index('--dist-dir')+1]).resolve() if '--dist-dir' in sys.argv else root / 'release' / f'v{version}'
dist.mkdir(parents=True, exist_ok=True)
guide = (root / 'README.md').read_text(encoding='utf-8-sig')
guide = re.sub(r'`(?:dist|release)/(?:v[0-9.]+/)?(ArkParkEditor\.(?:exe)|ArkParkEditor-portable\.zip)`', r'`\1`', guide)
guide = re.sub(r'`(?:dist|release)/v[0-9.]+/data/`', '`data/`', guide)
guide = re.sub(r'\[([^\]]+)\]\(((?:backend/|frontend/)[^)]+|launcher\.py)\)',
               lambda match: f'{match[1]}（源码包 `{match[2]}`）', guide)
(dist / '使用说明.md').write_text(guide, encoding='utf-8')
(dist / 'docs').mkdir(exist_ok=True)
for path in (root / 'docs').glob('*.md'):
    shutil.copy2(path, dist / 'docs' / path.name)
source_files = []
for folder in ('backend', 'frontend', 'tests', 'tools', 'docs'):
    for path in (root / folder).rglob('*'):
        if path.is_file() and not {'node_modules', '__pycache__'} & set(path.parts) and not path.is_relative_to(root / 'frontend' / 'dist'):
            source_files.append(path)
source_files += [root / name for name in ('README.md', 'launcher.py', 'ArkParkEditor.spec', 'build.ps1', 'requirements.txt', 'requirements-dev.txt', '.gitignore')]
with zipfile.ZipFile(dist / 'ArkParkEditor-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source_files):
        archive.write(path, 'ArkParkEditor/' + path.relative_to(root).as_posix())
with zipfile.ZipFile(dist / 'ArkParkEditor-portable.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.write(dist / 'ArkParkEditor.exe', 'ArkParkEditor.exe')
    archive.write(dist / '使用说明.md', '使用说明.md')
    for path in (root / 'docs').glob('*.md'):
        archive.write(path, 'docs/' + path.name)
manifest = {}
for name in ('ArkParkEditor.exe', 'ArkParkEditor-source.zip', 'ArkParkEditor-portable.zip'):
    path = dist / name
    manifest[name] = {'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
(dist / 'release-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps(manifest, indent=2))
