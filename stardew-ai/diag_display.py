#!/usr/bin/env python3
"""诊断「AI 抓到的窗口画面」与「显示器上真实画面」为什么不是同一幅。

在 per-monitor-v2 感知下，取物理像素坐标：
  1. 每个显示器的物理矩形（EnumDisplayMonitors / MONITORINFO）
  2. 每个游戏窗口的物理矩形、可见性、最小化状态、所属进程/路径
  3. 每块显示器整屏抓图 → 存 PNG，一眼看出游戏到底在哪
  4. 判定：窗口是否落在某块显示器内、是否覆盖不到、是否在屏幕外
"""
from __future__ import annotations

import ctypes
import json
import subprocess
from ctypes import wintypes
from pathlib import Path

from PIL import Image

# Windows 控制台是 GBK，写 ASCII 才不炸；中文一律走 utf-8 重定向
try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "screenshots"
SHOTS.mkdir(exist_ok=True)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32

# -4 = PER_MONITOR_AWARE_V2：要物理像素，不要被 150% 缩放虚拟化
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    DPI = "per-monitor-v2"
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        DPI = "per-monitor"
    except Exception:
        DPI = "unaware"


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", ctypes.c_uint32),
                ("szDevice", ctypes.c_wchar * 32)]


def monitors():
    out = []

    def cb(hmon, hdc, lprc, data):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append({"device": mi.szDevice,
                        "primary": bool(mi.dwFlags & 1),
                        "rect": (r.left, r.top, r.right, r.bottom),
                        "size": (r.right - r.left, r.bottom - r.top)})
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                         wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                         wintypes.LPARAM)
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


def process_path(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return "?"
    finally:
        k32.CloseHandle(h)


def windows():
    out = []

    def cb(hwnd, lp):
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
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append({
            "hwnd": hwnd, "title": title, "pid": pid.value,
            "proc": process_path(pid.value),
            "rect": (r.left, r.top, r.right, r.bottom),
            "size": (r.right - r.left, r.bottom - r.top),
            "iconic": bool(user32.IsIconic(hwnd)),
            "cloaked": bool(ctypes.c_int(0)) if False else None,
        })
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


class BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def grab(x, y, w, h) -> Image.Image:
    hdc = user32.GetDC(0)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)
    buf = ctypes.create_string_buffer(w * h * 4)
    bmi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1).copy()
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(0, hdc)
    return img


def main():
    print(f"DPI awareness: {DPI}\n")

    mons = monitors()
    print("显示器（物理像素）:")
    for m in mons:
        print(f"  {m['device']} primary={m['primary']} rect={m['rect']} size={m['size']}")
    print()

    wins = windows()
    print(f"游戏窗口（物理像素）: {len(wins)} 个")
    for w in wins:
        l, t, r, b = w["rect"]
        inside = [m["device"] for m in mons
                  if not (r <= m["rect"][0] or l >= m["rect"][2]
                          or b <= m["rect"][1] or t >= m["rect"][3])]
        offscreen = not inside
        print(f"  hwnd={w['hwnd']} pid={w['pid']} iconic={w['iconic']}")
        print(f"    proc={w['proc']}")
        print(f"    rect={w['rect']} size={w['size']} -> 覆盖显示器={inside or '无(在屏幕外!)'}")
        if offscreen:
            print("    ⚠ 该窗口矩形不与任何显示器相交 —— PrintWindow 仍能抓到画面，"
                  "但用户屏幕上什么也看不到")
    print()

    for i, m in enumerate(mons):
        l, t, r, b = m["rect"]
        img = grab(l, t, r - l, b - t)
        p = SHOTS / f"monitor{i}_{m['device'].replace(chr(92), '')}.png"
        img.save(p)
        print(f"整屏抓图: {p} {img.size}")

    (HERE / "diag_display.json").write_text(
        json.dumps({"dpi": DPI, "monitors": mons,
                    "windows": [{k: (v if k != "hwnd" else int(v))
                                 for k, v in w.items()} for w in wins]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结构化结果: {HERE / 'diag_display.json'}")


if __name__ == "__main__":
    main()
