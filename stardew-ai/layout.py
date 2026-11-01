#!/usr/bin/env python3
"""把两个星露谷窗口分别摆到主屏和副屏。

为什么需要：
  两个实例共用同一份 startup_preferences，displayIndex 只有一份，
  所以游戏自己起来时两个窗口会叠在同一个位置（实测都是 (3840,468)，
  那个坐标已经超出两块屏幕的范围，看起来就是"都在副屏"）。

做法：
  用 SetWindowPos 强制摆位。因为已经切成 windowMode=0（普通窗口），
  SetWindowPos 才会生效；全屏模式下游戏会反复把自己的尺寸改回去。

用法:
  python layout.py           摆一次
  python layout.py --watch   持续守护（窗口被游戏改回去时重新摆）
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
# 关键：不能设 SetProcessDPIAware。
# 副屏是 150% 缩放，开了 DPI 感知后 2560 会被系统再放大成 3840，
# 窗口就飞出副屏右边界（实测固定在 (3840,468)，宽 2880）。
# 关掉虚拟化后坐标直传，MoveWindow 才能精确落位。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(0)
except Exception:
    pass

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE_PLACEHOLDER = 0


def process_map() -> dict[int, str]:
    """pid -> 可执行文件路径。用路径区分主机和用户端。"""
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='StardewModdingAPI.exe'\" | "
         "Where-Object { $_.ExecutablePath } | "
         "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }"],
        capture_output=True, text=True).stdout
    out: dict[int, str] = {}
    for line in ps.splitlines():
        if "|" in line:
            p, path = line.split("|", 1)
            try:
                out[int(p.strip())] = path.strip()
            except ValueError:
                pass
    return out


def game_windows(procmap: dict[int, str]) -> dict[str, tuple[int, int, int]]:
    """返回 {'host': (hwnd,w,h), 'user': (hwnd,w,h)}，各取窗口面积最大的那个。"""
    found: list[tuple[int, int, int, int]] = []

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 800 or h < 600:          # 排除 SMAPI 小控制台
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in procmap:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if "Stardew Valley" not in buf.value:
            return True
        found.append((pid.value, hwnd, w, h))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)

    best: dict[str, tuple[int, int, int]] = {}
    bestarea: dict[str, int] = {}
    for pid, hwnd, w, h in found:
        tag = "host" if "星露谷AI" in procmap.get(pid, "") else "user"
        area = w * h
        if tag not in bestarea or area > bestarea[tag]:
            bestarea[tag] = area
            best[tag] = (hwnd, w, h)
    return best


def place(hwnd: int, x: int, y: int, w: int, h: int, tries: int = 5) -> bool:
    user32.ShowWindow(hwnd, 9)      # SW_RESTORE
    time.sleep(0.25)
    for _ in range(tries):
        # MoveWindow 同时改位置和尺寸，比 SetWindowPos 更可靠
        user32.MoveWindow(hwnd, x, y, w, h, True)
        time.sleep(0.3)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.left, r.top, r.right - r.left, r.bottom - r.top) == (x, y, w, h):
            return True
    return False


# 主屏 0..1707，副屏 2560..4480（Y 起点 312，可用高约 1032）
LAYOUT = {
    "user": (0, 0, 1700, 1028),        # 你的端 -> 主屏
    "host": (2560, 312, 1920, 1032),   # AI 主机 -> 副屏
}


def step() -> str:
    procmap = process_map()
    if not procmap:
        return "no-game"
    wins = game_windows(procmap)
    if not wins:
        return "no-window"

    done = []
    for tag, (hwnd, w, h) in wins.items():
        if tag not in LAYOUT:
            continue
        x, y, tw, th = LAYOUT[tag]
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.left, r.top, r.right - r.left, r.bottom - r.top) == (x, y, tw, th):
            continue                      # 已经对了，不折腾
        ok = place(hwnd, x, y, tw, th)
        done.append(f"{tag}:{'ok' if ok else 'fail'}")
    return ",".join(done) if done else "already-laid-out"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=3.0)
    args = ap.parse_args()

    if not args.watch:
        print(step())
        return

    print(f"守护中，每 {args.interval}s 检查（Ctrl+C 退出）", flush=True)
    last = None
    while True:
        try:
            s = step()
            if s != last:
                print(f"[{time.strftime('%H:%M:%S')}] {s}", flush=True)
                last = s
        except Exception as e:
            print(f"错误: {e}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n退出")
