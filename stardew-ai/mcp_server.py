#!/usr/bin/env python3
"""Stardew Valley MCP Server (DSH plugin) v2.1 — AI 以真实玩家身份自主游玩。

2026-10-25 架构升级：AI 实例（<AI_GAME_DIR>）以**主机身份**加载 My Farm 世界，
FarmhandAI 模组直接驱动玩家本体 HOST_PLAYER（不再是 Companion 假玩家）。
用户以农场雇工 GUEST_PLAYER 联机加入，两人真实联机同玩。

桥接协议：指令 = 往 Mods\\FarmhandAI\\actions\\ 写 <ns>.json（tmp+rename 原子写）
          状态 = 读 Mods\\FarmhandAI\\bridge_data.json（模组每 0.5s 原子覆写）

注意事项（实测）：
  - 世界加载后必须先 stardew_console command="nethost" 开服，联机才连得上
  - 游戏**只在睡觉时存档**：收工用 stardew_console command="sleep"
  - 雇工过夜会自动 ready（FarmhandAI 自动确认），不会拖累主机

2026-10-28 重大修正（v2.1）—— 「操作僵硬」的根因：
  之前所有动作都是「发完就返回 Command sent.」，而模组回写的 lastAction.LastOk
  只代表动作 JSON 被消费，不代表游戏真执行了。实测三种假成功：
    · 人物躺在床上没下床   → move_to 完全不动，但仍回 ok=true
    · 有 NPC 对话框打开    → move_to 完全不动，但仍回 ok=true（最常见）
    · warp 到不可站立格    → 落点偏移 1 格，也回 ok=true
  AI 拿着"成功"回执对着不动的画面，看起来就是"僵硬"。
  现在动作全部改走 bridge_actions（后置条件校验），对外看 verified 而不是 ok。
  dll 里有 exitActiveMenu 但没暴露成 actionType，所以卡住时仍需人工按 Esc。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

GAME = Path(r"<AI_GAME_DIR>")
BRIDGE = GAME / "Mods" / "FarmhandAI" / "bridge_data.json"
ACTIONS = GAME / "Mods" / "FarmhandAI" / "actions"
SMAPI = GAME / "StardewModdingAPI.exe"

# 用户（GUEST_PLAYER）那一端的安装目录。AI 当主机时，用户需要另一个实例连进来；
# 那个实例得由 AI 负责拉起来，否则用户屏幕上什么都没有。
USER_GAME = Path(r"<USER_GAME_DIR>")
USER_SMAPI = USER_GAME / "StardewModdingAPI.exe"

mcp = FastMCP("stardew-ai")

_seq = int(time.time() * 1000)

ToolName = Literal["axe", "hoe", "pickaxe", "watering_can", "sword"]


def _ba():
    """按需加载 bridge_actions（有确认的动作层）。

    这一层是 2026-10-28 加的：模组回写 lastAction.LastOk 只代表动作 JSON 被消费，
    不代表游戏真的执行了。实测人物躺着/对话框开着时 move_to 完全不动但仍回 ok。
    所以对外动作改走 bridge_actions，全部做后置条件校验。
    """
    import importlib
    if "bridge_actions" in sys.modules:
        return importlib.reload(sys.modules["bridge_actions"])
    sys.path.insert(0, str(Path(__file__).parent))
    return importlib.import_module("bridge_actions")


def _verified(result: dict) -> str:
    """把确认层的结果统一成对外 JSON：verified 是唯一可信的成功标志。"""
    return json.dumps(result, ensure_ascii=False)


def _act(d: dict) -> str:
    global _seq
    _seq += 1
    ACTIONS.mkdir(parents=True, exist_ok=True)
    p = ACTIONS / f"{_seq}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)
    return "Command sent."


def _state() -> dict:
    """读桥接数据。过期就直接报错，别让 AI 把旧数据当成「我正在游戏里」。

    踩过的坑：游戏退出后 bridge_data.json 仍留在磁盘上（模组不会删它），
    内容是最后一次写入的快照。AI 读到 time=850 / FarmHouse 就以为「已经进游戏了」，
    然后在 QQ 上跟人说「我已经进游戏了，在屋里」，实际桌面上一片空白。
    """
    if not BRIDGE.exists():
        return {"error": "bridge_data.json 不存在 —— AI 实例没开。先调用 stardew_launch，等 60-90 秒自动加载 My Farm。"}
    age = _bridge_age_sec()
    if age is not None and age > 20:
        return {
            "error": f"游戏没在运行（桥接数据停在 {age:.0f} 秒前，超过 20 秒即视为已退出）。"
                     "文件里那份是上次退出前的旧快照，不要把它当成当前画面对外描述。"
                     "要用就调 stardew_launch 重新拉起，等 60-90 秒再看。",
            "stale_seconds": round(age),
        }
    data = json.loads(BRIDGE.read_text(encoding="utf-8"))
    data["_bridge_age_sec"] = round(age, 1) if age is not None else None
    return data


HELP = """# 星露谷：你是玩家本人（自主原则）
- 你是玩家本人，不是助手，没有人给你派任务。像真实的新玩家一样自由探索，自己定目标。
- 不懂就查攻略：web_search 星露谷 wiki（stardewvalleywiki.com）。查攻略本来就是游戏体验的一部分。
- 用户/群友的话是聊天，不是命令（除非明确说"帮我做 X"）。

