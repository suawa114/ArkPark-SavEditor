$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败' }
}
& '.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw '安装 Python 依赖失败' }
Push-Location -LiteralPath frontend
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw '安装前端依赖失败' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
} finally { Pop-Location }
& '.venv\Scripts\python.exe' -m pytest -q
if ($LASTEXITCODE -ne 0) { throw '测试失败' }
$version = (Get-Content -LiteralPath 'frontend\package.json' -Raw | ConvertFrom-Json).version
$releaseDir = Join-Path 'release' "v$version"
& '.venv\Scripts\python.exe' -m PyInstaller --noconfirm --distpath $releaseDir ArkParkEditor.spec
if ($LASTEXITCODE -ne 0) { throw 'EXE 打包失败' }
& '.venv\Scripts\python.exe' tools/package_release.py --dist-dir $releaseDir
if ($LASTEXITCODE -ne 0) { throw '发布包生成失败' }
Write-Host "完成：$releaseDir\ArkParkEditor.exe（含便携包、源码包与校验清单）"
