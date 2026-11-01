#!/usr/bin/env python3
"""A/B：生产截图路径（sdv.shot/PrintWindow）vs 显示器真实画面（per-monitor-v2 BitBlt）。

两件事一起验：
  1. 选窗对不对：把「所有标题含 Stardew Valley 的窗口」原样打出来，
     看生产过滤规则（标题以 SMAPI 开头才算控制台）会不会把 SMAPI 控制台当成游戏。
  2. 像素对不对：同一窗口，PrintWindow 图 vs 屏幕抓图，尺寸/差异百分比/黑边比例。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
SHOTS = HERE / "screenshots"
SHOTS.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE))

user32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32


def proc_path(pid: int) -> str:
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else "?"
    finally:
        k32.CloseHandle(h)


def all_titled():
    out = []

    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        t = buf.value
        if "Stardew Valley" not in t:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(dict(hwnd=hwnd, pid=pid.value, title=t,
                        rect=(r.left, r.top, r.right, r.bottom),
                        size=(r.right - r.left, r.bottom - r.top),
                        iconic=bool(user32.IsIconic(hwnd)),
                        starts_with_SMAPI=t.strip().upper().startswith("SMAPI")))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def main():
    print("=" * 78)
    print("① 所有标题含 'Stardew Valley' 的可见窗口（生产过滤前的原始集合）")
    print("=" * 78)
    rows = all_titled()
    for w in rows:
        print(f"hwnd={w['hwnd']:>10} pid={w['pid']:<7} iconic={int(w['iconic'])} "
              f"size={w['size']} startsWithSMAPI={int(w['starts_with_SMAPI'])}")
        print(f"   title={w['title']!r}")
        print(f"   proc ={proc_path(w['pid'])}")

    print()
    print("=" * 78)
    print("② 生产过滤规则（sdv.stardew_windows）留下的窗口")
    print("=" * 78)
    import sdv
    kept = sdv.stardew_windows()
    for pid, title, rect in kept:
        print(f"pid={pid} size=({rect[2]-rect[0]}x{rect[3]-rect[1]}) rect={rect}")
        print(f"   proc={proc_path(pid)}")
        print(f"   title={title!r}")
    print(f"\n--> 留下了 {len(kept)} 个，shot() 会按面积排序取第 1 个（面积相同则顺序不定）")

    if not kept:
        return

    # 生产路径截图
    prod = SHOTS / "ab_prod_printwindow.png"
    sdv.shot(str(prod))
    pimg = Image.open(prod).convert("RGB")

    # 换成 per-monitor-v2 之后，同一窗口的真实屏幕像素
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    pid, title, rect = max(kept, key=lambda w: (w[2][2] - w[2][0]) * (w[2][3] - w[2][1]))
    l, t, r, b = rect
    w, h = r - l, b - t
    hdc = user32.GetDC(0)
    gdi32 = ctypes.windll.gdi32
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, hdc, l, t, 0x00CC0020)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    buf = ctypes.create_string_buffer(w * h * 4)
    bmi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    simg = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1).copy()
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(0, hdc)
    scr = SHOTS / "ab_real_screen.png"
    simg.save(scr)

    print()
    print("=" * 78)
    print("③ 像素对比")
    print("=" * 78)
    print(f"生产图(PrintWindow): {prod}  {pimg.size}")
    print(f"真实屏幕(BitBlt)   : {scr}  {simg.size}")

    if pimg.size != simg.size:
        # 尺寸不同本身就是关键证据：PrintWindow 按 (可能被虚拟化的) 窗口矩形建位图
        print(f"!! 尺寸不一致 window={pimg.size} screen={simg.size} "
              f"比例 x={simg.size[0]/pimg.size[0]:.3f} y={simg.size[1]/pimg.size[1]:.3f}")
        pimg = pimg.resize(simg.size)

    d = ImageChops.difference(pimg, simg).convert("L")
    hist = d.histogram()
    total = sum(hist)
    for thr in (8, 24, 64):
        n = sum(hist[thr:])
        print(f"  差异>={thr:>3}: {n/total*100:6.2f}%")

    # 黑边检测：真实游戏画面四周不会是纯黑长条；有黑边 = 位图尺寸与渲染尺寸不符
    for name, strip in (("top", simg.crop((0, 0, w, 8))),
                        ("bottom", simg.crop((0, h - 8, w, h))),
                        ("left", simg.crop((0, 0, 8, h))),
                        ("right", simg.crop((w - 8, 0, w, h)))):
        px = list(strip.getdata())
        black = sum(1 for p in px if max(p) < 8) / len(px)
        print(f"  真实屏幕 {name} 8px 全黑: {black*100:5.1f}%")


if __name__ == "__main__":
    main()