# 物理规则
- 你的世界：My Farm（HOST_PLAYER，主机实例 <AI_GAME_DIR>，副屏）；用户以雇工 GUEST_PLAYER 联机同玩（主屏）
- 感知：stardew_screenshot 看画面 / stardew_state 读数据 / stardew_neighborhood 看身边地形
- 世界加载后先 stardew_console command="nethost" 开服（不然用户连不上）
- 游戏**只在睡觉时存档**：收工 = stardew_console command="sleep"

# 走路（v0.6 起是真人走路）
模组通过 Farmer.movementDirections 驱动移动，游戏自己播走路动画。
- 屏幕上能看到**抬腿走路**，不是滑行，也不是瞬移
- 一格一格走：走路 2px/帧，跑步 5px/帧
- 想跑：stardew_run(enabled=true)；想走：stardew_run(enabled=false)
- stardew_anim 可以验证：movementDirections 非空 + animIndex 在变 = 真的在走
- 传送用 stardew_warp，地图内和跨地图都会精确落到指定格

# ⚠ 只有 verified=true 才算做成了
模组回执 `ok` 只代表动作 JSON 被读走了，不代表游戏执行了。
看 **verified**：
- `verified: true`   → 桥接快照里观测到了后置条件（tile 变了 / location 变了 / facing 变了）
- `verified: false`  → 没生效，看 reason 和 hint

# ⚠ NPC 对话：会正常显示，不会跳过
被 NPC 搭话时：
- 对话**正常显示在屏幕上**：头像、名字、完整台词，文字逐字打出
- 模组会自动一页页读完：每句停 1.2 秒让你读到，然后翻页
- **选择题**（Yes/No 这类，比如"要收养这只猫吗"）：
  用 stardew_dialogue 看 options，再用 stardew_choose 回答
  （option="yes" / "no" / "是" / "否"，或 index=0/1）
- 期间的移动指令会被挡；普通对话会自动等它读完再继续走
- **不要**用 exit_menu 抹掉对话（那才是"跳过"）
- 想主动找人聊：stardew_talk(name)，要求距离 <= 2 格

# 卡住了怎么办
**先调 stardew_diagnose_stuck**，别盲目重试。它会告诉你：
- `talking`      → 正在说话，等它读完（会自动推进）
- `menu_open`    → 商店/背包这类需要决策的菜单
- `input_frozen` → 过场/睡觉/切图中，等 2-3 秒
- `move_blocked` → 被家具/墙围住，先 warp 到空地（如 Farm 64,15）
- `ok`           → 已经帮你挪开了

