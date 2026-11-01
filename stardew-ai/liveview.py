#!/usr/bin/env python3
"""星露谷 AI 实例实时画面 — 浏览器打开就能看，主屏/手机都行。

为什么单独做一个服务（而不是靠 stardew_screenshot 一张张要）：
  · PrintWindow 抓窗口表面，**不需要游戏在前台**，也不会抢用户焦点；
  · MJPEG 流是浏览器原生支持的，零依赖、低延迟，手机上也能看；
  · AI 自己在玩的时候，人可以挂着看，不用一直问它要截图。

用法:
  python liveview.py                 # 默认 8899 端口，监听所有网卡
  python liveview.py --port 8899 --fps 4
  浏览器打开 http://127.0.0.1:8899/  （手机用 http://<本机内网IP>:8899/）
"""
from __future__ import annotations

import argparse
import ctypes
import io
import sys
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()


def find_game_window() -> tuple[int, tuple[int, int, int, int]] | None:
    """找星露谷主窗口（标题含 Stardew Valley 且尺寸最大那个）。

    不硬编码 hwnd —— 游戏重启后句柄会变。
    """
    found: list[tuple[int, str, tuple[int, int, int, int]]] = []

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
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 400 or h < 300:      # 排除 SMAPI 那个小控制台
            return True
        found.append((hwnd, title, (r.left, r.top, w, h)))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        return None
    found.sort(key=lambda x: x[2][2] * x[2][3], reverse=True)
    hwnd, _t, rect = found[0]
    return hwnd, rect


class _BIH(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def grab(hwnd: int, w: int, h: int) -> Image.Image | None:
    """PrintWindow 抓窗口表面。无视遮挡，不要求前台。"""
    hwndDC = user32.GetWindowDC(hwnd)
    if not hwndDC:
        return None
    mfcDC = gdi32.CreateCompatibleDC(hwndDC)
    bmp = gdi32.CreateCompatibleBitmap(hwndDC, w, h)
    old = gdi32.SelectObject(mfcDC, bmp)
    img = None
    try:
        # PW_RENDERFULLCONTENT = 2，让 DX/GL 渲染的内容也能抓到
        user32.PrintWindow(hwnd, mfcDC, 2)
        bmi = _BIH()
        bmi.biSize = ctypes.sizeof(_BIH)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mfcDC, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    except Exception:
        img = None
    finally:
        gdi32.SelectObject(mfcDC, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mfcDC)
        user32.ReleaseDC(hwnd, hwndDC)
    return img


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>星露谷 · AI 实时画面</title>
<style>
  html,body{margin:0;height:100%;background:#111;color:#ddd;
    font:13px/1.5 -apple-system,"Segoe UI",sans-serif}
  #wrap{display:flex;flex-direction:column;height:100%}
  #bar{padding:6px 10px;background:#1b1b1b;display:flex;gap:14px;
    align-items:center;flex:0 0 auto;border-bottom:1px solid #2a2a2a}
  #bar b{color:#7cc4ff;font-weight:600}
  #st{color:#888}
  #st.on{color:#5f5}
  #st.off{color:#f55}
  #box{flex:1;display:flex;align-items:center;justify-content:center;
    overflow:hidden;background:#000}
  img{max-width:100%;max-height:100%;image-rendering:pixelated;display:block}
</style></head><body>
<div id="wrap">
  <div id="bar">
    <b>HOST_PLAYER</b>
    <span id="st">连接中…</span>
    <span id="fps" style="color:#666"></span>
    <a href="/" style="margin-left:auto;color:#5af;text-decoration:none">重载</a>
  </div>
  <div id="box"><img id="v" alt=""></div>
</div>
<script>
const v=document.getElementById('v'), st=document.getElementById('st'),
      fp=document.getElementById('fps');
let t0=Date.now(), n=0;
v.onload=()=>{ st.textContent='● 直播中'; st.className='on';
  n++; const el=(Date.now()-t0)/1000;
  if(el>=2){ fp.textContent=(n/el).toFixed(1)+' fps'; t0=Date.now(); n=0; } };
v.onerror=()=>{ st.textContent='● 没画面（游戏没开？）'; st.className='off';
  setTimeout(()=>{ v.src='/snap?t='+Date.now(); }, 2000); };
v.src='/stream';
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    fps = 4

    def log_message(self, fmt, *args):   # 静音，别刷屏
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/snap":
            self._send_jpeg()
        elif path == "/stream":
            self._send_stream()
        elif path == "/health":
            w = find_game_window()
            body = (b'{"game":true}' if w else b'{"game":false}')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def _jpeg(self) -> bytes | None:
        win = find_game_window()
        if not win:
            return None
        hwnd, (_x, _y, w, h) = win
        img = grab(hwnd, w, h)
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72)
        return buf.getvalue()

    def _send_jpeg(self):
        data = self._jpeg()
        if data is None:
            self.send_error(503, "No game window")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_stream(self):
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        delay = 1.0 / max(1, self.fps)
        try:
            while True:
                data = self._jpeg()
                if data is None:
                    time.sleep(1.0)
                    continue
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n")
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                time.sleep(delay)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return      # 浏览器关了，正常退出


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--fps", type=int, default=4, help="画面刷新率")
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    Handler.fps = args.fps
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True

    win = find_game_window()
    print(f"游戏窗口: {win[1] if win else '未找到（游戏没开）'}")
    print(f"本机看:   http://127.0.0.1:{args.port}/")
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        print(f"手机看:   http://{ip}:{args.port}/")
    except Exception:
        pass
    print(f"刷新率:   {args.fps} fps")
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")


if __name__ == "__main__":
    main()
