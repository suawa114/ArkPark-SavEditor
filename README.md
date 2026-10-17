<h1 align="center">ArkPark2 存档修改器</h1>

<p align="center">中文本地 Web 工具，支持货币、枪械、背包参数修改和完整存档备份恢复。</p>

## 功能

- **货币修改** 修改金瓜子数量
- **编辑枪械** 自定义枪械属性等级、火力、备弹、射速、移速、稳定；修改枪械附魔(附加技能)  
- **物资编辑** 修改背包物资数量
- **查看进度** 查看当前剧情进度(剧情调整为实验功能,跳转后可能无法继续任务!!)
- **进度统计** 查看游戏统计

## 使用

1. 运行 `ArkParkEditor.exe`
2. 首次运行确认游戏目录和存档目录，程序自动建立 EXE 同级 `data` 文件夹。

更新版本时请保留原版本的 `data` 文件夹，其中包含设置和备份。游戏运行期间修改器只读。

## 编译 EXE

在 Windows 64 位系统上编译，准备以下环境：

- Python 3.12（64 位），确保终端可运行 `python`。
- Node.js 22.12 或更高版本，包含 npm，确保终端可运行 `npm.cmd`。
- 完整项目源码及可下载 Python、npm 依赖的网络环境。

在项目根目录打开 PowerShell，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

脚本会自动创建 `.venv`、安装 Python 和前端依赖、检查 TypeScript、构建网页、运行后端测试，再通过 PyInstaller 生成内置网页的独立 EXE，并打包发布文件。任一步骤失败时停止构建。

版本号读取自 `frontend/package.json`。输出目录为 `release/v<版本号>/`，例如当前版本的 `release/v0.5.4/`，包含：

- `ArkParkEditor.exe`：可直接运行，无需在使用设备上安装 Python 或 Node.js。
- `ArkParkEditor-portable.zip`：EXE 和使用文档。
- `ArkParkEditor-source.zip`：项目源码。
- `release-manifest.json`：发布文件大小及 SHA-256 校验值。
- `使用说明.md` 和 `docs/`：使用说明及相关文档。

构建中间文件保存在 `build/`，前端构建结果保存在 `frontend/dist/`。发布包不包含玩家存档、设置数据库和备份；程序首次运行时才建立 EXE 同级的 `data` 文件夹。
