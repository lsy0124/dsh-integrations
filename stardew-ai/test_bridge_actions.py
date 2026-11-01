"""实测 bridge_actions 的确认逻辑：故意制造失败，看它能不能诚实报出来。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"<DSH_HARNESS>\stardew-ai")
import bridge_actions as ba


def show(name, r):
    print(f"\n--- {name}")
    print(json.dumps(r, ensure_ascii=False, indent=1)[:900])


print("=" * 70)
print("用例1：正常 warp 到 Farm 已知可站立点")
r = ba.warp("Farm", 64, 16)
show("warp(64,16)", r)
assert r["verified"], "正常 warp 应该验证成功"

print("\n" + "=" * 70)
print("用例2：move_to 到邻近可走格")
cur = ba.player_tile(ba.snapshot())
tx, ty = cur[0] - 3, cur[1] + 3
r = ba.move_to(tx, ty)
show(f"move_to({tx},{ty}) 起点 {cur}", r)

print("\n" + "=" * 70)
print("用例3：故意 move_to 到一个大概率不可达的远点（应诚实报 partial/fail）")
r = ba.move_to(5, 5)
show("move_to(5,5) 远点", r)
print(">>> 这条如果 ok=False 且带 hint，说明假成功被抓住了")

print("\n" + "=" * 70)
print("用例4：face 确认")
r = ba.face(2)
show("face(2)", r)

print("\n" + "=" * 70)
print("用例5：neighborhood 覆盖率")
r = ba.neighborhood(radius=8)
print(f"coverage={r['coverage']} 玩家={r['player_tile']} 半径内格数={len(r['tiles_in_radius'])}")
print("note:", r["note"])

print("\n" + "=" * 70)
print("用例6：recover（当前没卡，应该第一下就走开）")
r = ba.recover()
show("recover()", r)

print("\n全部用例跑完")