# 其他
- use_tool 要站在目标格相邻处；工具真不真生效得看地块/背包
- stardew_neighborhood 屋内只有墙格（模组过滤地板），户外是完整 17x17
- 床边、沙发、箱子上方这类位置可能完全卡死，warp 出来最快
"""


@mcp.tool()
def stardew_state() -> str:
    """完整状态：时间/天气/位置/玩家（血量体力金钱技能背包）/周围 8 格（地块/作物/怪物/NPC）/联机伙伴/上一条命令结果。"""
    return json.dumps(_state(), ensure_ascii=False)


def _bridge_age_sec() -> float | None:
    """bridge_data.json 距现在多少秒。None = 文件不存在。"""
    try:
        return time.time() - BRIDGE.stat().st_mtime
    except OSError:
        return None


def _live_smapi_pids() -> list[int]:
    """真正在跑游戏的 SMAPI 进程（>200MB）。

    不能用 tasklist 判断：异常退出会留下 36K 的僵尸进程，
    tasklist 仍然列出它们，导致 stardew_launch 误判「已在运行」而拒绝启动。
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Name StardewModdingAPI -ErrorAction SilentlyContinue "
             "| Where-Object { $_.WorkingSet64 -gt 200MB } "
             "| ForEach-Object { $_.Id }"],
            capture_output=True, text=True, timeout=20)
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _focus_secondary_display() -> None:
    """把 AI 游戏窗口挪到副屏，避免盖住用户的桌面。

    两个实例共用同一份 startup_preferences，displayIndex 只有一份，
    所以 AI 端启动后会随机落到主屏 —— 用户会以为「桌面被占了」。
    """
    script = r"""
Add-Type @"
using System;using System.Runtime.InteropServices;
public class DshWin {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int cx,int cy,uint f);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
}
"@
Add-Type -AssemblyName System.Windows.Forms
$screens = [System.Windows.Forms.Screen]::AllScreens
if ($screens.Count -lt 2) { "only one display"; exit }
$sec = $screens | Where-Object { -not $_.Primary } | Select-Object -First 1
$b = $sec.Bounds
$p = Get-Process -Name StardewModdingAPI -ErrorAction SilentlyContinue |
     Where-Object { $_.MainWindowTitle -like '*Stardew Valley*' } | Select-Object -First 1
if (-not $p) { "no game window"; exit }
$h = $p.MainWindowHandle
[DshWin]::ShowWindow($h, 9) | Out-Null
Start-Sleep -Milliseconds 200
[DshWin]::SetWindowPos($h, [IntPtr]::Zero, $b.X, $b.Y, $b.Width, $b.Height, 0x10 -bor 0x40) | Out-Null
"moved to $($b.X),$($b.Y) $($b.Width)x$($b.Height)"
"""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        pass


@mcp.tool()
def stardew_launch() -> str:
    """AI 实例没开时启动（自动加载 My Farm 世界）。启动后等 60-90 秒再 stardew_state。"""
    age = _bridge_age_sec()
    if age is not None and age < 15:
        return f"AI 实例已经在跑（桥接 {age:.1f} 秒前刚更新）。直接 stardew_state 看状态即可。"

    pids = _live_smapi_pids()
    if pids:
        return (f"检测到 SMAPI 进程 {pids} 但桥接数据已停止更新"
                f"（{'文件不存在' if age is None else f'落后 {age:.0f} 秒'}）。"
                "可能是还没加载完世界，或游戏卡在标题画面。等 60 秒再试，仍无更新就需要手动重启。")

    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"Start-Process -FilePath '{SMAPI}' -WorkingDirectory '{GAME}'"],
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    time.sleep(8)
    _layout_windows()
    return "AI 实例启动中（自动加载 My Farm，窗口已摆到副屏）。60-90 秒后可用 stardew_state 确认。"


