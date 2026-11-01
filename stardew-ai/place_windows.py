#!/usr/bin/env python3
"""把两个星露谷窗口分别摆到主屏和副屏 —— 由 PID 精确识别，不靠路径字符串。

为什么单独抽成一个文件：
  MCP 服务（mcp_server.py）运行在 DSH 的 Python 进程里，
  直接内联一大段 PowerShell/C# 既难维护又容易踩引号；这里做成独立脚本，
  AI 通过 subprocess 调用即可，也能手动跑。

关键点（都是踩过的坑）：
  · 不能 SetProcessDPIAware。副屏是 150% 缩放，开了之后 2560 会被系统
    再放大成 3840，窗口直接飞出屏幕右边界（实测固定在 (3840,468)）。
  · 必须用 MoveWindow 而不是 SetWindowPos：全屏样式下只有它稳定生效。
  · 按 PID 判断谁是谁。用可执行文件路径判断时，两个进程可能读到同一条
    路径（中文路径编码问题），结果两个窗口都被摆到同一块屏幕。

用法:
  python place_windows.py            # 摆一次
  python place_windows.py --print    # 只打印当前布局，不动窗口
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
# 关 DPI 虚拟化，坐标才是真实像素
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(0)
except Exception:
    pass

HOST_MARK = "星露谷AI"


def _ps(cmd: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True)
    # PowerShell 输出在中文系统上默认是 GBK，显式按 UTF-8 解码再退回 GBK，
    # 否则路径里的中文会抛 UnicodeDecodeError（实测踩过）。
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            return r.stdout.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.stdout.decode("utf-8", errors="replace")


def process_map() -> dict[int, str]:
    """pid -> 可执行文件路径（只取已加载游戏本体的进程）。"""
    out = _ps(
        "Get-CimInstance Win32_Process -Filter \"Name='StardewModdingAPI.exe'\" | "
        "Where-Object { $_.ExecutablePath } | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }"
    )
    procs: dict[int, str] = {}
    for line in out.splitlines():
        if "|" in line:
            p, path = line.split("|", 1)
            try:
                procs[int(p.strip())] = path.strip()
            except ValueError:
                pass
    return procs


def screens() -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """(主屏工作区, 副屏工作区)；没有副屏时两边相同。"""
    out = _ps(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "foreach ($s in [System.Windows.Forms.Screen]::AllScreens) { "
        "\"$($s.Primary)|$($s.WorkingArea.X)|$($s.WorkingArea.Y)|"
        "$($s.WorkingArea.Width)|$($s.WorkingArea.Height)\" }"
    )
    primary = None
    secondary = None
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 5:
            continue
        is_primary = parts[0].lower() == "true"
        rect = tuple(int(x) for x in parts[1:])
        if is_primary:
            primary = rect
        elif secondary is None:
            secondary = rect
    if primary is None:
        primary = (0, 0, 1707, 1019)
    if secondary is None:
        secondary = primary
    return primary, secondary


def game_windows(procs: dict[int, str]) -> dict[str, tuple[int, int]]:
    """{'host'|'user': (hwnd, pid)}，各取面积最大的游戏窗口。"""
    found: list[tuple[int, int, int]] = []   # (pid, hwnd, area)

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if "Stardew Valley" not in title:
            return True
        if title.strip().upper().startswith("SMAPI"):
            return True          # SMAPI 控制台
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 800 or h < 600:
            return True          # 小窗/工具窗
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in procs:
            return True
        found.append((pid.value, hwnd, w * h))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)

    best: dict[str, tuple[int, int, int]] = {}
    for pid, hwnd, area in found:
        tag = "host" if HOST_MARK in procs[pid] else "user"
        if tag not in best or area > best[tag][2]:
            best[tag] = (hwnd, pid, area)
    return {k: (v[0], v[1]) for k, v in best.items()}


def place(hwnd: int, rect: tuple[int, int, int, int], tries: int = 6) -> bool:
    x, y, w, h = rect
    user32.ShowWindow(hwnd, 9)      # SW_RESTORE
    time.sleep(0.25)
    for _ in range(tries):
        user32.MoveWindow(hwnd, x, y, w, h, True)
        time.sleep(0.3)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.left, r.top, r.right - r.left, r.bottom - r.top) == rect:
            return True
    return False


def do_layout(verbose: bool = True) -> dict[str, str]:
    procs = process_map()
    if not procs:
        return {"status": "no-game"}

    prim, sec = screens()
    wins = game_windows(procs)
    result: dict[str, str] = {}

    for tag, target in (("user", prim), ("host", sec)):
        if tag not in wins:
            continue
        hwnd, pid = wins[tag]
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        cur = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        if cur == target:
            result[tag] = "already"
            continue
        ok = place(hwnd, target)
        result[tag] = "ok" if ok else "fail"
        if verbose:
            print(f"  {tag} pid={pid} {cur} -> {target} {'OK' if ok else 'FAIL'}")

    if verbose:
        print(f"  主屏工作区 {prim}")
        print(f"  副屏工作区 {sec}")
    return result


def print_layout() -> None:
    procs = process_map()
    print("活进程:")
    for pid, path in procs.items():
        print(f"  pid={pid} {'主机' if HOST_MARK in path else '你的端'}")

    wins = game_windows(procs)
    print("\n游戏窗口:")
    for tag, (hwnd, pid) in wins.items():
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        name = "主机(AI)" if tag == "host" else "你的端"
        print(f"  [{name}] pid={pid} ({r.left},{r.top}) "
              f"{r.right - r.left}x{r.bottom - r.top}")

    prim, sec = screens()
    print(f"\n主屏 {prim}")
    print(f"副屏 {sec}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="只打印布局")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.print:
        print_layout()
    else:
        print(do_layout())
