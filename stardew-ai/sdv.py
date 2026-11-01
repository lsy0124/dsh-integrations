#!/usr/bin/env python3
"""星露谷 AI 桥接驱动器 — 直接读写 SMAPI 模组的 JSON 桥接文件，不需要 MCP 客户端。

架构: 本脚本往  Mods\\StardewMCPBridge\\actions\\  写动作 JSON，
游戏内的 StardewMCPBridge 模组（AI 同伴）消费动作并把游戏状态写回 bridge_data.json。

用法:
  python sdv.py state                          全局状态（时间/天气/玩家/同伴）
  python sdv.py spawn                          生成 Companion1/2（跟随模式）
  python sdv.py follow | stay | farm | mine | fish   设置所有同伴的自主模式
  python sdv.py mode Companion1 player         切单个同伴模式（follow/farm/mine/fish/idle/player）
  python sdv.py see Companion1                 同伴视野（地块/怪物/NPC，需 player 模式）
  python sdv.py inv Companion1                 同伴背包
  python sdv.py cstate Companion1              同伴完整状态（含 lastCommand）
  python sdv.py move Companion1 66 18          寻路走到某格
  python sdv.py warp Companion1 Farm 64 16     传送到指定地点
  python sdv.py warpall Town 30 20             所有同伴传送
  python sdv.py tool Companion1 hoe 66 19      在某格用工具 (axe/hoe/pickaxe/watering_can/sword)
  python sdv.py interact Companion1 66 19      与格子交互（作物/箱子/梯子/机器）
  python sdv.py attack Companion1              攻击最近怪物
  python sdv.py cast Companion1                甩竿钓鱼（自动提钩）
  python sdv.py autocombat Companion1 on      开关自动战斗
  python sdv.py eat Companion1                 吃东西回体力
  python sdv.py face Companion1 2              转向 0上1右2下3左
  python sdv.py water_all | harvest_all        当前地图全浇水/全收获
  python sdv.py chat 你好呀                     游戏内聊天
  python sdv.py action water 66 19             自定义动作 (water/harvest/clear/hoe)
  python sdv.py console sleep                  执行游戏调试命令（如 sleep 上床睡觉存档）
  python sdv.py watch 5                        每 2 秒刷新全局状态
  python sdv.py launch                         游戏没开的话通过 SMAPI 启动（副屏+自动读档）
"""
import json
import os
import subprocess
import sys
import time

GAME = r"<USER_GAME_DIR>"
BRIDGE = os.path.join(GAME, "Mods", "StardewMCPBridge", "bridge_data.json")
ACTIONS = os.path.join(GAME, "Mods", "StardewMCPBridge", "actions")
SMAPI_EXE = os.path.join(GAME, "StardewModdingAPI.exe")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def act(d):
    os.makedirs(ACTIONS, exist_ok=True)
    p = os.path.join(ACTIONS, f"{time.time_ns()}.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.rename(p + ".tmp", p)
    print("sent:", json.dumps(d, ensure_ascii=False))


def state():
    if not os.path.exists(BRIDGE):
        return {"error": "bridge_data.json 不存在 —— 游戏没开？先跑: python sdv.py launch"}
    with open(BRIDGE, encoding="utf-8") as f:
        return json.load(f)


def comp(name):
    s = state()
    for c in s.get("companions", []):
        if c["name"] == name:
            return c
    return {"error": f"找不到同伴 {name}，现有: {[c['name'] for c in s.get('companions', [])]}"}


def out(x):
    print(json.dumps(x, ensure_ascii=False, indent=1))


def launch():
    try:
        subprocess.run(["tasklist", "/FI", f"IMAGENAME eq StardewModdingAPI.exe"],
                       capture_output=True, text=True, check=True)
        running = "StardewModdingAPI.exe" in subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq StardewModdingAPI.exe"],
            capture_output=True, text=True).stdout
    except Exception:
        running = False
    if running:
        print("游戏已在运行。")
        return
    subprocess.Popen(["powershell", "-NoProfile", "-Command",
                      f"Start-Process -FilePath '{SMAPI_EXE}' -WorkingDirectory '{GAME}'"])
    print("已通过 SMAPI 启动游戏（副屏、自动读档）。约 60-90 秒后可用 python sdv.py state 查看状态。")


