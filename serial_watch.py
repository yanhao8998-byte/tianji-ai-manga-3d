# -*- coding: utf-8 -*-
"""
串行流水线守望（用户批准"串行化"，替代 watch_and_build2.py）

背景: 原先 style_all_v2.py（风格化）与 face_refine_all.py（脸部精修）并行跑，
      两者抢占同一块显存 → 各自都掉到 250-330s/帧，总时长翻倍。
本脚本把两阶段拆成严格串行，GPU 全程只服务一个阶段：

  阶段A: 等风格化 _正式输出_v2 累计到 657 张（此时 GPU 独占，全速）
  阶段B: 启动 face_refine_all.py 串行精修（全部完成后写 _face_refine_done.json）
  阶段C: 启动 build_episode2.py 用 audio_final.wav(P3n) 组装成片

用法: python serial_watch.py
"""
import os
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.abspath(__file__))
STY = os.path.join(PROJ, "04_关键帧_风格化")
OUTDIR = os.path.join(STY, "_正式输出_v2")
REFINE_FLAG = os.path.join(STY, "_face_refine_done.json")
PY_MINI = os.path.join(os.environ.get("USERPROFILE", ""), ".workbuddy", "binaries", "python", "versions", "3.13.12", "python.exe")
PY_COMFY = r"D:\AI\ComfyUI_windows_portable\python_embeded\python.exe"
LOG = os.path.join(PROJ, "_serial_watch.log")
EXPECT = 657
TIMEOUT = 72 * 3600


def log(msg):
    line = time.strftime("%F %T") + "  " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def count():
    try:
        return len([f for f in os.listdir(OUTDIR) if f.endswith(".png")])
    except OSError:
        return 0


def run_stage(pyexe, script, logname, extra=None):
    lp = os.path.join(PROJ, logname)
    cmd = [pyexe, script] + list(extra or [])
    log(f"[阶段启动] {script} {' '.join(extra or [])}  →  {logname}")
    with open(lp, "a", encoding="utf-8") as lf:
        lf.write("\n" + "=" * 60 + "\n" + time.strftime("%F %T") + " 串行阶段: " + script + "\n")
        p = subprocess.Popen(cmd, cwd=PROJ, stdout=lf, stderr=subprocess.STDOUT)
        rc = p.wait()
    log(f"[阶段结束] {script}  退出码 {rc}")
    return rc


def main():
    log("=" * 60)
    log("串行流水线守望启动（风格化 → 脸部精修 → 组装）")
    start = time.time()
    last = 0

    # ---------- 阶段 A: 风格化 ----------
    while True:
        n = count()
        if n >= EXPECT:
            log(f"✓ 阶段A 完成: 风格化 {n}/{EXPECT} 张")
            break
        if time.time() - start > TIMEOUT:
            log(f"✗ 阶段A 超时退出（已风格化 {n}/{EXPECT}）")
            return 2
        if time.time() - last > 900:
            last = time.time()
            log(f"  阶段A 进行中: 风格化 {n}/{EXPECT} 张（全速独占 GPU）")
        time.sleep(120)

    # ---------- 阶段 B: 脸部精修 ----------
    if os.path.isfile(REFINE_FLAG):
        log("阶段B 已完成过（_face_refine_done.json 存在），跳过")
    else:
        rc = run_stage(PY_COMFY, "face_refine_all.py", "_face_refine_serial.log")
        if rc != 0 and not os.path.isfile(REFINE_FLAG):
            log(f"⚠ 阶段B 退出码 {rc} 且未生成完成标志，仍尝试进入组装")

    # ---------- 阶段 C: 组装 ----------
    out_mp4 = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集v2_无声.mp4")
    tmp_dir = os.path.join(PROJ, "06_成片", "_组装临时v2")
    final_mp4 = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集_画质修复版.mp4")
    run_stage(PY_MINI, "build_episode2.py", "_build_serial.log",
              [OUTDIR, out_mp4, tmp_dir, final_mp4])
    log("✓ 串行全流程结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
