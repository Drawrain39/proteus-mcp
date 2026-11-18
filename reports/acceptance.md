# Proteus 8.9 SP2 本机验收

2026-11-18 在本机 Windows 会话、Proteus 8.9 SP2（ISIS 8.09.02，Build 28501）完成最小 LED 电路验证。工程关闭重开后运行，D1 亮红，停止后恢复暗色。导出的逻辑 SDF 与 CircuitSpec 的器件值和三个网络一致。

| 检查 | 状态 | 证据 |
| --- | --- | --- |
| 版本和 MCP 握手 | PASS | `proteus-mcp doctor` 确认可执行文件版本 8.9.28501.0。`windows-probe` 列出 5 个允许的 Windows-MCP 工具 |
| 静态检查和测试 | PASS | `ruff check src tests scripts` 通过。`pytest -q` 有 29 项通过、1 项跳过 |
| 真实窗口读取和输入 | PASS | 实测前台窗口读取、对话框文本输入、参考地放置与接线、操作后截图 |
| 原生弹出菜单鼠标点选 | UNVERIFIED | 验收使用键盘方向键和 Enter 完成菜单选择 |
| 电气端点和属性 | PASS | 对照 [LED 规格](../examples/led_dc.json) 核对 Proteus 导出的 [逻辑网表](../examples/led_dc_minimal.sdf) |
| 保存、重开 | PASS | [可编辑项目](../examples/led_dc_minimal.pdsprj)、[重开截图](p1/led-reopened.png) |
| 真实仿真 | PASS | [运行时 D1 亮红](p1/led-running.png)，[重开未运行时 D1 暗色](p1/led-reopened.png)。仿真消息显示网表编译、链接和分区分析成功 |
| 从空白经同一 MCP 流程重复绘制 | UNVERIFIED | 真实验证从已有草稿续画，由 MCP 客户端按截图完成放置参考地、连线、另存及仿真 |
| AT89C51、Keil、HEX | BLOCKED | 验收机器未安装可用的 Keil C51，尚未验证 MCU 流程 |

测试中的符号链接用例因 Windows 当前会话不能创建符号链接而跳过。相对路径越界用例已执行。

## 核对真实网络

| 电路规格 | SDF 端点 |
| --- | --- |
| VPLUS | `BT1,+` 与 `R1,1` |
| LED_A | `R1,2` 与 `D1,A` |
| GND | `BT1,-`、`D1,K` 与 `GND,PR`，网表标为 `CLASS=POWER` |

在原理图中通过 **工具 → 编译网表 → 文件 → 逻辑的 → SDF** 导出。用下面的命令复核：

```powershell
uv run --no-sync proteus-mcp verify-netlist --spec examples/led_dc.json --netlist examples/led_dc_minimal.sdf
```

## 示例工程

发布的 [示例工程](../examples/led_dc_minimal.pdsprj) 来自验收中由 Proteus 另存的文件。文件大小为 12,028 字节，SHA-256 为 `e5ae11eeaf6a5e91dbda97fcfcc619b8ae0eb1bf09f4923cf5df8dfb5caed54a`。