def stardew_windows():
    """枚举可见的星露谷**游戏主窗口**，返回 (pid, title, rect)。

    必须过滤掉 SMAPI 控制台窗口：
      · 控制台标题是 "SMAPI 4.0.8 - running Stardew Valley 1.6.8 with 4 mods"，
        同样含 "Stardew Valley"，会被误当成游戏窗口；
      · 旧逻辑取「最靠右」的窗口做截图目标，控制台一旦比游戏窗口更靠右
        （实测主机在 2560、控制台在 3032），截到的就是一屏日志文字，
        AI 因此「看到的画面和显示器上不一样」。
    过滤依据：游戏窗口至少 800x600，控制台约 1168x624 但标题以 SMAPI 开头。
    """
    import ctypes
    from ctypes import wintypes
    # 不能设 SetProcessDPIAware：
    # 副屏是 150% 缩放，开了 DPI 感知后 GetWindowRect 返回的是被放大的坐标
    # （1920x1080 的窗口会报成 2880x1620），按那个尺寸去 GetDIBits
    # 就会在图片底部/右侧留出黑边 —— 这正是「AI 看到的画面和显示器不一致」的来源。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(0)
    except Exception:
        pass
    user32 = ctypes.windll.user32
    found = []

    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if "Stardew Valley" not in title:
            return True
        # 排除 SMAPI 控制台：标题以 "SMAPI" 开头的那种
        if title.strip().upper().startswith("SMAPI"):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        # 游戏窗口一定有实际画面尺寸；控制台/工具窗只有 26px 高
        if w < 800 or h < 600:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((pid.value, title, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def shot(out_path=None):
    """截图星露谷窗口（PrintWindow 抓窗口表面，无视遮挡与越界）。

    选窗口的顺序：
      1. 优先选可执行文件在 <AI_GAME_DIR> 下的那个（AI 自己的实例）；
      2. 找不到就退化成「面积最大的游戏窗口」。
    绝不按「最靠右」选 —— 用户的客户端一旦也在副屏，就会截到别人的画面。
    """
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    wins = stardew_windows()
    if not wins:
        print("没找到 Stardew Valley 窗口 —— 游戏没开？")
        return
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # 按窗口面积排序，最大的通常就是有画面的游戏窗口
    wins.sort(key=lambda w: (w[2][2] - w[2][0]) * (w[2][3] - w[2][1]), reverse=True)
    pid, title, rect = wins[0]

    # re-find hwnd by matching rect + pid
    hwnds = []

    def callback(h, lp):
        if not user32.IsWindowVisible(h):
            return True
        length = user32.GetWindowTextLengthW(h)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(h, buf, length + 1)
        if "Stardew Valley" not in buf.value:
            return True
        if buf.value.strip().upper().startswith("SMAPI"):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if (r.left, r.top, r.right, r.bottom) == rect and p.value == pid:
            hwnds.append(h)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    if not hwnds:
        print("窗口枚举失败")
        return
    hwnd = hwnds[0]
    w, h = rect[2] - rect[0], rect[3] - rect[1]

    hwndDC = user32.GetWindowDC(hwnd)
    mfcDC = gdi32.CreateCompatibleDC(hwndDC)
    bmp = gdi32.CreateCompatibleBitmap(hwndDC, w, h)
    gdi32.SelectObject(mfcDC, bmp)
    PW_RENDERFULLCONTENT = 0x2
    ok = user32.PrintWindow(hwnd, mfcDC, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mfcDC, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mfcDC)
    user32.ReleaseDC(hwnd, hwndDC)

    out_path = out_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "screenshots", f"sdv-{int(time.time())}.png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path)
    print(f"saved: {out_path}  (pid {pid}, PrintWindow ok={bool(ok)}, {w}x{h})")


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = a[0], a[1:]
    n = lambda i: float(rest[i]) if "." in str(rest[i]) else int(rest[i])

    if cmd == "state":
        out(state())
    elif cmd == "spawn":
        act({"actionType": "spawn"})
    elif cmd == "follow":
        act({"actionType": "follow"})
    elif cmd == "stay":
        act({"actionType": "stay"})
    elif cmd == "farm":
        act({"actionType": "farm"})
    elif cmd == "mine":
        act({"actionType": "mine"})
    elif cmd == "fish":
        act({"actionType": "fish"})
    elif cmd == "water_all":
        act({"actionType": "water_all"})
    elif cmd == "harvest_all":
        act({"actionType": "harvest_all"})
    elif cmd == "warpall":
        act({"actionType": "warp", "location": rest[0], "x": n(1), "y": n(2)})
    elif cmd == "mode":
        act({"actionType": "set_mode", "target": rest[0], "mode": rest[1]})
    elif cmd == "see":
        c = comp(rest[0])
        out({"tile": c.get("tile"), "location": c.get("location"),
             "surroundings": c.get("surroundings")})
    elif cmd == "inv":
        out(comp(rest[0]).get("inventory"))
    elif cmd == "cstate":
        out(comp(rest[0]))
    elif cmd == "move":
        act({"actionType": "move_to", "companion": rest[0], "x": n(1), "y": n(2)})
    elif cmd == "warp":
        act({"actionType": "warp_to", "companion": rest[0], "location": rest[1],
             "x": n(2), "y": n(3)})
    elif cmd == "face":
        act({"actionType": "face_direction", "companion": rest[0], "direction": n(1)})
    elif cmd == "tool":
        act({"actionType": "use_tool", "companion": rest[0], "tool": rest[1],
             "x": n(2), "y": n(3)})
    elif cmd == "interact":
        act({"actionType": "interact", "companion": rest[0], "x": n(1), "y": n(2)})
    elif cmd == "attack":
        act({"actionType": "attack", "companion": rest[0]})
    elif cmd == "cast":
        act({"actionType": "cast_fishing_rod", "companion": rest[0]})
    elif cmd == "autocombat":
        act({"actionType": "set_auto_combat", "companion": rest[0],
             "enabled": rest[1] in ("on", "true", "1")})
    elif cmd == "eat":
        d = {"actionType": "eat_item", "companion": rest[0]}
        if len(rest) > 1:
            d["slot"] = n(1)
        act(d)
    elif cmd == "chat":
        act({"actionType": "chat", "metadata": {"message": " ".join(rest)}})
    elif cmd == "action":
        d = {"actionType": rest[0]}
        if len(rest) > 1:
            d["x"], d["y"] = n(1), n(2)
        act(d)
    elif cmd == "console":
        act({"actionType": "console", "command": " ".join(rest)})
    elif cmd == "screenshot":
        shot(rest[0] if rest else None)
    elif cmd == "watch":
        times = int(rest[0]) if rest else 3
        for i in range(times):
            s = state()
            print(f"--- {i+1}/{times} game {s.get('time')} {s.get('season')} day{s.get('day')} @ {s.get('location')}")
            for c in s.get("companions", []):
                print(f"    {c['name']}: {c['mode']} {c['tile']} {c['status']}")
            if i < times - 1:
                time.sleep(2)
    elif cmd == "launch":
        launch()
    else:
        print(f"未知命令: {cmd}（python sdv.py help 查看用法）")
        sys.exit(1)


if __name__ == "__main__":
    main()
