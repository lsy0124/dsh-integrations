# stardew-ai — DSH 插件

AI 玩星露谷物语（不占键鼠）。通过 SMAPI 模组 `StardewMCPBridge` 的 JSON 文件桥控制游戏：
AI 同伴 Companion1/2 可以种田、浇水、钓鱼、下矿、战斗；游戏窗口在副屏全屏、启动自动读档、失焦不暂停。

## 组成

- `mcp_server.py` — FastMCP 服务器（stdio），27 个 `stardew_*` 工具（25 个操控 + launch + help）
- `.venv/` — Python 3.12 venv（mcp v1，与 bili-duty 插件同款 API）
- `sdv.py` — 文件桥备用驱动脚本（MCP 不可用时直接 `python sdv.py help`）
- `headless-overlay.yml` — MCP 注册片段（与 web/cordis.patch.yml 中一致）

## 注册方式

已在 `harness/profiles/web/cordis.patch.yml` 末尾插入 `mcp-stardew-ai`
（`@deepseek-ai/dsh-mcp-client`，stdio，命令 = 本目录 .venv 的 python.exe）。
备份：`cordis.patch.yml.bak-before-stardew-20261025`。卸载 = 删掉该 BEGIN/END 注释块 + 本目录。

## 依赖（本机，已就绪）

- SMAPI 4.0.8 + 模组 `<USER_GAME_DIR>\Mods\StardewMCPBridge`（含钓竿补丁）
- 模组 `Mods\AutoLoadSave`（自动读档）
- `startup_preferences`：displayIndex=1（副屏）、pauseWhenOutOfFocus=false
