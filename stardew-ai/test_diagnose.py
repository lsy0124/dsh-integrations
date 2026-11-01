"""确诊当前卡住原因 —— 现场就是 Marnie 对话框开着。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"<DSH_HARNESS>\stardew-ai")
import bridge_actions as ba

print("=" * 74)
print("1) diagnose_stuck() —— 分层探针确诊")
print("=" * 74)
r = ba.diagnose_stuck()
print(json.dumps(r, ensure_ascii=False, indent=1))

print()
print("=" * 74)
print("2) try_exit_menu() —— 用调试命令通道绕开关不掉菜单的问题")
print("=" * 74)
r2 = ba.try_exit_menu()
print(json.dumps(r2, ensure_ascii=False, indent=1))

print()
print("=" * 74)
print("3) 如果 exitMenu 有效，现在应该能走了")
print("=" * 74)
cur = ba.player_tile(ba.snapshot())
print("当前 tile:", cur)
if cur:
    m = ba.move_to(cur[0] - 2, cur[1] + 2)
    print(json.dumps(m, ensure_ascii=False, indent=1))
