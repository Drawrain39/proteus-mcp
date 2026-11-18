# proteus-mcp

[English](README.en.md) · 简体中文

使AI Agent/Harness 能够直接操控Proteus，提升AI对于proteus的工作效率

基于proteus8的MCP服务，暂时先不做proteus9相关（其实是因为学校数电实验的proteus就是proteus8的）

做这个的初衷是因为数电实验，曾试过让Agent一边识图一遍操控电脑的效率非常慢，还要各种反复确认，非常的费token费时间，所以我想搞个mcp出来，也许能够加快Agent的工作效率，更快的帮忙完成设计与调试，目前还在开发中

## 本机接入

当前目标是 Windows 上的 Proteus **8.9 SP2**。MCP 提供电路规格校验、布局规划、模拟执行，以及供 Codex 按截图逐步操作 Proteus 的工具。首个场景是 9V 电池、1kΩ 电阻和红色 LED 串联，LED 阴极与电池负极共地。

从项目根目录运行：

```powershell
uv sync --locked --python 3.13
uv run --no-sync proteus-mcp doctor
uv run --no-sync proteus-mcp validate --spec examples/led_dc.json
uv run --no-sync proteus-mcp dry-run --spec examples/led_dc.json
uv run --no-sync proteus-mcp run --spec examples/led_dc.json --backend mock
```

`mock` 会保存计划和执行记录，真实仿真状态保持 `UNVERIFIED`。真实运行通过 Codex 调用 `circuit_run` 启动，再依据 Proteus 的窗口截图执行计划。当前命令行的 `--backend windows` 负责检查条件和分配输出目录，完整绘图需要 MCP 客户端编排。

- [安装与注册](docs/INSTALL_WINDOWS.md)
- [中文操作指南](docs/USAGE_zh.md)
- [架构与结果状态](docs/ARCHITECTURE.md)
- [本机验收报告](reports/acceptance.md)
- [可编辑 LED 示例工程](examples/led_dc_minimal.pdsprj)
