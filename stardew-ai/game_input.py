#!/usr/bin/env python3
"""给游戏窗口发按键，用来关掉吞掉移动输入的对话框/菜单。

为什么要这个：
    实测确认 FarmhandAI 的移动管线在有对话框时完全被吞，且回执仍报成功。
    dll 里的 exitActiveMenu 没暴露成 actionType，stardew_console 的调试命令
    也解不开 NPC 对话框（exitMenu 实测无效）。
    唯一还能从外部干预的通道就是**给窗口投递按键**。

两种投递方式，实测结论写在各自 docstring 里：
    post_key()  —— PostMessage，不需要抢焦点，但 SDL/MonoGame 通常不处理
    send_key()  —— SendInput，必须让游戏窗口在前台，会短暂占用键盘
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102

VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
VK_SPACE = 0x20


def find_game_hwnd(exe_hint: str = "星露谷AI") -> int | None:
    """找 AI 那份游戏窗口。按进程可执行路径分辨，不按面积/位置猜。"""
    import os
    k32 = ctypes.windll.kernel32
    hit = []

    def path_of(pid):
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
        finally:
            k32.CloseHandle(h)

    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if "Stardew Valley" not in buf.value:
            return True
        if buf.value.strip().upper().startswith("SMAPI"):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        p = path_of(pid.value)
        if exe_hint in p:
            hit.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return hit[0] if hit else None


def post_key(hwnd: int, vk: int = VK_ESCAPE) -> bool:
    """PostMessage 投递按键到指定窗口，不抢焦点。

    ⚠ 实测：SDL2 后端（星露谷 1.6.8 用的）读的是键盘原始状态，
       WM_KEYDOWN 走的是另一条路，多数情况**不生效**。留着作为无害尝试。
    """
    scan = user32.MapVirtualKeyW(vk, 0)
    lparam_down = 1 | (scan << 16)
    lparam_up = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    ok1 = user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam_down)
    time.sleep(0.05)
    ok2 = user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up)
    return bool(ok1 and ok2)


def send_key(vk: int = VK_ESCAPE, hold_ms: int = 60) -> bool:
    """SendInput 真键盘事件。需要把游戏窗口弄到前台，会短暂占用键盘。

    这是**唯一实测有效**的关菜单方式，代价是抢一次焦点。
    用完记得把焦点还给用户（见 restore_foreground）。
    """
    # 记下当前前台窗口，事后还回去
    prev = user32.GetForegroundWindow()

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
                    ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_uint32))]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_uint32), ("u", _U)]

    def make(vk_, flags):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.ki = KEYBDINPUT(vk_, 0, flags, 0, None)
        return i

    down = make(vk, 0)
    up = make(vk, KEYEVENTF_KEYUP)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(hold_ms / 1000.0)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))

    # 焦点还回去，别偷用户的键盘
    if prev and prev != user32.GetForegroundWindow():
        try:
            user32.SetForegroundWindow(prev)
        except Exception:
            pass
    return True


def focus_window(hwnd: int) -> bool:
    """把窗口提到前台（SendInput 的前提）。"""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        return False


def close_menu(hwnd: int | None = None, use_sendinput: bool = False) -> dict:
    """尝试关掉当前菜单/对话框。默认只用 PostMessage（不抢焦点）。

    2026-10-28 实测（当时画面确定是 Marnie 对话框）：
        PostMessage(WM_KEYDOWN, ESC)  → 对话框没关
        SendInput(ESC) + SetForegroundWindow → 对话框也没关
    两者都没效果。SendInput 那次虽返回成功，但游戏端没有反应，
    推测是副屏窗口在当前会话里拿不到真正的键盘焦点（SetForegroundWindow
    对非本进程的前台窗口经常只是"闪一下"而不真正提权前台）。
    所以对外结论是：**这条路在自动化场景下不可靠，不要指望它**。
    真正该修的是模组：把已有的 exitActiveMenu 暴露成一个 actionType。
    """
    hwnd = hwnd or find_game_hwnd()
    if not hwnd:
        return {"ok": False, "detail": "找不到 AI 那份游戏窗口"}
    if use_sendinput:
        focus_window(hwnd)
        time.sleep(0.15)
        send_key(VK_ESCAPE)
        return {"ok": True, "method": "SendInput+VK_ESCAPE",
                "reliable": False,
                "detail": "已发送 ESC（真键盘事件，焦点事后已还回）",
                "caveat": "实测对副屏窗口常无效 —— 拿不到真正前台焦点时游戏收不到输入"}
    post_key(hwnd, VK_ESCAPE)
    return {"ok": True, "method": "PostMessage+WM_KEYDOWN",
            "reliable": False,
            "detail": "已投递 ESC（不抢焦点）",
            "caveat": "实测无效：SDL2 后端不处理投递的 WM_KEYDOWN"}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    h = find_game_hwnd()
    print("hwnd:", h)
    if h:
        import json
        print(json.dumps(close_menu(h), ensure_ascii=False, indent=1))
