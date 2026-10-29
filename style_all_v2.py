# -*- coding: utf-8 -*-
"""
整片风格化 v2 批量驱动（画质修复版）
配置: 896×672 · steps30 · cfg5.0 · denoise0.60 · 深度0.40(至0.6) · 姿态0.55(至0.85) · IPA0.90(至0.9)

特性: 断点续跑 / 失败重试 / 进度+ETA / 完成后写状态文件（供守望脚本触发）
必须在 ComfyUI python_embeded 下运行。
用法: python style_all_v2.py [--limit N] [--out 目录]
"""
import os
import argparse, glob, os, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_style2 as ls2

PROJ = os.path.dirname(os.path.abspath(__file__))
IN_DEF = os.path.join(PROJ, "03_关键帧_原图")
OUT_DEF = os.path.join(PROJ, "04_关键帧_风格化", "_正式输出_v2")
STATE = os.path.join(PROJ, "04_关键帧_风格化", "_style_v2_state.json")
EXPECT = 657


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT_DEF)
    ap.add_argument("--in", dest="inp", default=IN_DEF)
    a = ap.parse_args()
    o = argparse.Namespace(
        w=896, h=672, steps=30, cfg=5.0, denoise=0.78, pose_res=704,
        sd=0.40, sd_end=0.60, sp=0.55, sp_end=0.85, iw=1.00, ipa_end=1.00,
        bw=0.90,
        hires=0.0, hires_denoise=0.30, hires_steps=12, hires_cfg=5.0,
        hires_sd=0.45, hires_sp=0.40, hires_iw=0.75,
        face=False, face_size=512, face_denoise=0.35, face_steps=20,
        face_cfg=5.0, face_weight=0.9, seed=20261013, save_base=False,
        inp=None, out=None)

    os.makedirs(a.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(a.inp, "*.png")))
    if a.limit:
        files = files[:a.limit]
    todo = [f for f in files
            if not os.path.isfile(os.path.join(a.out, os.path.basename(f)))
            or os.path.getsize(os.path.join(a.out, os.path.basename(f))) < 20000]

    print("=" * 74)
    print(f"整片风格化 v3  896x672  30步 cfg5.0 d0.78  sd0.40(→0.6) sp0.55(→0.85) 脸IPA1.00 全身IPA0.90（月白道袍）")
    print(f"待跑 {len(todo)} / {len(files)} 张   →  {a.out}")
    print("=" * 74, flush=True)
    if not todo:
        print("✓ 全部已完成")
    else:
        root = os.path.join(ls2.CIN, ls2.SUB)
        os.makedirs(root, exist_ok=True)
        ref_name = "玄鹤先生_定妆参考.png"
        shutil.copy2(ls2.REF, os.path.join(root, ref_name))
        # 清除残留状态文件（防止守望脚本误判）
        if os.path.isfile(STATE):
            try:
                os.remove(STATE)
            except OSError:
                pass

        t0 = time.time()
        ok = 0
        fails = []
        for i, fp in enumerate(todo, 1):
            base = os.path.basename(fp)
            shutil.copy2(fp, os.path.join(root, base))
            o.out = os.path.join(a.out, base)
            succ = False
            dt = 0
            for _ in (1, 2):
                g = ls2.build(f"{ls2.SUB}/{base}", f"{ls2.SUB}/{ref_name}", o)
                succ, dt = ls2.run_graph(g, o.out)
                if succ:
                    break
                time.sleep(3)
            ok += succ
            if not succ:
                fails.append(base)
            el = time.time() - t0
            avg = el / i
            print(f"[{i}/{len(todo)}] {base} {'✓' if succ else '✗'} {dt:5.0f}s  "
                  f"| 均 {avg:5.1f}s 已用 {el/60:6.1f}m 剩余约 {avg*(len(todo)-i)/60:5.1f}m", flush=True)

    total = len([f for f in os.listdir(a.out) if f.endswith(".png")])
    with open(STATE, "w", encoding="utf-8") as f:
        import json
        json.dump({"total": total, "expect": EXPECT,
                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    print("=" * 74)
    print(f"✓ 目录累计 {total} 张（目标 {EXPECT}）→ {a.out}")
    return 0 if total >= EXPECT else 2


if __name__ == "__main__":
    sys.exit(main())
