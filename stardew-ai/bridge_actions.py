#!/usr/bin/env python3
"""FarmhandAI 桥接的「有确认的动作」层。

问题（2026-10-28 实测确定）：
    mod 回写 lastAction.LastOk 只代表「动作 JSON 被我消费了」，
    不代表游戏真的执行了。实测三种情况全部静默失败但仍回 ok=true：
      · 人物躺在床上没下床      → move_to 完全不动
      · 有对话框/菜单开着         → move_to 完全不动
      · warp 目标格不可站立       → 落点偏移 1 格
    这让 AI 拿着「成功」回执对着不动的画面，表现为「操作僵硬」。

本模块的做法：
    每个动作发出后不信任回执，改为**轮询桥接快照做后置条件校验**：
      move_to  → 必须观测到 tile 变成目标（或至少离开原地并靠近）
      warp     → 必须观测到 location 变目标
      face     → 必须观测到 facing 变目标
      use_tool / interact → 观测 lastAction 变化 + 目标格 obj/crop 变化
    校验不过就返回结构化失败，带原因猜测（睡着 / 有弹窗 / 不可达），
    让上层能采取补救动作而不是盲目继续。

另外补一个 bridge 缺的感知：真正可用的「以玩家为中心的邻域格子」。
现有 surroundings.tiles 只给 x/y∈[1,11] 的 31 个房间边界墙，
玩家在 Farm 时这些坐标毫无意义 —— 这是「AI 不知道自己周围有什么」的根源。

2026-10-28 补充（反编译 FarmhandAI.dll 确认）：
    dll 里存在 exitActiveMenu / get_activeClickableMenu 两个符号，
    说明模组本身能检测并关闭当前菜单，但**没有对应的 actionType 分支**暴露给桥接，
    所以外面无路可走 —— 对话框一开，所有移动指令永久假成功。
    本模块用「转向不被菜单拦」这一实测事实做活性探针，并在卡住时给出
    明确的恢复建议（见 recover / diagnose_stuck）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

GAME = Path(r"<AI_GAME_DIR>")
BRIDGE = GAME / "Mods" / "FarmhandAI" / "bridge_data.json"
ACTIONS = GAME / "Mods" / "FarmhandAI" / "actions"

_seq = int(time.time() * 1000)

# 观测预算：mod 每 ~0.5s 刷一次快照，给足 3.5s 让寻路真的走完
MOVE_BUDGET = 3.5
ACT_BUDGET = 1.5


# ---------------------------------------------------------------- 基础读写

def snapshot() -> dict:
    """读一次桥接快照，带年龄。"""
    if not BRIDGE.exists():
        return {"_error": "bridge_data.json 不存在 —— 游戏没开"}
    age = time.time() - BRIDGE.stat().st_mtime
    try:
        d = json.loads(BRIDGE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"_error": f"桥接读取失败: {e}"}
    d["_age"] = round(age, 2)
    return d


def alive() -> bool:
    """桥接是否还在刷新（20s 阈值，与生产一致）。"""
    d = snapshot()
    return "_error" not in d and d.get("_age", 999) < 20


def _send(payload: dict) -> None:
    global _seq
    _seq += 1
    ACTIONS.mkdir(parents=True, exist_ok=True)
    p = ACTIONS / f"{_seq}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 状态指纹

def player_tile(d: dict) -> tuple[int, int] | None:
    t = (d.get("player") or {}).get("tile") or {}
    if "x" in t and "y" in t:
        return int(t["x"]), int(t["y"])
    return None


def location(d: dict) -> str:
    return d.get("location") or "?"


def facing(d: dict) -> int | None:
    f = (d.get("player") or {}).get("facing")
    return int(f) if f is not None else None


def is_in_bed(d: dict) -> bool:
    """人物是否还在床上。

    没有直接字段，用两个信号推断：
      1. 位置停在 FarmHouse 且长时间不动（床在 (9,9) 一带）
      2. 时间是清晨但坐标一直是屋内固定点
    更可靠的做法是看 mod 有没有暴露 sleep 状态；这里先用保守启发式，
    真正的判定交给 move_to 的后置条件失败去说明。
    """
    tiles = [t for t in ((d.get("surroundings") or {}).get("tiles") or [])]
    indoors = location(d) == "FarmHouse"
    return indoors and not tiles  # 屋内且没有任何有效邻域数据


def dialog_open_guess(d: dict) -> bool:
    """猜测是否有弹窗/菜单挡住输入。

    mod 不暴露菜单状态，但可以看：人物在屋里、时间在走、move 无效果。
    这里只做一个弱提示，不作为硬判定。
    """
    return False


# ---------------------------------------------------------------- 补的感知

def neighborhood(d: dict | None = None, radius: int = 4) -> dict:
    """以玩家为中心的邻域格子，带覆盖率标注。

    实测（2026-10-28）mod 上报的是**以玩家为中心 17x17 的窗口**：
      · Farm 户外：135 格，dx/dy 覆盖 -8..+8，草地/杂草/石头/树全在
      · FarmHouse 屋内：只有 31 格，全是墙 —— 因为室内地板属于「无特征格」
        被模组过滤掉了，不是模组坏了
    所以邻域感知本身是可用的，问题是**没有标注这份数据在当前地图是否够用**，
    上层容易把「屋里只有墙」误读成「身边什么都没有」。这里补 coverage。
    """
    d = d or snapshot()
    if "_error" in d:
        return {"error": d["_error"]}
    pt = player_tile(d)
    if not pt:
        return {"error": "拿不到玩家坐标"}
    px, py = pt
    raw = (d.get("surroundings") or {}).get("tiles") or []

    near, far = [], []
    for t in raw:
        dx, dy = abs(int(t["x"]) - px), abs(int(t["y"]) - py)
        (near if max(dx, dy) <= radius else far).append(t)

    return {
        "player_tile": {"x": px, "y": py},
        "location": location(d),
        "radius": radius,
        "tiles_in_radius": near,
        "tiles_out_of_radius": len(far),
        "coverage": (
            "full" if len(near) > 100 else
            "partial" if near else "empty"
        ),
        "note": (
            "屋内（FarmHouse 等）模组只上报墙格，地板被过滤；"
            "此时看不到脚下地面，判断走位请配合 stardew_screenshot。"
            if len(near) <= 40 else
            "户外为 17x17 完整窗口，含地形/杂草/石头/树/水。"
        ),
    }


# ---------------------------------------------------------------- 有确认的动作

def _wait_until(pred, budget: float, poll: float = 0.2) -> tuple[bool, dict]:
    """轮询桥接直到 pred(snapshot) 为真，或超时。回 (ok, 最后一帧)。"""
    end = time.time() + budget
    last = snapshot()
    while time.time() < end:
        if "_error" not in last and pred(last):
            return True, last
        time.sleep(poll)
        last = snapshot()
    return (("_error" not in last) and pred(last)), last


def menu(d: dict | None = None) -> str | None:
    """当前挡路的菜单类型。模组 v0.5+ 会直接给这个字段。"""
    d = d or snapshot()
    return d.get("menu")


def anim(d: dict | None = None) -> dict:
    """动画自检：能确认人物真的在抬腿走，而不是滑行。"""
    d = d or snapshot()
    return d.get("anim") or {}


def say(text: str, who: str | None = None, budget: float = 2.0) -> dict:
    """让某人说一句话（自测/剧情用），走和 NPC 一样的 DialogueBox。"""
    payload = {"actionType": "say", "text": text}
    if who:
        payload["who"] = who
    _send(payload)
    time.sleep(0.4)
    d = snapshot()
    return {
        "ok": menu(d) == "dialogue",
        "menu": menu(d),
        "detail": "对话已显示（模组会自动一页页推进，不跳过）",
        "note": "推进由模组 TickDialogue 完成：打字完整 → 停 1.2s → 翻页",
    }


def talk(name: str | None = None, budget: float = 3.0) -> dict:
    """主动跟 NPC 说话（真人走过去按交互键那种）。

    要求距离 <= 2 格；不在范围会明确报错，不会硬来。
    对话会正常显示在屏幕上，由模组一页页推进（不跳过）。

    判定要小心：桥上 menu 可能因为**别的原因**已经是 dialogue
    （比如刚被另一个 NPC 搭话）。所以要看 lastAction 是不是 talk 且 LastOk。
    """
    before = snapshot()
    m0 = menu(before)
    if m0 == "dialogue":
        return {
            "ok": False, "verified": False, "reason": "already_talking",
            "talker": before.get("talker"),
            "detail": f"已经在和 {before.get('talker')} 说话，等这段读完再找人",
        }

    payload = {"actionType": "talk"}
    if name:
        payload["name"] = name
    _send(payload)

    # 等「模组自己说 talk 成功了」，而不是「场上有对话」
    def talk_ok(d):
        la = d.get("lastAction") or {}
        return la.get("LastAction") == "talk" and la.get("LastOk") is True

    def talk_failed(d):
        la = d.get("lastAction") or {}
        return la.get("LastAction") == "talk" and la.get("LastOk") is False

    end = time.time() + budget
    last = snapshot()
    while time.time() < end:
        if talk_ok(last):
            break
        if talk_failed(last):
            break
        time.sleep(0.15)
        last = snapshot()

    la = last.get("lastAction") or {}
    success = la.get("LastAction") == "talk" and la.get("LastOk") is True

    return {
        "ok": success,
        "verified": success,
        "menu": menu(last),
        "talker": last.get("talker"),
        "detail": la.get("LastDetail"),
        "hint": None if success else (
            "没触发对话：可能距离超过 2 格（先 move_to 靠近），"
            "或该 NPC 不在这张地图/此刻没话说"
        ),
        "note": "对话会正常显示（头像+名字+完整台词），模组自动一页页读完" if success else None,
    }


def dialogue_status() -> dict:
    """看当前对话状态：谁在说、是否还在显示、是不是选择题。"""
    d = snapshot()
    if "_error" in d:
        return {"error": d["_error"]}
    m = menu(d)
    dg = d.get("dialogue") or {}

    if m != "dialogue":
        return {
            "talking": False, "menu": m, "talker": d.get("talker"),
            "note": "当前没有对话",
        }

    out = {
        "talking": True,
        "menu": m,
        "talker": d.get("talker"),
        "speaker": dg.get("speaker"),
        "text": dg.get("fullText"),
        "shown": dg.get("shownText"),
        "typing_done": dg.get("typingDone"),
        "progress": f"{dg.get('charIndex')}/{dg.get('totalChars')}",
        "is_question": bool(dg.get("isQuestion")),
        "remaining_pages": dg.get("remainingPages"),
    }

    if out["is_question"]:
        opts = dg.get("options") or []
        out["options"] = [{"index": o.get("index"), "text": o.get("text"),
                           "key": o.get("key")} for o in opts]
        out["note"] = (
            "这是选择题，需要选一个：stardew_choose(index=... 或 option='yes'/'no')"
            if dg.get("selectedResponse", -1) < 0 else
            "已选好，正在确认"
        )
    else:
        out["note"] = "对话会自己一页页读完，不用干预"

    return out


def choose(index: int | None = None, option: str | None = None,
           budget: float = 6.0) -> dict:
    """回答选择题（Yes/No 这类）。

    两种指定方式：
      index   选项下标（0 起，从 stardew_dialogue 的 options 里看）
      option  按文本/关键词："yes" / "no" / "是" / "否" / 实际选项文字

    提交是分两步的（和真人一样）：
      1. 模组设 selectedResponse
      2. 等 safetyTimer 走完再点击确认
    所以这个函数会等对话推进，返回结果。
    """
    d = snapshot()
    if menu(d) != "dialogue":
        return {"ok": False, "verified": False, "reason": "no_dialogue",
                "detail": "现在没有对话", "hint": "先用 stardew_talk 或等人搭话"}

    dg = d.get("dialogue") or {}
    if not dg.get("isQuestion"):
        return {
            "ok": False, "verified": False, "reason": "not_a_question",
            "detail": f"当前是普通对话（{dg.get('fullText')}），不需要选择",
            "hint": "普通对话会自动读完，不用选",
        }

    opts = dg.get("options") or []
    payload: dict = {"actionType": "choose"}
    if index is not None:
        payload["index"] = int(index)
    elif option is not None:
        payload["option"] = str(option)
    else:
        return {"ok": False, "reason": "no_target",
                "detail": "要指定 index 或 option",
                "available": [{"index": o.get("index"), "text": o.get("text")} for o in opts]}

    _send(payload)
    time.sleep(0.4)

    # 等对话真的推进/关闭
    end = time.time() + budget
    last = snapshot()
    while time.time() < end:
        la = last.get("lastAction") or {}
        if la.get("LastAction") == "choose":
            if la.get("LastOk"):
                break
            if la.get("LastOk") is False:
                break
        # 选项确认后对话会往前推，isQuestion 变 False 或菜单关闭
        dg2 = last.get("dialogue") or {}
        if menu(last) != "dialogue" or not dg2.get("isQuestion"):
            break
        time.sleep(0.2)
        last = snapshot()

    la = last.get("lastAction") or {}
    ok = la.get("LastAction") == "choose" and la.get("LastOk") is True

    return {
        "ok": bool(ok),
        "verified": bool(ok),
        "chosen": option if option is not None else index,
        "detail": la.get("LastDetail"),
        "menu_after": menu(last),
        "dialogue_after": (last.get("dialogue") or {}).get("fullText"),
        "note": "选择已提交，对话继续往下走（会正常显示后续台词）" if ok else None,
    }


def move_to(x: int, y: int, budget: float = MOVE_BUDGET,
            retry_on_dialogue: bool = True, max_retries: int = 2) -> dict:
    """走到某格，并**确认真的走到了**。

    v0.5 起模组自己用 movementDirections 驱动移动，屏幕上能看到走路动画。

    关于 NPC 搭话（v0.6.2 改进）：
      走路途中 NPC 会主动说话，移动会被打断。真人遇到这种情况是
      "听完，然后继续走"。所以这里默认**自动等对话读完再重试**，
      而不是半路就放弃。重试次数有上限，避免被 NPC 缠住。
    """
    before = snapshot()
    if "_error" in before:
        return {"ok": False, "reason": "bridge", "detail": before["_error"]}
    start = player_tile(before)
    if not start:
        return {"ok": False, "reason": "no_tile", "detail": "拿不到起始坐标"}

    m0 = menu(before)
    if m0 == "dialogue":
        dg = before.get("dialogue") or {}
        # 选择题要人来答，不替他做决定
        if dg.get("isQuestion"):
            return {
                "ok": False, "verified": False, "reason": "question_pending",
                "talker": before.get("talker"),
                "detail": f"{before.get('talker')} 在问选择题，需要先回答",
                "hint": "用 stardew_choose 回答后再移动",
                "options": dg.get("options"),
            }
        # 普通对话：等它读完
        waited = 0.0
        while waited < 12.0 and menu(before) == "dialogue":
            time.sleep(0.4)
            waited += 0.4
            before = snapshot()
        if menu(before) == "dialogue":
            return {
                "ok": False, "verified": False, "reason": "talking",
                "talker": before.get("talker"),
                "detail": f"还在和 {before.get('talker')} 说话（等了 12 秒）",
                "hint": "对话会在屏幕上正常读完；选择题用 stardew_choose",
            }
        start = player_tile(before) or start

    if menu(before):
        return {
            "ok": False, "verified": False, "reason": "menu",
            "detail": f"'{menu(before)}' 菜单开着，挡住移动",
            "hint": "这个菜单需要做决定，用 exit_menu force 或人工处理",
        }

    attempts = []
    target = (int(x), int(y))

    for attempt in range(max_retries + 1):
        _send({"actionType": "move_to", "x": x, "y": y})

        seen_anim = set()

        def moving_and_arrived(d):
            a = anim(d)
            if a.get("animIndex") is not None:
                seen_anim.add(a.get("animIndex"))
            return player_tile(d) == target

        got, last = _wait_until(moving_and_arrived, budget)
        end = player_tile(last) or start

        if got:
            return {
                "ok": True, "verified": True, "from": start, "to": end,
                "attempts": attempt + 1,
                "walked_with_animation": len(seen_anim) > 1,
                "anim_frames": sorted(x for x in seen_anim if x is not None),
                "detail": f"走到 ({x},{y}) 并已在桥接里确认"
                          + (f"（被搭话 {attempt} 次，等对话读完继续）" if attempt else "")
                          + ("，走路动画正常" if len(seen_anim) > 1 else ""),
            }

        # 被对话打断 → 等它读完再重试（除非是选择题）
        m1 = menu(last)
        dg1 = last.get("dialogue") or {}
        if m1 == "dialogue":
            if dg1.get("isQuestion"):
                return {
                    "ok": False, "verified": False, "reason": "question_pending",
                    "from": start, "to": end, "target": target,
                    "talker": last.get("talker"),
                    "detail": f"走到 {end} 时 {last.get('talker')} 问了选择题",
                    "hint": "用 stardew_choose 回答后再移动",
                    "options": dg1.get("options"),
                }
            if retry_on_dialogue and attempt < max_retries:
                attempts.append(f"被 {last.get('talker')} 搭话于 {end}")
                time.sleep(0.3)
                # 等对话读完
                waited = 0.0
                while waited < 12.0 and menu(last) == "dialogue":
                    time.sleep(0.4)
                    waited += 0.4
                    last = snapshot()
                if menu(last) == "dialogue":
                    break
                continue
            return {
                "ok": False, "verified": False, "reason": "interrupted_by_dialogue",
                "from": start, "to": end, "target": target,
                "talker": last.get("talker"),
                "attempts": attempts,
                "detail": f"走到 {end} 时被 {last.get('talker')} 搭话 {attempt+1} 次，放弃",
                "hint": "对话会自动读完；稍后再发一次 move_to",
            }

        # 没到也没被搭话
        def dist(a, b):
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        moved = dist(end, target) < dist(start, target)
        return {
            "ok": False,
            "verified": False,
            "reason": "not_moved" if end == start else "partial",
            "from": start, "to": end, "target": target,
            "walked_with_animation": len(seen_anim) > 1,
            "anim_frames": sorted(x for x in seen_anim if x is not None),
            "detail": (
                f"回执说 ok，但 {budget}s 内 tile 停在 {end}，没有移动。"
                "最常见原因：目标不可达 / 被家具挡住 / 卡在角落。"
                if end == start else
                f"只走到 {end}，没到 {target}（差 {dist(end, target)} 格）。"
            ),
            "hint": _stuck_hint(last),
        }

    # 重试用尽
    d = snapshot()
    return {
        "ok": False, "verified": False, "reason": "too_many_interruptions",
        "attempts": attempts, "target": target,
        "detail": f"被反复搭话 {len(attempts)} 次，移动没能完成",
        "hint": "附近 NPC 太多；先 warp 到空地再走",
    }


def warp(loc: str, x: int, y: int, budget: float = 4.0) -> dict:
    """传送到某地图某格，并确认**真的到了那儿**。

    踩过的坑（2026-10-28 审计）：
      1) 原来只检查 location 变没变 —— 同地图 warp 时 location 不变，
         于是报"verified=true 但位置没动"。
      2) 同地图 warp 期间，模组的 bridge 快照更新有延迟，可能读到"旧帧"，
         第一次读到新位置、下一次又读回旧位置 → 结论前后矛盾。
      3) 跨地图 warp 要走过场（黑屏+加载），实测 4 秒不够，会超时误判失败。
    现在的做法：
      · 跨地图给更长的预算（默认 9 秒），同地图 4 秒
      · 只有「location == 目标 且 tile 距离目标 <= 1」才认成功
      · 都做不到就诚实报失败，并区分「根本没动」和「落点偏差」
    """
    before = snapshot()
    start_loc = location(before) if "_error" not in before else "?"
    start_tile = player_tile(before) if "_error" not in before else None

    same_map = (start_loc == loc)
    _send({"actionType": "warp", "location": loc, "x": x, "y": y})

    tx, ty = int(x), int(y)
    # 跨地图要加载，给足时间
    actual_budget = budget if same_map else max(budget, 10.0)

    def near_target(d):
        t = player_tile(d)
        if t is None:
            return False
        return location(d) == loc and abs(t[0] - tx) <= 1 and abs(t[1] - ty) <= 1

    got, last = _wait_until(near_target, actual_budget)

    end_loc = location(last)
    end = player_tile(last)

    if got:
        if end != (tx, ty):
            return {"ok": True, "verified": True, "location": loc,
                    "requested": {"x": tx, "y": ty},
                    "landed": {"x": end[0], "y": end[1]},
                    "offset": {"dx": end[0] - tx, "dy": end[1] - ty},
                    "detail": f"到了 {loc}，但落在 {end}（吸附了相邻格）"}
        return {"ok": True, "verified": True, "location": loc, "landed": end,
                "detail": f"到了 {loc} {end}"}

    # 失败：分清是「根本没动」还是「动到别处了」
    if end == start_tile and end_loc == start_loc:
        return {
            "ok": False, "verified": False, "reason": "no_move",
            "location": end_loc, "tile": end, "requested": {"x": tx, "y": ty},
            "detail": f"回执说 warp 到 {loc}({tx},{ty})，但位置仍是 {end} —— 实际没生效",
            "hint": "目标格可能不可站立；改用 stardew_move_to 走过去更可靠",
        }

    return {
        "ok": False, "verified": False, "reason": "wrong_place",
        "location": end_loc, "tile": end, "requested": {"x": tx, "y": ty},
        "detail": f"位置从 {start_tile} 变成 {end}（现在在 {end_loc}），但没到 {loc}({tx},{ty})",
        "hint": f"{actual_budget:.0f}s 内没等到落点，可能还在加载；稍后再查一次状态",
    }


def face(direction: int, budget: float = 1.2) -> dict:
    """转向，并确认 facing 变了。"""
    want = int(direction) % 4
    before = facing(snapshot())
    _send({"actionType": "face", "direction": want})
    got, last = _wait_until(lambda d: facing(d) == want, budget)
    return {"ok": got, "verified": got, "from": before, "to": facing(last),
            "detail": "转向已确认" if got else "回执 ok 但 facing 没变"}


def _wait_action(action: str, budget: float = 2.0,
                 poll: float = 0.12) -> tuple[dict, dict]:
    """等到「属于本次调用」的 lastAction 出现，返回 (lastAction, snapshot)。

    踩过的坑（2026-10-28 审计）：模组每 30 tick 才把 lastAction 写进桥接，
    如果发完动作只 sleep(0.35) 就去读，读到的往往是**上一条**动作的回执，
    于是 use_tool/eat/attack 全都误判失败（"模组未确认，lastAction=choose"）。

    做法：发动作前先记下当前 lastAction，然后轮询直到它变成目标动作。
    """
    end = time.time() + budget
    last = snapshot()
    while time.time() < end:
        la = last.get("lastAction") or {}
        if la.get("LastAction") == action:
            return la, last
        time.sleep(poll)
        last = snapshot()
    return (last.get("lastAction") or {}), last


def use_tool(tool: str, x: int, y: int, budget: float = 2.5) -> dict:
    """用工具，并确认模组执行了（等本次动作的回执，不读旧值）。"""
    before = snapshot()
    start = player_tile(before)
    _send({"actionType": "use_tool", "tool": tool, "x": x, "y": y})

    la, last = _wait_action("use_tool", budget)
    cell = _tile_at(last, x, y)
    acted = bool(la.get("LastOk"))
    return {
        "ok": acted, "verified": acted,
        "tool": tool, "target": {"x": x, "y": y}, "player": start,
        "target_state": cell,
        "detail": la.get("LastDetail"),
        "caveat": "工具是否真正生效要看地块/背包变化，回执只代表模组执行了动作",
    }


def run(enabled: bool = True, budget: float = 1.5) -> dict:
    """切换跑步（等价于按住 Shift）。跑步动画和走路不同，速度 2 → 5。"""
    _send({"actionType": "run", "enabled": bool(enabled)})
    la, last = _wait_action("run", budget)
    ok = bool(la.get("LastOk"))
    return {
        "ok": ok, "verified": ok,
        "running": enabled,
        "detail": la.get("LastDetail"),
        "note": "跑起来后 move_to 每帧位移会从 2px 变 5px" if ok else None,
    }


def chop(x: int, y: int, budget: float = 60.0, collect: bool = True) -> dict:
    """砍树 —— 反复挥斧直到树倒（10 下左右），然后走过去捡木头。

    实测算术：Tree.startingHealth = 10，普通斧每下 health -= 1，
    所以大树正好 10 下；体力每下 -2。
    砍倒后木头会掉在地上（debrisCount > 0），要走过去才会进背包。

    collect=True 时会自动来回走几趟把地上的木头捡起来。
    """
    before = snapshot()
    wood0 = _count_item(before, "(O)388")
    prev = _current_detail()
    _send({"actionType": "chop", "x": int(x), "y": int(y)})

    la, last = _wait_job("chop", prev, budget)
    ok = bool(la.get("LastOk"))

    collected = 0
    if collect and ok:
        collected = _collect_debris(x, y, rounds=3)

    last2 = snapshot()
    wood1 = _count_item(last2, "(O)388")
    return {
        "ok": ok, "verified": ok,
        "target": {"x": x, "y": y},
        "wood_gained": wood1 - wood0,
        "wood_total": wood1,
        "debris_left": last2.get("debrisCount") or 0,
        "detail": la.get("LastDetail"),
    }


def _collect_debris(cx: int, cy: int, rounds: int = 3) -> int:
    """在目标格附近来回走几趟，把地上的掉落物捡进背包。

    星露谷的 debris 是走到附近自动吸附的，所以要真的移动。
    这里绕着目标格转一圈（上下左右各走 1-2 格）。
    """
    got = 0
    before_total = _inv_total(snapshot())
    spots = [
        (cx, cy + 1), (cx + 1, cy + 1), (cx + 1, cy),
        (cx + 1, cy - 1), (cx, cy - 1), (cx - 1, cy - 1),
        (cx - 1, cy), (cx - 1, cy + 1),
    ]
    for _ in range(rounds):
        for sx, sy in spots:
            d = snapshot()
            if (d.get("debrisCount") or 0) == 0:
                return _inv_total(d) - before_total
            _send({"actionType": "move_to", "x": sx, "y": sy})
            # 给一点时间走一格
            end = time.time() + 2.0
            while time.time() < end:
                time.sleep(0.2)
                d2 = snapshot()
                if not (d2.get("moving")):
                    break
    return _inv_total(snapshot()) - before_total


def _inv_total(d: dict) -> int:
    return sum((i.get("stack") or 0) for i in (d.get("player") or {}).get("inventory") or [])


def _wait_job(action_name: str, before_detail: str, budget: float = 60.0) -> tuple[dict, dict]:
    """等一个"任务型"动作（chop/mine/cut）结束。

    ⚠ 关键：必须先把「开始前那次的 LastDetail」记下来，
      轮询时只认**不等于它**的新消息，否则会立刻命中上一次的
      "done at (71,17)" —— 实测五棵树全都返回同一句旧回执。
    """
    end = time.time() + budget
    last = snapshot()

    while time.time() < end:
        la = last.get("lastAction") or {}
        det = str(la.get("LastDetail") or "")

        # 新消息（和开始前不一样）且带结束标志 → 才算本次结束
        if det and det != before_detail:
            if any(k in det for k in ("done at", "unfinished", "timed out",
                                      "no walkable", "can't reach",
                                      "Out of energy", "No axe", "No pickaxe",
                                      "No scythe", "No watering",
                                      "started at")):
                # "started at" 只是刚开始，还要继续等；但如果同时没有别的标志就别返回
                if "started at" in det and not any(
                        k in det for k in ("done at", "unfinished", "timed out",
                                           "no walkable", "can't reach")):
                    pass
                else:
                    return la, last

        time.sleep(0.3)
        last = snapshot()

    return (last.get("lastAction") or {}), last


def _current_detail() -> str:
    """当前 lastAction.LastDetail（用来区分"本次"和"上次"的任务回执）。"""
    la = snapshot().get("lastAction") or {}
    return str(la.get("LastDetail") or "")


def mine(x: int, y: int, budget: float = 40.0, collect: bool = True) -> dict:
    """挖矿 —— 敲石头/矿点，自动前进到相邻格，完了捡掉落。"""
    before = snapshot()
    inv0 = _inv_counts(before)
    prev = _current_detail()
    _send({"actionType": "mine", "x": int(x), "y": int(y)})

    la, last = _wait_job("mine", prev, budget)
    ok = bool(la.get("LastOk"))

    if collect and ok:
        _collect_debris(x, y, rounds=2)

    last2 = snapshot()
    inv1 = _inv_counts(last2)
    gained = {k: v - inv0.get(k, 0) for k, v in inv1.items() if v > inv0.get(k, 0)}
    return {
        "ok": ok, "verified": ok,
        "target": {"x": x, "y": y},
        "gained": gained,
        "debris_left": last2.get("debrisCount") or 0,
        "detail": la.get("LastDetail"),
    }


def fish(budget: float = 20.0) -> dict:
    """钓鱼 —— 甩竿、等咬钩、提竿进小游戏。

    **只到小游戏开始**：BobberBar 那一步需要节奏点击，
    这一步做不了，需要人接手或者后续加自动玩。
    """
    d0 = snapshot()
    has_rod = any("Rod" in (i.get("name") or "") for i in (d0.get("player") or {}).get("inventory") or [])
    if not has_rod:
        return {
            "ok": False, "verified": False, "reason": "no_rod",
            "detail": "背包里没有钓鱼竿",
            "hint": "先去 Willy 的鱼店买一根（500g），或者让用户帮忙买",
        }

    _send({"actionType": "fish"})
    la, last = _wait_action("fish", budget)
    ok = bool(la.get("LastOk"))
    detail = la.get("LastDetail") or ""
    return {
        "ok": ok, "verified": ok,
        "hooked": "hooked" in detail.lower() or "minigame" in detail.lower(),
        "detail": detail,
        "note": ("鱼上钩了，进入小游戏 —— 那一步需要节奏点击，暂未自动化"
                 if ok and "minigame" in detail.lower() else None),
    }


def cut(x: int, y: int, budget: float = 20.0, collect: bool = True) -> dict:
    """割草/砍灌木（镰刀）。"""
    before = snapshot()
    fiber0 = _count_item(before, "(O)771")
    prev = _current_detail()
    _send({"actionType": "cut", "x": int(x), "y": int(y)})

    la, last = _wait_job("cut", prev, budget)
    ok = bool(la.get("LastOk"))

    if collect and ok:
        _collect_debris(x, y, rounds=2)

    last2 = snapshot()
    fiber1 = _count_item(last2, "(O)771")
    return {
        "ok": ok, "verified": ok,
        "target": {"x": x, "y": y},
        "fiber_gained": fiber1 - fiber0,
        "detail": la.get("LastDetail"),
    }


def water(x: int, y: int, budget: float = 8.0) -> dict:
    """浇水（水壶有水量限制，干了要先在水边补）。"""
    _send({"actionType": "water", "x": int(x), "y": int(y)})
    la, last = _wait_action("water", budget)
    ok = bool(la.get("LastOk"))
    return {
        "ok": ok, "verified": ok,
        "target": {"x": x, "y": y},
        "detail": la.get("LastDetail"),
    }


def pickup(budget: float = 5.0) -> dict:
    """捡身边地上的掉落物。"""
    _send({"actionType": "pickup"})
    la, last = _wait_action("pickup", budget)
    ok = bool(la.get("LastOk"))
    return {"ok": ok, "verified": ok, "detail": la.get("LastDetail")}


def _count_item(d: dict, qualified_id: str) -> int:
    n = 0
    for i in (d.get("player") or {}).get("inventory") or []:
        if i.get("qualifiedId") == qualified_id:
            n += i.get("stack") or 0
    return n


def _inv_counts(d: dict) -> dict:
    out = {}
    for i in (d.get("player") or {}).get("inventory") or []:
        k = i.get("qualifiedId") or i.get("name")
        out[k] = out.get(k, 0) + (i.get("stack") or 0)
    return out


def name_it(name: str = "Cat", budget: float = 3.0) -> dict:
    """处理起名菜单（领养宠物、给动物改名）。

    星露谷领养宠物选 Yes 之后会弹 NamingMenu 让你给猫/狗起名，
    不处理就永久卡住。这里直接设名字并确认。
    """
    d = snapshot()
    m = menu(d)
    if m != "naming":
        return {
            "ok": False, "verified": False, "reason": "not_naming",
            "menu": m,
            "detail": f"当前不是起名菜单（{m or '没有菜单'}）",
        }

    _send({"actionType": "name_it", "name": name})
    la, last = _wait_action("name_it", budget)
    ok = bool(la.get("LastOk"))
    return {
        "ok": ok, "verified": ok,
        "named": name,
        "menu_after": menu(last),
        "detail": la.get("LastDetail"),
    }


def boot() -> dict:
    """开局检查 + 现在最该做的一件事。

    一个人玩最怕"不知道现在该干嘛"。这个把局面压成一句话 + 一个建议动作：
      · 几点、在哪、体力多少
      · 有没有活（树/石头/草/熟作物）
      · 该不该收工
      · 下一步干什么
    """
    d = snapshot()
    if "_error" in d:
        return {"error": d["_error"], "action": "stardew_launch",
                "reason": "游戏没开"}

    p = d.get("player") or {}
    t = d.get("time") or 0
    stamina = p.get("stamina") or 0
    ms = p.get("maxStamina")
    max_st = 270
    if isinstance(ms, list) and ms:
        max_st = ms[0] or 270
    elif isinstance(ms, (int, float)) and ms:
        max_st = ms

    m = menu(d)
    dg = d.get("dialogue") or {}

    # 1) 先清挡路的 UI
    if m == "dialogue" and dg.get("isQuestion"):
        return {
            "action": "stardew_choose",
            "reason": f"{d.get('talker') or '有人'}在问：「{dg.get('fullText')}」",
            "options": [o.get("text") for o in dg.get("options") or []],
            "hint": "选择题要你决定：stardew_choose(option='yes'/'no')",
        }
    if m == "naming":
        return {"action": "stardew_name_it", "reason": "在起名"}
    if m == "dialogue":
        return {"action": "wait",
                "reason": f"正在和 {d.get('talker')} 说话，会自动读完"}
    if m:
        return {"action": "stardew_exit_menu", "reason": f"{m} 菜单开着", "menu": m}

    # 2) 该不该收工
    # ⚠ 星露谷的 timeOfDay 是 HHMM 整数：620=6:20am, 2200=10pm, 2400=午夜
    #   之前写成 t//100 判断，740//100=7 → 被误判成"太晚"（早上7点！）
    #   必须按 HHMM 比较：2200 就是晚上10点，740 就是早上7:40
    if t >= 2400 or t < 600:
        return {"action": "stardew_console", "command": "sleep",
                "reason": f"现在 {t//100}:{t%100:02d}，太晚了，该睡觉存档"}
    if t >= 2200:
        return {"action": "stardew_console", "command": "sleep",
                "reason": f"已经 {t//100} 点，再晚会累倒在半路，建议收工"}
    if t >= 1800 and stamina < max_st * 0.3:
        return {"action": "stardew_console", "command": "sleep",
                "reason": f"{t//100} 点、体力只剩 {stamina:.0f}，差不多可以收工了"}
    if stamina < max_st * 0.15:
        return {"action": "stardew_eat",
                "reason": f"体力只剩 {stamina:.0f}/{max_st:.0f}",
                "hint": "没食物就回屋睡觉"}

    # 3) 找活
    #    在屋里（FarmHouse）通常没树没石头，先出门去农场
    loc = d.get("location") or ""
    if loc in ("FarmHouse", "Cabin") and t < 2100:
        return {
            "action": "stardew_warp",
            "reason": f"在{loc}里没事可干，出门去农场",
            "location": "Farm", "x": 64, "y": 15,
        }

    scan = scan_resources(radius=12)
    counts = scan.get("counts") or {}

    order = [
        ("ready_crops", "stardew_interact", "可以收的作物"),
        ("trees", "stardew_chop", "棵树"),
        ("stones", "stardew_mine", "块石头"),
        ("weeds", "stardew_cut", "处杂草"),
    ]
    for key, action, label in order:
        items = scan.get(key) or []
        if counts.get(key, 0) > 0 and items:
            it = items[0]
            return {
                "action": action,
                "reason": f"附近 {counts[key]} {label}，最近的 ({it['x']},{it['y']}) 距离 {it['dist']}",
                "x": it["x"], "y": it["y"],
            }

    return {
        "action": "stardew_warp",
        "reason": "周围没什么可干的，换个地方",
        "suggest": "Town（找人聊天）或 Farm 别处",
        "now": {"time": t, "location": d.get("location"),
                "tile": player_tile(d), "stamina": stamina},
    }


def day_report() -> dict:
    """今天干了什么的简报（收工前看）。"""
    d = snapshot()
    if "_error" in d:
        return {"error": d["_error"]}
    p = d.get("player") or {}
    return {
        "time": d.get("time"),
        "day": d.get("day"),
        "season": d.get("season"),
        "location": d.get("location"),
        "money": p.get("money"),
        "stamina": p.get("stamina"),
        "resources": {
            "wood": _count_item(d, "(O)388"),
            "stone": _count_item(d, "(O)390"),
            "fiber": _count_item(d, "(O)771"),
            "sap": _count_item(d, "(O)92"),
        },
        "debris_on_ground": d.get("debrisCount") or 0,
        "inventory": _inv_counts(d),
    }


def scan_resources(radius: int = 8) -> dict:
    """扫身边有什么可以干的活：树、石头、杂草、水、成熟作物。

    这是"自主玩"的关键 —— AI 得先知道周围有什么，才能决定砍哪棵。
    """
    d = snapshot()
    pt = player_tile(d)
    if not pt:
        return {"error": "拿不到坐标"}

    tiles = (d.get("surroundings") or {}).get("tiles") or []
    trees, stones, weeds, water, crops, others = [], [], [], [], [], []

    for t in tiles:
        dx = abs(t["x"] - pt[0])
        dy = abs(t["y"] - pt[1])
        if max(dx, dy) > radius:
            continue

        entry = {"x": t["x"], "y": t["y"], "dist": dx + dy}
        terrain = t.get("terrain")
        obj = t.get("obj")

        if terrain == "tree":
            trees.append(entry)
        elif terrain == "grass":
            weeds.append({**entry, "kind": "grass"})
        elif obj in ("Stone", "Weeds", "Twig") or (obj and "Stone" in str(obj)):
            (stones if "Stone" in str(obj) else weeds).append(entry)
        elif t.get("crop") and t.get("cropReady"):
            crops.append({**entry, "crop": t.get("crop")})
        elif t.get("water"):
            water.append(entry)
        elif obj:
            others.append({**entry, "obj": obj, "type": t.get("objType")})

    # 按距离排序，最近的先做
    for lst in (trees, stones, weeds, crops, water, others):
        lst.sort(key=lambda e: e["dist"])

    return {
        "player": {"x": pt[0], "y": pt[1]},
        "location": location(d),
        "radius": radius,
        "trees": trees[:10],
        "stones": stones[:10],
        "weeds": weeds[:10],
        "ready_crops": crops[:10],
        "water": water[:5],
        "other_objects": others[:10],
        "counts": {
            "trees": len(trees), "stones": len(stones), "weeds": len(weeds),
            "ready_crops": len(crops), "water": len(water),
        },
        "hint": (
            "砍树用 stardew_chop(x,y)，挖矿用 stardew_mine(x,y)，"
            "割草用 stardew_cut(x,y)，收作物用 stardew_interact(x,y)，"
            "钓鱼用 stardew_fish()（要有鱼竿且面前是水）"
        ),
    }
def eat(budget: float = 2.5) -> dict:
    """吃背包里第一个能吃的东西。"""
    before = snapshot()
    st0 = (before.get("player") or {}).get("stamina")
    _send({"actionType": "eat"})

    la, last = _wait_action("eat", budget)
    st1 = (last.get("player") or {}).get("stamina")
    ok = bool(la.get("LastOk"))
    return {
        "ok": ok, "verified": ok,
        "stamina_before": st0, "stamina_after": st1,
        "detail": la.get("LastDetail"),
    }


def attack(budget: float = 2.5) -> dict:
    """攻击最近的怪物。没怪物会优雅失败（LastOk=false）。"""
    before = snapshot()
    monsters = (before.get("surroundings") or {}).get("monsters") or []
    _send({"actionType": "attack"})

    la, last = _wait_action("attack", budget)
    ok = bool(la.get("LastOk"))
    return {
        "ok": ok, "verified": ok,
        "monsters_before": len(monsters),
        "detail": la.get("LastDetail"),
    }


def interact(x: int, y: int, budget: float = 2.0) -> dict:
    """与格子交互，并回报前后该格状态差。"""
    before = snapshot()
    cell_before = _tile_at(before, x, y)
    _send({"actionType": "interact", "x": x, "y": y})
    time.sleep(0.4)
    last = snapshot()
    cell_after = _tile_at(last, x, y)
    la = last.get("lastAction") or {}
    return {
        "ok": bool(la.get("LastOk")), "verified": cell_before != cell_after,
        "target": {"x": x, "y": y},
        "before": cell_before, "after": cell_after,
        "detail": ("格子状态有变化" if cell_before != cell_after
                   else "格子状态没变（作物没成熟 / 不是可交互物 / 被挡住）"),
    }


def _tile_at(d: dict, x: int, y: int) -> dict | None:
    for t in ((d.get("surroundings") or {}).get("tiles") or []):
        if int(t.get("x", -999)) == int(x) and int(t.get("y", -999)) == int(y):
            return t
    return None


def _stuck_hint(d: dict) -> str:
    """卡住时给一句可执行的猜测。"""
    hints = []
    if location(d) == "FarmHouse":
        hints.append("人物在屋内屋内 —— 如果刚读档，很可能仍处于睡醒动画/躺在床上的状态，先等 2-3 秒或走到床边外一格")
    if not ((d.get("surroundings") or {}).get("tiles")):
        hints.append("mod 没有上报邻域格子，无法判断前方是否可通行")
    if not hints:
        hints.append("检查是否有对话框/背包/菜单开着；这类 UI 会吞掉移动输入")
    return "；".join(hints)


def recover(budget: float = 4.0) -> dict:
    """尝试从「卡住」里恢复：先小步移动抖一下，再检查是否真动了。"""
    d = snapshot()
    start = player_tile(d)
    if not start:
        return {"ok": False, "detail": "拿不到坐标"}
    # 朝四个方向各试一小步，看哪个方向游戏肯接受
    tried = []
    for dx, dy, name in ((0, 1, "down"), (0, -1, "up"), (1, 0, "right"), (-1, 0, "left")):
        tx, ty = start[0] + dx, start[1] + dy
        _send({"actionType": "move_to", "x": tx, "y": ty})
        got, last = _wait_until(lambda s: player_tile(s) != start, 1.0)
        tried.append({"dir": name, "moved": got, "now": player_tile(last)})
        if got:
            return {"ok": True, "moved_to": player_tile(last), "tried": tried,
                    "detail": f"靠 {name} 方向拜托卡住状态"}
    return {"ok": False, "tried": tried,
            "detail": "四个方向都不动 —— 不是寻路问题，是输入被整体吞掉（躺着/弹窗/暂停菜单）"}


# ------------------------------------------------- 卡住原因的确诊与恢复

def diagnose_stuck() -> dict:
    """确诊「为什么不能动」，用可观测的分层探针。

    探针设计（每条都实测过）：
      L0 菜单探针 —— 模组直接给 menu 字段：
          dialogue  → 只是被搭话，会正常显示并自动读完
          其他菜单  → 需要决策，不能自动关
      L1 face 探针 —— 转向不经过移动管线。facing 变了说明桥接活着、游戏在跑，
          只是**移动**被拦。facing 也不变说明整个输入层被冻住。
      L2 move 探针 —— 四向各走一格，全失败说明移动管线整体被吞。
    """
    d = snapshot()
    if "_error" in d:
        return {"verdict": "bridge_dead", "detail": d["_error"]}

    m = menu(d)
    if m == "dialogue":
        return {
            "verdict": "talking",
            "talker": d.get("talker"),
            "detail": f"正在和 {d.get('talker')} 说话。对话在屏幕上正常显示，"
                      f"模组会自动一页页读完（每句停 1.2 秒），不需要干预。",
            "next_action": "等对话结束，或直接发下一条指令（会被对话挡一下然后继续）",
        }
    if m:
        return {
            "verdict": "menu_open",
            "menu": m,
            "detail": f"'{m}' 菜单开着，挡住移动。这个菜单需要做决定。",
            "next_action": "exit_menu force 强关，或人工处理",
        }

    f0 = facing(d)
    face_ok = face(1 if f0 != 1 else 2, budget=1.0)["verified"]

    if not face_ok:
        return {"verdict": "input_frozen",
                "location": location(d),
                "detail": "转向都没反应 —— 输入层被整体冻住（过场动画 / 睡觉 / 地图切换中）。"
                          "这时发任何指令都会假成功，先等 2-3 秒再说。"}

    r = recover()
    if r["ok"]:
        return {"verdict": "ok", "detail": "本来能走，已经帮你挪开了", "detail_raw": r}

    return {
        "verdict": "move_blocked",
        "location": location(d),
        "tile": player_tile(d),
        "detail": ("转向正常但四向都走不动 —— 周围被家具/墙围住了。"
                   "注意：床头、沙发、箱子上方这类位置会完全卡死。"),
        "next_action": "先 warp 到空地（如 Farm 64,15），再继续行动",
        "worked_probe": "face 探针证明游戏主循环在跑、桥接没死",
    }


def exit_menu(force: bool = False, budget: float = 2.0) -> dict:
    """关掉当前菜单。

    注意：**对话不走这条路** —— 对话由模组 TickDialogue 一页页读完，
    在屏幕上正常显示，不会被抹掉。这里只处理商店/背包这类需要决策的菜单。
    """
    d = snapshot()
    before = menu(d)
    if before is None:
        return {"ok": True, "verified": True, "detail": "当前没有菜单"}

    if before == "dialogue":
        return {
            "ok": False, "verified": False, "reason": "is_dialogue",
            "detail": f"这是对话（{d.get('talker')}），不该用 exit_menu 抹掉。",
            "hint": "对话会自动一页页读完；想插话就等它结束",
        }

    if not force:
        return {
            "ok": False, "verified": False, "reason": "needs_force",
            "menu": before,
            "detail": f"'{before}' 需要做决定，没加 force 不关",
        }

    _send({"actionType": "exit_menu", "force": True})
    got, last = _wait_until(lambda s: menu(s) is None, budget)
    return {
        "ok": got, "verified": got, "menu_before": before, "menu_now": menu(last),
        "detail": f"关掉 '{before}'" if got else f"没能关掉 '{before}'",
    }


def try_exit_menu() -> dict:
    """旧接口保留：尝试用调试命令关菜单（实测对 NPC 对话无效，仅兜底）。"""
    before = snapshot()
    if "_error" in before:
        return {"ok": False, "detail": before["_error"]}
    _send({"actionType": "console", "command": "exitMenu"})
    time.sleep(0.6)
    after = diagnose_stuck()
    return {"ok": after["verdict"] == "ok", "verdict_after": after["verdict"],
            "detail": after["detail"]}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    d = snapshot()
    print(json.dumps({k: v for k, v in d.items() if k != "surroundings"},
                     ensure_ascii=False, indent=1))
    print("neighborhood:", json.dumps(neighborhood(d, 3), ensure_ascii=False, indent=1)[:800])
