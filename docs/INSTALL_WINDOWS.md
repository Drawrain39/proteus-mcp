# 在 Windows 上安装

在运行 Proteus 的 Windows 登录会话中启动 Codex。当前验证对象是 Proteus 8.9 SP2。主项目采用 Python 3.13，[Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 0.8.9 单独使用 Python 3.14。

在项目根目录执行：

```powershell
uv sync --locked --python 3.13
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.runtime\python'
$env:UV_CACHE_DIR = Join-Path $PWD '.cache\uv'
uv python install 3.14.6
uv venv --python 3.14.6 .venv-windows
uv pip sync --python .venv-windows/Scripts/python.exe requirements-windows.lock.txt
if (-not (Test-Path -LiteralPath 'config.local.json')) {
    Copy-Item -LiteralPath 'config.example.json' -Destination 'config.local.json'
}
& .\.venv\Scripts\python.exe -m proteus_mcp doctor
& .\.venv\Scripts\python.exe -m proteus_mcp windows-probe
```

在 `config.local.json` 核对实际 `proteus_exe` 和 `windows_python`。打开 Proteus，切到它的窗口后可运行 `proteus_mcp windows-observe` 检查截图。前台窗口检查失败时，先看 `doctor` 中的 `proteus.windows` 与最小化、前台状态。

## 注册到 Codex

先运行 Codex，让它创建用户配置文件。随后在项目根目录执行注册脚本。

```powershell
& .\.venv\Scripts\python.exe scripts/register_codex.py
codex mcp list
```

脚本会先备份已有配置，再追加 `proteus` 和 `windows_gui` 两项。旧配置内容按原字节保留。脚本发现有同名异配置时会退出，便于用户保留已有服务。 `proteus` 封装带窗口检查的输入和截图。直接注册的 `windows_gui` 仅提供 `ControlStatus` 诊断，实际截图由 `proteus` 限定到前台 Proteus 区域。

在新的 Codex 会话加载注册项。MCP 输入默认关闭。通过只读截图检查后，在 `config.local.json` 设置 `"gui_enabled": true`，并重新启动该 MCP 服务。Windows-MCP 自带桌面控制许可与接管机制，桌面显示许可对话框时请在本机处理。

用 `proteus_doctor` 确认当前会话是否加载了 `api_revision: 4`。快点击默认在画布批次的每次松键后等待 125 ms。若某个环境对快点击的响应不稳定，可在 `config.local.json` 中设置 `"canvas_click_settle_ms": 500` 并重启服务，恢复原来的点击路径。`windows-probe` 显示后端实际使用的等待设置。

如要撤销注册，使用 `codex mcp remove proteus` 和 `codex mcp remove windows_gui`。若撤销前确认配置没有其他修改，也可以从注册脚本打印的备份恢复。
