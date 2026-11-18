# 用 Codex 规划和操作 Proteus

可在已加载 `proteus` MCP 的会话中使用这条请求：

> 用本项目的 Proteus MCP 创建 9V 电池、1kΩ 电阻和红色 LED 的串联电路，LED 阴极与电池负极共地。采用 examples/led_dc.json。检查本机引脚，保存到新建运行目录，重新打开并观察 LED 仿真。

## 一次真实运行

1. 调用 `circuit_validate`，再调用 `circuit_run`，传入规格和 `backend: "windows"`。`READY` 表示已分配本次运行的独立目录。使用返回的 `project_destination` 保存新工程。
2. 调用 `proteus_observe`，根据图像确认窗口、对话框和缩放。若 Proteus 未置于前台，先解决窗口状态。
3. 按返回的 `steps` 新建工程，从本机库选器件，逐个放置并设置属性。通过状态栏或属性对话框核实脚位。按 `wire_net` 连线，最后把参考地接到回路底部。
4. `proteus_action` 用图像宽高归一化坐标。令牌有效期为 30 秒且只能用一次。每次操作返回新图像和令牌。定位字段后用 `type`，替换文本时传 `clear: true`。用 `hover` 看连线或引脚的状态提示。
5. 保存并重新打开原生工程，确认器件值和连线仍在。用 **工具 → 编译网表** 导出同目录、同名的逻辑 SDF。启动仿真，确认 LED 可见变化和仿真消息。保存分别对应重开和运行的截图。
6. 用 `circuit_record` 提交 `pins_and_properties`、`connectivity`、`reopened`、`simulation` 四项。每项采用 `{"screenshot": "本次返回截图的文件名", "notes": "真实观察结果"}`。服务检查工程文件、截图归属和证据字段，记录哈希。暂时放弃或交回操作时调用 `proteus_release`。

例如某次截图宽 1000 像素、高 800 像素，文本字段位于截图像素 (250, 160)，那么调用 `proteus_action` 时用 `action: "type"`、`x: 0.25`、`y: 0.20`、`text: "1k"` 和该截图的 `observation_id`。每个新的窗口和对话框都重新定位。

如果原生菜单点选被窗口命中检查拒绝，先刷新截图，再用方向键定位菜单项并按 Enter。仿真结束后停止运行，再释放桌面控制。

## 运行记录

每次运行的目录位于 `artifacts/runs`。其中 `spec.json` 是请求，`plan.json` 是落实到逻辑器件和网络的步骤，`events.jsonl` 记录操作，`screen-*.png` 记录窗口图像，`result.json` 保存最终状态。真实运行要保留原生 `.pdsprj` 文件。 `RECORDED` 表示已归档代理观察。若同名 `.SDF` 存在，服务还会比对器件值与全部网络端点。

## 命令行和测试

```powershell
uv run --no-sync proteus-mcp validate --spec examples/led_dc.json
uv run --no-sync proteus-mcp dry-run --spec examples/led_dc.json
uv run --no-sync proteus-mcp run --spec examples/led_dc.json --backend mock
uv run --no-sync proteus-mcp verify-netlist --spec examples/led_dc.json --netlist examples/led_dc_minimal.sdf
uv run --no-sync proteus-mcp schema
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests scripts
```

`windows-probe` 验证子服务可启动、可初始化和可列工具。 `windows-observe` 在 Proteus 是前台窗口时保存只读截图。MCP 默认使用本地 stdio。
