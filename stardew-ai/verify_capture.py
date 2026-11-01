#!/usr/bin/env python3
"""对照实验：PrintWindow 截到的窗口画面 vs 显示器上真实看到的画面。

用途：定位「AI 看到的画面 ≠ 显示器画面」到底是
  A) 抓错窗口（抓到 SMAPI 控制台 / 抓到用户的客户端）
  B) DPI 缩放导致尺寸/偏移不符（黑边、错位、只截了一角）
  C) 截到过期帧（PrintWindow 拿到上一次合成的表面）
  D) 无边框全屏独占导致 PrintWindow 抓不到内容

输出：两张 PNG + 逐像素差异统计，差异图另存。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "screenshots"
SHOTS.mkdir(exist_ok=True)


def set_dpi_aware():
    """按显示器逐个 DPI 感知：拿到物理像素坐标，不受 150% 缩放虚拟化干扰。"""
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        return "per-monitor"
    except Exception:
        return "unaware"


def physical_rects():
    """每个显示器的物理像素矩形（EnumDisplaySettings，不吃缩放）。"""
    class DEVMODE(ctypes.Structure):
        _fields_ = [("dmDeviceName", ctypes.c_wchar * 32),
                    ("dmSpecVersion", ctypes.c_uint16), ("dmDriverVersion", ctypes.c_uint16),
                    ("dmSize", ctypes.c_uint16), ("dmDriverExtra", ctypes.c_uint16),
                    ("dmFields", ctypes.c_uint32),
                    ("dmPositionX", ctypes.c_int32), ("dmPositionY", ctypes.c_int32),
                    ("dmDisplayOrientation", ctypes.c_uint32), ("dmDisplayFixedOutput", ctypes.c_uint32),
                    ("dmColor", ctypes.c_int16), ("dmDuplex", ctypes.c_int16),
                    ("dmYResolution", ctypes.c_int16), ("dmTTOption", ctypes.c_int16),
                    ("dmCollate", ctypes.c_int16),
                    ("dmFormName", ctypes.c_wchar * 32),
                    ("dmLogPixels", ctypes.c_uint16), ("dmBitsPerPel", ctypes.c_uint32),
                    ("dmPelsWidth", ctypes.c_uint32), ("dmPelsHeight", ctypes.c_uint32),
                    ("dmDisplayFlags", ctypes.c_uint32), ("dmDisplayFrequency", ctypes.c_uint32),
                    ("dmICMMethod", ctypes.c_uint32), ("dmICMIntent", ctypes.c_uint32),
                    ("dmMediaType", ctypes.c_uint32), ("dmDitherType", ctypes.c_uint32),
                    ("dmReserved1", ctypes.c_uint32), ("dmReserved2", ctypes.c_uint32),
                    ("dmPanningWidth", ctypes.c_uint32), ("dmPanningHeight", ctypes.c_uint32)]

    res = []
    i = 0
    while True:
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        if not ctypes.windll.user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
            break
        res.append((dm.dmPositionX, dm.dmPositionY, dm.dmPelsWidth, dm.dmPelsHeight,
                    dm.dmDisplayFrequency, dm.dmBitsPerPel))
        i += 1
    return res


def grab_screen(x: int, y: int, w: int, h: int) -> Image.Image:
    """GDI 抓屏（物理像素）。"""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hdc = user32.GetDC(0)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)  # SRCCOPY
    buf = ctypes.create_string_buffer(w * h * 4)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    bmi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1).copy()
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(0, hdc)
    return img


def main() -> int:
    mode = set_dpi_aware()
    print(f"DPI mode: {mode}")
    print("物理分辨率:")
    for r in physical_rects():
        print(f"  pos=({r[0]},{r[1]}) {r[2]}x{r[3]} @{r[4]}Hz {r[5]}bpp")

    sys.path.insert(0, str(HERE))
    import sdv

    wins = sdv.stardew_windows()
    print(f"\n游戏窗口（插件枚举）: {len(wins)}")
    for pid, title, rect in wins:
        print(f"  pid={pid} rect={rect} {rect[2]-rect[0]}x{rect[3]-rect[1]}  title={title!r}")
        try:
            exe = Path(ctypes.windll.kernel32.QueryFullProcessImageNameW)  # noqa: F841
        except Exception:
            pass

    if not wins:
        print("没找到游戏窗口")
        return 1

    # 1) 插件自己那条路径截的窗口图
    win_shot = SHOTS / "verify_printwindow.png"
    sdv.shot(str(win_shot))
    win_img = Image.open(win_shot).convert("RGB")

    # 2) 窗口所在显示器上，真实看到的画面
    pid, title, rect = max(wins, key=lambda w: (w[2][2] - w[2][0]) * (w[2][3] - w[2][1]))
    l, t, r, b = rect
    w, h = r - l, b - t
    scr_img = grab_screen(l, t, w, h)
    scr_path = SHOTS / "verify_screen.png"
    scr_img.save(scr_path)
    print(f"\n窗口图: {win_shot} {win_img.size}")
    print(f"屏幕图: {scr_path} {scr_img.size}")

    # 3) 逐像素对比
    if win_img.size != scr_img.size:
        print(f"尺寸不一致: window={win_img.size} screen={scr_img.size}")
        win_img = win_img.resize(scr_img.size)
    diff = ImageChops.difference(win_img, scr_img)
    bbox = diff.getbbox()
    hist = diff.convert("L").histogram()
    total = sum(hist)
    big = sum(hist[24:])
    print(f"差异区域 bbox={bbox}")
    print(f"明显差异像素(>=24): {big}/{total} = {big/total*100:.2f}%")
    diff.point(lambda v: min(255, v * 4)).save(SHOTS / "verify_diff.png")
    print(f"差异图: {SHOTS / 'verify_diff.png'}")

    # 4) 窗口图自身的完整性检查（黑边 = DPI 缩放导致的多余区域）
    import numpy as np
    a = np.asarray(win_img)
    for name, strip in (("top", a[:8]), ("bottom", a[-8:]), ("left", a[:, :8]), ("right", a[:, -8:])):
        black = float((strip.max(axis=2) < 8).mean())
        print(f"  窗口图 {name} 8px 全黑比例: {black*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
