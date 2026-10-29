# -*- coding: utf-8 -*-
"""
守望脚本 v2: 等 style_all_v2.py 完成后自动用 build_episode2.py 组装画质修复版。
触发条件(须同时满足):
  A) _正式输出_v2 已累计到 657 张（或 _style_v2_state.json 刷新）
  B) _face_refine_done.json 已出现（脸部精修全部完成）
用法: python watch_and_build2.py
"""
import os, subprocess, sys, time

PROJ = os.path.dirname(os.path.abspath(__file__))
STY = os.path.join(PROJ, "04_关键帧_风格化")
OUTDIR = os.path.join(STY, "_正式输出_v2")
STATE = os.path.join(STY, "_style_v2_state.json")
REFINE_FLAG = os.path.join(STY, "_face_refine_done.json")
PY = os.path.join(os.environ.get("USERPROFILE", ""), ".workbuddy", "binaries", "python", "versions", "3.13.12", "python.exe")
BUILD = os.path.join(PROJ, "build_episode2.py")
EXPECT = 657
TIMEOUT = 40 * 3600


def count():
    try:
        return len([f for f in os.listdir(OUTDIR) if f.endswith(".png")])
    except OSError:
        return 0


def main():
    start = time.time()
    print(f"守望中: 等待画质版风格化完成 (目标 {EXPECT} 张) ...", flush=True)
    last_report = 0
    while True:
        c = count()
        fresh_state = os.path.isfile(STATE) and os.path.getmtime(STATE) > start
        styled_done = c >= EXPECT or fresh_state
        refined = os.path.isfile(REFINE_FLAG)
        if styled_done and refined:
            print(f"✓ 触发条件满足 (已风格化 {c} 张, 脸部精修完成)", flush=True)
            break
        if time.time() - start > TIMEOUT:
            print(f"✗ 超时退出 (已风格化 {c} 张, 精修完成: {refined})")
            sys.exit(2)
        if time.time() - last_report > 900:
            last_report = time.time()
            print(f"  ... 已风格化 {c}/{EXPECT} 张, 精修{'已完成' if refined else '进行中'}, 等待中", flush=True)
        time.sleep(120)

    print("开始组装画质修复版整片 ...", flush=True)
    r = subprocess.run(
        [PY, BUILD,
         OUTDIR,
         os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集v2_无声.mp4"),
         os.path.join(PROJ, "06_成片", "_组装临时v2"),
         os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集_画质修复版.mp4")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout[-5000:])
    if r.returncode != 0:
        print("✗ 组装失败:", r.stderr[-2000:])
        sys.exit(r.returncode)
    print("✓ 全流程完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
