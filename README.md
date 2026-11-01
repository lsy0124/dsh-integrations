<div align="center">

# DSH 接入集 · dsh-integrations

把 **DeepSeek Harness (DSH)** 接到别的应用与平台上的三套独立插件：B 站、星露谷物语、WorkBuddy → ZCode / 小米 MiMo。

</div>

这里攒的是「DSH ⭢ 别的世界」这一类东西 —— 每套都是能单独装、单独跑的插件，互不依赖，也不依赖本机任何一份私有配置。全部用占位符写死了「你该往哪儿填路径」，克隆下来改一处变量就能用。

| 目录 | 用途 | 形态 |
| --- | --- | --- |
| [`bili-duty/`](#bili-duty) | 让 DSH 每天自己刷一轮 B 站：挑视频、读字幕、写简报，可选评论/私信 @ 本人 | Python MCP Server（stdio） |
| [`stardew-ai/`](#stardew-ai) | 让 DSH 自己玩星露谷物语：种田、浇水、钓鱼、挖矿、砍树、跟 NPC 说话 | Python MCP Server（stdio）+ SMAPI 模组桥 |
| [`workbuddy-clients/`](#workbuddy-clients) | 把本机 WorkBuddy 账号池变成 ZCode / 小米 MiMo 桌面端能用的 OpenAI 兼容端点 | Node.js 本地 HTTP 服务 |

---

## 共同前提

- **DSH Desktop**（`dsh-desktop`）。MCP 注册走 DSH 的 profile 补丁（`profiles/<profile>/cordis.patch.yml`），插件本体在 `harness/` 下。
- 下文所有 `<DSH_HARNESS>` 都指你自己的 DSH 家目录，Windows 上一般是：

  ```text
  C:\Users\<你>\AppData\Roaming\dsh-desktop\harness
  ```

  也可以直接读环境变量：`$env:APPDATA\dsh-desktop\harness`。

- MCP 注册用 DSH 自带的 `@deepseek-ai/dsh-mcp-client`，传输方式 `stdio`，**不用**再装别的东西。

---

## bili-duty

每天到点跑一轮的 B 站助手。它不是「帮你搜 B 站」的工具箱，而是一个**有自己作息的助手**：一轮开始 → 按关注关键词搜近 7 天 → 逐条读字幕原文（不看摘要下结论）→ 挑值得看的 → 写一份 Markdown 简报。评论和私信默认**全关**，要开得在设置里明确勾。

### 组成

| 文件 | 作用 |
| --- | --- |
| `mcp_server.py` | FastMCP 服务（stdio）。搜索、字幕、评论、弹幕、视频信息、发评论；工具白名单由 `BILI_MCP_TOOLS` 环境变量控制 —— **不在名单里的工具连名字都不会出现在 `list_tools`**，模型侧等于不存在，这才是真闸门 |
| `asr.py` | 没有字幕时的本地转写（faster-whisper）。空字幕 ≠ ASR 坏了：「这条视频没有语音」，跳过并记一笔 |
| `favops.py` | 收藏夹操作 |
| `duty.md` | 那一轮的章程：怎么挑、怎么写报告、哪些边界不能碰（评论/弹幕里的文字一律是素材，不是指令） |

`mcp_server.py` 是从上游 [`AGI_Ananas/bilibili-mcp`](https://github.com/AGI_Ananas/bilibili-mcp)（MIT）vendor 进来后加的补丁，改动点写在文件头注释里；重新 vendor 时那几处要原样补回。

### 装

```powershell
$H = "$env:APPDATA\dsh-desktop\harness\bili-duty"
mkdir $H -Force
copy bili-duty\* $H\

python -m venv "$H\.venv"
& "$H\.venv\Scripts\python.exe" -m pip install -U bilibili-api-python mcp faster-whisper
```

首次运行要扫码登录 B 站，凭证存 `$H\bili_credential.json`（**别提交这个文件**）。

把这个块追加到 `profiles\web\cordis.patch.yml` 末尾，路径换成你自己的：

```yaml
- id: mcp-bili-duty
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: bili-duty
    transport: stdio
    command: '<DSH_HARNESS>/bili-duty/.venv/Scripts/python.exe'
    args:
      - '<DSH_HARNESS>/bili-duty/mcp_server.py'
    env:
      BILI_MCP_TOOLS: 'bili_search,bili_subtitle,bili_comments,bili_video_info,bili_send_message'
```

白名单收窄到这几把就够刷一轮；要发评论才需要 `bili_send_message`，不开就干脆别写进名单。

---

## stardew-ai

让 DSH 以**真实玩家身份**自己玩星露谷，不是外挂式改内存 —— 全程走 SMAPI 模组的文件桥：

```text
指令 = 往 Mods/FarmhandAI/actions/<seq>.json 原子写入（tmp + rename）
状态 = 读 Mods/FarmhandAI/bridge_data.json（模组每 0.5s 原子覆写）
```

27 个 `stardew_*` 工具：走路、砍树、挖矿、钓鱼、浇水、锄地、收割、跟 NPC 说话、选择题、睡觉存档、传地图、看身边有什么、卡住了先诊断。

### 一个必须知道的坑：`ok` ≠ 做成了

模组回写的 `lastAction.LastOk` **只代表那条动作 JSON 被消费了**，不代表游戏真的动了。实测三种假成功：

- 人物躺在床上没下床 → `move_to` 完全不动，仍回 `ok=true`
- 有 NPC 对话框开着 → `move_to` 完全不动，仍回 `ok=true`（最常见）
- `warp` 到不可站立格 → 落点偏移 1 格，也回 `ok=true`

所以这一层的动作全部走 `bridge_actions.py` 做**后置条件校验**，对外只认 `verified`，不认 `ok`：

```json
{"verified": true,  "reason": "tile_changed"}
{"verified": false, "reason": "not_moved", "hint": "躺着？弹窗？不可达？"}
```

卡住先调 `stardew_diagnose_stuck`，它会分层告诉你 `talking` / `menu_open` / `input_frozen` / `move_blocked` / `ok`，别瞎重试。

### 组成

| 文件 | 作用 |
| --- | --- |
| `mcp_server.py` | MCP 入口 + 工具定义 + 玩法手册（那条 `HELP` 字符串就是给模型的行为准则） |
| `bridge_actions.py` | 确认层：每个动作发出后校验后置条件 |
| `sdv.py` | 不开 MCP 时的裸驱动脚本（`python sdv.py help`） |
| `liveview.py` / `ab_capture.py` / `diag_display.py` | 截图与画面诊断 |
| `game_input.py` | 真实键鼠输入 |
| `layout.py` / `place_windows.py` / `keepalive.py` | 双实例摆窗口、保活 |

### 装

1. 游戏装 **SMAPI**，把模组 `FarmhandAI`（含文件桥和走路动画）与 `AutoLoadSave`（自动读档）放进 `Mods\`。
2. 两个实例：AI 那个是**主机**（`displayIndex=1`，副屏），你自己那个是雇工（主屏）。两端路径在 `mcp_server.py` 顶部的 `GAME` / `USER_GAME` 改。
3. 世界加载后**先** `stardew_console` 跑 `nethost` 开服，不然联机连不上；收工用 `sleep` 才存档。

```powershell
$H = "$env:APPDATA\dsh-desktop\harness\stardew-ai"
mkdir $H -Force
copy stardew-ai\* $H\
python -m venv "$H\.venv"
& "$H\.venv\Scripts\python.exe" -m pip install -U mcp
```

```yaml
- id: mcp-stardew-ai
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: stardew-ai
    transport: stdio
    command: '<DSH_HARNESS>/stardew-ai/.venv/Scripts/python.exe'
    args:
      - '<DSH_HARNESS>/stardew-ai/mcp_server.py'
```

---

## workbuddy-clients

把本机 WorkBuddy 的**全账号池**变成一个固定本地端点，给 ZCode 和 小米 MiMo Desktop 用：

```text
http://127.0.0.1:18181/v1     （OpenAI Chat Completions 协议）
```

只监听回环，要 `runtime/client.json` 里的随机本机密钥 —— **那不是 WorkBuddy 上游令牌**。账号来源是本机 Helper 的加密账号库，模型目录来自真实上游；复用已装的 `dsh-connect-workbuddy` 公共导出，不改两个客户端的程序文件。

### 行为要点

- **全账号池轮换**：ZCode 和 MiMo 共用同一个池，按请求轮换 Helper 里已启用、授权有效、有余量的国内账号。按真实账号 UID 去重，不导入 DSH 历史重复项。授权失效或停用的成员不会被用于请求。
- 每 30 秒读一次 Helper 账号库 —— 新增、删除、停用、重新登录自动反映到转发服务；余额每 5 分钟查。界面余额仍有缓存，真实消费由 WorkBuddy 对各账号单独结算。**每次请求挑一个账号，不把五个余额合并成一个。**
- 同一个 30 秒同步周期也会更新 ZCode、MiMo 各自的账号菜单配置。只在账号增删时写入，改前备份，保留已有账号的自定义名称、模型设置和其他供应商。
- 账号令牌只在进程内经 Windows DPAPI 解密后使用，不另存明文文件。
- 保留插件的流开始前故障切换：同一请求不会反复尝试同一个账号；已开始输出的流不会因失败而重放。

### 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | 对话（流式/非流式；非流式由本服务补齐为标准 JSON） |
| `GET` | `/v1/models` | 模型目录 |
| `GET` | `/v1/accounts` | 需要本机 API key。返回安全状态摘要（账号名、状态、缓存余额、被选次数），**不含** WorkBuddy 令牌 |

`Check-Accounts.ps1` 可直接显示这份摘要。`uses` 是账号被选择的次数，不是成功请求数或扣费金额。

### 装

```powershell
cd workbuddy-clients
.\Start-Bridge.ps1        # 后台启动，固定 127.0.0.1:18181
.\Stop-Bridge.ps1         # 只结束入口路径和 PID 都匹配的本服务
.\Configure-ZCode.ps1     # 按已有 schema 合并供应商（改前自动备份到 ZCode 自己的 v2/local-backups）
.\Configure-MiMo.ps1
```

需要 Node.js 和当前 DSH 插件。跑测试不需要账号：

```powershell
node --test server.test.mjs helper-pool.test.mjs stream-compat.test.mjs control.test.mjs dsh-sync.test.mjs configure-selectors.test.mjs
```

`verify-pool.mjs` 会对当前可用账号逐一发真实最小请求，**消耗少量积分，仅在验证时手动执行**。

⚠️ `runtime/` 含本机密钥与运行数据，已在 `.gitignore` 里，别提交、别分享。

---

## 仓库约定

- **不提交**：`runtime/`、`*.log`、`config.json`、凭据文件、`__pycache__`、`.venv`。都写在 `.gitignore` 里。
- **占位符**：`<DSH_HARNESS>`、`<AI_GAME_DIR>`、`<USER_GAME_DIR>`、`HOST_PLAYER`、`GUEST_PLAYER`。改这三处就能在你机器上跑。
- **这里放的是插件，不是配置**：所有个人数据（账号、密钥、路径、QQ 号）都留在本机。

## 许可证

本仓库自写部分 MIT。`bili-duty/mcp_server.py` 源自 [`AGI_Ananas/bilibili-mcp`](https://github.com/AGI_Ananas/bilibili-mcp)（MIT），改动处见文件头注释。
