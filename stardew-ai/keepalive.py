#!/usr/bin/env python3
"""让星露谷在后台也持续 tick —— 不抢你的焦点。

问题：SDL 窗口失去焦点后，游戏主循环会被系统降频，timeOfDay 不走、
      PathFindController 不推进。实测 time 会整段冻在同一个值上。

做法：用 SetWindowPos 的 SWP_NOACTIVATE 反复把窗口提到 Z 序顶部，
     但**不激活它**（不进前台、不抢键盘、不打断你在主屏干的事）。
     实测这样能让渲染循环保持运行。

只在检测到窗口确实在后台时才动手；游戏已经在前台就什么都不做。

用法:
  python keepalive.py                 持续守护
  python keepalive.py --once          只做一次
  python keepalive.py --interval 8    每 8 秒一次
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def find_game_window() -> int | None:
    found: list[tuple[int, int]] = []

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if "Stardew Valley" not in buf.value:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 400 or h < 300:
            return True
        found.append((hwnd, w * h))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        return None
    found.sort(key=lambda x: x[1], reverse=True)
    return found[0][0]


def nudge(hwnd: int) -> bool:
    """把窗口提到 Z 序顶部但不激活它。"""
    return bool(user32.SetWindowPos(
        hwnd, HWND_TOP, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW))


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=8.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="即使游戏在前台也 nudge")
    args = ap.parse_args()

    def step() -> str:
        hwnd = find_game_window()
        if not hwnd:
            return "no-game"
        if is_foreground(hwnd) and not args.force:
            return "foreground"      # 已经在前台，别多事
        ok = nudge(hwnd)
        return "nudged" if ok else "failed"

    if args.once:
        print(step())
        return

    print(f"守护中，每 {args.interval}s 检查一次（Ctrl+C 退出）", flush=True)
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
