"""验证 PostMessage 是否奏效；没效再上 SendInput。"""
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"<DSH_HARNESS>\stardew-ai")
import bridge_actions as ba
import game_input as gi

print("投递 PostMessage ESC 之前，先看移动是否恢复正常（用 face 当活性探针 + 小步移动）")
start = ba.player_tile(ba.snapshot())
print("起始 tile:", start)

r = ba.move_to(start[0], start[1] + 1, budget=1.5)
print("小步移动:", r["reason"], "->", r.get("to"))
if r["ok"]:
    print(">>> PostMessage 生效了，对话框已关")
    sys.exit(0)

print(">>> PostMessage 无效（SDK 后端忽略），改用 SendInput")
res = gi.close_menu(use_sendinput=True)
print(json.dumps(res, ensure_ascii=False))
time.sleep(0.8)

start2 = ba.player_tile(ba.snapshot())
r2 = ba.move_to(start2[0], start2[1] + 1, budget=2.0)
print("SendInput ESC 之后小步移动:", r2["reason"], "->", r2.get("to"))
print("结论:", "SendInput 有效，对话框关掉了" if r2["ok"] else "两种都没关掉，需要人工按键")