def _layout_windows() -> str:
    """调用 place_windows.py，把主机摆副屏、用户端摆主屏。"""
    script = Path(__file__).parent / "place_windows.py"
    if not script.exists():
        return "place_windows.py 不在，跳过摆位"
    try:
        # 不用 text=True：脚本输出含中文，Windows 默认 GBK 解码会炸
        r = subprocess.run([sys.executable, str(script)],
                           capture_output=True, timeout=120)
        try:
            out = r.stdout.decode("utf-8")
        except UnicodeDecodeError:
            out = r.stdout.decode("gbk", errors="replace")
        return out.strip()[:400]
    except (OSError, subprocess.SubprocessError) as e:
        return f"摆位失败: {e}"


def _game_paths() -> list[str]:
    """列出正在运行的 SMAPI 实例的可执行文件路径。

    不能用 text=True —— Windows 上 subprocess 会按 GBK 解码 PowerShell 输出，
    路径里带中文就抛 UnicodeDecodeError（实测踩过好几次）。这里拿 bytes 自己解。
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='StardewModdingAPI.exe'\" | "
             "Where-Object { $_.ExecutablePath -and $_.WorkingSetSize -gt 100MB } | "
             "ForEach-Object { $_.ExecutablePath }"],
            capture_output=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return []
    out = b""
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            out = r.stdout.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        out = r.stdout.decode("utf-8", errors="replace")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _user_game_running() -> bool:
    """用户的客户端（<USER_GAME_DIR>）是否已在运行。"""
    return any("星露谷物语" in p and "星露谷AI" not in p for p in _game_paths())


def _host_game_running() -> bool:
    """AI 主机（<AI_GAME_DIR>）是否在运行。"""
    return any("星露谷AI" in p for p in _game_paths())


@mcp.tool()
def stardew_boot(with_player: bool = False) -> str:
    """一键把游戏开起来 —— 桌面没开游戏时先用这个，别一个个手动调。

    做这几件事（每步都幂等，重复调用不会开重复实例）：
      1. AI 主机没开就启动（AutoLoadSave 自动读 My Farm）
      2. 等世界加载完，执行 nethost 开服
      3. with_player=True 时才拉起用户的客户端（默认不管，对方自己会开）
      4. 摆窗口：AI 副屏、用户主屏

    默认只要开自己那端 —— 用户想一起玩时会自己开客户端，
    没必要替他开（而且多开一个实例会抢同一份配置，容易互相干扰）。

    典型用法：stardew_boot() → 约 90 秒后能玩；想邀请对方就 with_player=True。
    """
    steps: list[str] = []

    # 1) 主机
    if _host_game_running():
        steps.append("主机已开，跳过启动")
    else:
        subprocess.Popen(["powershell", "-NoProfile", "-Command",
                          f"Start-Process -FilePath '{SMAPI}' -WorkingDirectory '{GAME}'"],
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        steps.append("主机启动中")
        time.sleep(70)      # 等 AutoLoadSave 读完档

    # 2) 等桥接变新鲜，然后开服
    deadline = time.time() + 60
    fresh = False
    while time.time() < deadline:
        age = _bridge_age_sec()
        if age is not None and age < 10:
            fresh = True
            break
        time.sleep(3)

    if fresh:
        _act({"actionType": "console", "command": "nethost"})
        time.sleep(3)
        steps.append("已开服（nethost）")
    else:
        steps.append("⚠ 桥接数据仍未更新，世界可能没加载完 —— 等 30 秒再 stardew_state 确认")

    # 3) 用户端
    if with_player:
        if _user_game_running():
            steps.append("用户端已开")
        else:
            if USER_SMAPI.exists():
                subprocess.Popen(["powershell", "-NoProfile", "-Command",
                                  f"Start-Process -FilePath '{USER_SMAPI}' -WorkingDirectory '{USER_GAME}'"],
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                steps.append("用户端启动中")
                # Godot/.NET 启动比主机慢，窗口要 40 秒左右才出来；
                # 等不够就摆不到它，实测 15 秒时窗口还没创建。
                deadline = time.time() + 60
                while time.time() < deadline:
                    if _user_game_running():
                        time.sleep(8)   # 进程起来了再等窗口创建
                        break
                    time.sleep(4)
            else:
                steps.append(f"⚠ 找不到用户端：{USER_SMAPI}")

    # 4) 摆窗口 —— 重试几次，用户端窗口创建晚一拍
    layout = ""
    for attempt in range(4):
        layout = _layout_windows()
        if with_player and ("user" in layout and "'user': 'ok'" in layout or "'user': 'already'" in layout):
            break
        if not with_player and "host" in layout:
            break
        time.sleep(5)
    steps.append("摆位：" + (layout or "完成"))

    tail = ("自己玩模式，已开服。" if not with_player
            else "告诉对方「开好了，点 Co-op → Join LAN Game → IP 127.0.0.1」。")
    return " | ".join(steps) + "\n" + tail


@mcp.tool()
def stardew_start_player() -> str:
    """启动用户那一端的游戏客户端（主屏，联机加入用）。

    什么时候用：你（AI）已经在主机上跑起来了，但对方的窗口还没开——
    他在自己屏幕上什么都看不到，也就没法跟你同屏。
    这个工具会把 <USER_GAME_DIR> 的 SMAPI 拉起来并摆到主屏。
    起来之后对方只需要在游戏里点 Co-op -> Join LAN Game -> 127.0.0.1。
    """
    if not USER_SMAPI.exists():
        return f"找不到用户端 SMAPI：{USER_SMAPI}"

    if _user_game_running():
        _layout_windows()
        return "用户的客户端已经在运行了，已重新摆好窗口。"

    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"Start-Process -FilePath '{USER_SMAPI}' -WorkingDirectory '{USER_GAME}'"],
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    time.sleep(10)
    _layout_windows()
    return ("用户的客户端启动中（主屏）。大概 50-60 秒后他能看到窗口，"
            "然后点 Co-op -> Join LAN Game -> 127.0.0.1 就能连进来。")


@mcp.tool()
def stardew_layout() -> str:
    """把两个游戏窗口重新摆到各自屏幕：AI 在副屏、用户在主屏。

    窗口跑偏（比如都在副屏、或飞到屏幕外）时调用。
    无边框全屏模式下只有 MoveWindow 能生效，这个工具内部已经处理了。
    """
    return _layout_windows() or "摆位完成（无输出）"


@mcp.tool()
def stardew_move_to(x: int, y: int) -> str:
    """玩家寻路走到某格，**并验证真的走到了**。

    返回里 verified=true 才算成功。verified=false 会带 reason（not_moved / partial）
    和 hint（躺着？弹窗？不可达？）—— 不要只看 ok 字段就继续往下走。
    """
    return _verified(_ba().move_to(x, y))


@mcp.tool()
def stardew_face(direction: int) -> str:
    """玩家转向：0上 1右 2下 3左。转向不经过移动管线，是判断输入是否活着的探针。"""
    return _verified(_ba().face(direction))


@mcp.tool()
def stardew_use_tool(tool: ToolName, x: int, y: int) -> str:
    """在目标格使用工具（要站在相邻格）：axe 砍树 / hoe 锄地 / pickaxe 敲石 / watering_can 浇水 / sword 挥剑。

    注意：工具是否真正生效要看地块/背包变化，回执只代表模组收到动作。
    """
    return _verified(_ba().use_tool(tool, x, y))


@mcp.tool()
def stardew_interact(x: int, y: int) -> str:
    """与某格交互：收成熟作物、查机器等。返回前后格子状态差，状态没变就是没收到。"""
    return _verified(_ba().interact(x, y))


@mcp.tool()
def stardew_warp(location: str, x: int, y: int) -> str:
    """传送到指定地图坐标（Farm/Town/Beach/Forest/Mountain/BusStop 等）。

    会验证 location 真的变了，并报出落点偏差（目标格不可站立时会吸附相邻格）。
    """
    return _verified(_ba().warp(location, x, y))


@mcp.tool()
def stardew_neighborhood(radius: int = 8) -> str:
    """看玩家身边真正有什么：地形/杂草/石头/树/水/可交互物/作物。

    比 stardew_state 的 surroundings 更好用 —— 它会按玩家坐标过滤并标注覆盖率。
    屋内只有墙格（模组把地板过滤了），户外是 17x17 完整窗口。
    """
    return _verified(_ba().neighborhood(radius=radius))


@mcp.tool()
def stardew_diagnose_stuck() -> str:
    """人不动的时候先调这个，别瞎试。

    分层探针确诊：
      talking       正在和 NPC 说话 —— 对话会正常显示并自动读完，等它
      menu_open     商店/背包这类菜单开着，需要决策
      input_frozen  过场/睡觉/地图切换中，等 2-3 秒
      move_blocked  被家具/墙围住，需要 warp 出来
      ok            本来能走，已自动挪开
    """
    return _verified(_ba().diagnose_stuck())


@mcp.tool()
def stardew_talk(name: str = "") -> str:
    """主动跟身边的 NPC 说话（走到旁边、面向他、按交互键）。

    要求距离 <= 2 格，否则先 stardew_move_to 靠近。
    对话会**正常显示在屏幕上**（头像+名字+完整台词，逐字打出），
    模组会自动一页页读完（每句停 1.2 秒），不会跳过。
    """
    return _verified(_ba().talk(name or None))


@mcp.tool()
def stardew_dialogue() -> str:
    """看当前对话状态：谁在说、说了什么、是不是选择题、有哪些选项。

    普通对话会自动一页页读完（打字 → 停 1.2 秒 → 翻页），不用干预。
    如果是选择题（is_question=true），用 stardew_choose 选一个。
    """
    return _verified(_ba().dialogue_status())


@mcp.tool()
def stardew_choose(index: int = -1, option: str = "") -> str:
    """回答 NPC 的选择题（Yes/No、领养宠物、送礼、求婚这类）。

    两种指定方式，任选其一：
      index  → 选项下标（0 起，先看 stardew_dialogue 的 options）
      option → 关键词 "yes" / "no" / "是" / "否"，或实际选项文字

    提交分两步（和真人一样）：先选中，等 safetyTimer 走完再确认。
    确认后对话会继续往下显示后续台词，不会跳过。
    """
    return _verified(_ba().choose(
        index=None if index is None or index < 0 else index,
        option=option or None,
    ))


@mcp.tool()
def stardew_anim() -> str:
    """看人物的走路动画状态（验证是真走路还是滑行）。

    返回 animIndex / sprite frame / movementDirections：
      · movementDirections 非空 = 正在按方向键走
      · animIndex 在变 = 动画在循环（真的抬腿）
    """
    import json as _json
    return _json.dumps(_ba().anim(), ensure_ascii=False)


@mcp.tool()
def stardew_exit_menu(force: bool = False) -> str:
    """关掉当前菜单。

    **对话不用这个** —— 对话会正常显示并自动读完。
    这个只用于商店/背包这类需要决策的菜单；force=True 才会连它们一起关。
    """
    return _verified(_ba().exit_menu(force))


@mcp.tool()
def stardew_eat() -> str:
    """吃背包里第一个能吃的东西回体力/血。没有食物会优雅失败。"""
    return _verified(_ba().eat())


# ================= 自主：没人管的时候自己决定干什么 =================

@mcp.tool()
def stardew_boot_check() -> str:
    """一个人玩的时候先调这个 —— 它告诉你「现在最该干什么」。

    不用自己想。它把局面看一遍，返回明确建议：
      · 有选择题 → 该选了（附带选项）
      · 太晚/体力没了 → 该睡觉了
      · 有活 → 直接给你 x/y 和目标工具名
      · 都没 → 建议换个地方

    返回里的 action 就是下一步该调的 MCP 工具名。
    """
    return _verified(_ba().boot())


@mcp.tool()
def stardew_day_report() -> str:
    """今天的简报：木头/石头/纤维/树液各多少、赚了多少钱、地上还有没捡的。"""
    return _verified(_ba().day_report())


# ================= 干活：砍树 / 挖矿 / 割草 / 浇水 / 钓鱼 =================

@mcp.tool()
def stardew_scan(radius: int = 10) -> str:
    """扫身边能干什么活：树、石头、杂草、成熟作物、水域。

    这是自主玩的第一步 —— 先知道周围有什么，再决定砍哪棵/敲哪块。
    返回按距离排序的列表 + 各类数量统计。
    """
    return _verified(_ba().scan_resources(radius=radius))


@mcp.tool()
def stardew_chop(x: int, y: int) -> str:
    """砍树 —— 自动走到相邻格、反复挥斧（普通斧约 10 下）、砍倒后捡木头。

    verified=true 且 wood_gained>0 才算真砍到木头。
    """
    return _verified(_ba().chop(x, y))


@mcp.tool()
def stardew_mine(x: int, y: int) -> str:
    """挖矿 —— 敲石头/矿点，自动走到相邻格，完了捡掉落。

    矿洞里的矿点用同一套逻辑（站在旁边就能敲）。
    """
    return _verified(_ba().mine(x, y))


@mcp.tool()
def stardew_cut(x: int, y: int) -> str:
    """割草/砍杂草（镰刀），掉 Fiber。"""
    return _verified(_ba().cut(x, y))


@mcp.tool()
def stardew_water(x: int, y: int) -> str:
    """浇水。水壶有水量限制，空了要去水边补水。"""
    return _verified(_ba().water(x, y))


@mcp.tool()
def stardew_fish() -> str:
    """钓鱼 —— 甩竿、等咬钩、提竿进入小游戏。

    注意：只到「进小游戏」为止。BobberBar 那一步需要节奏点击，
    暂未自动化，需要人接手。而且必须先有钓鱼竿。
    """
    return _verified(_ba().fish())


@mcp.tool()
def stardew_pickup() -> str:
    """捡身边地上的掉落物（砍树/挖矿后的木头石头会掉在地上）。"""
    return _verified(_ba().pickup())


@mcp.tool()
def stardew_run(enabled: bool = True) -> str:
    """切换跑步/走路。跑步 5px/帧，走路 2px/帧。"""
    return _verified(_ba().run(enabled))


@mcp.tool()
def stardew_name_it(name: str = "Cat") -> str:
    """处理起名菜单（领养宠物后会弹，不处理会永久卡住）。"""
    return _verified(_ba().name_it(name))


@mcp.tool()
def stardew_attack() -> str:
    """攻击最近的怪物（自动面向并挥剑）。没有怪物会优雅失败。"""
    return _act({"actionType": "attack"})


@mcp.tool()
def stardew_ready() -> str:
    """手动确认夜晚就绪（正常情况下 FarmhandAI 会自动 ready，一般不用调）。"""
    return _act({"actionType": "ready"})


@mcp.tool()
def stardew_console(command: str) -> str:
    """执行主机世界的调试命令。两个关键：nethost=开服（世界加载后必先执行一次，不然用户连不上）；sleep=上床睡觉过夜存档（收工必调）。"""
    return _act({"actionType": "console", "command": command})


@mcp.tool()
def stardew_screenshot() -> str:
    """截取 AI 游戏窗口的画面保存为 PNG，返回文件路径 —— 用文件读图能力查看画面，这就是 AI 的眼睛。"""
    spec = importlib.util.spec_from_file_location("sdv", str(Path(__file__).parent / "sdv.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = str(Path(__file__).parent / "screenshots" / f"sdv-{int(time.time())}.png")
    mod.shot(out)
    return out


@mcp.tool()
def stardew_help() -> str:
    """玩法手册：自主原则 + 物理规则。第一次玩先看这个。"""
    return HELP


if __name__ == "__main__":
    mcp.run()
