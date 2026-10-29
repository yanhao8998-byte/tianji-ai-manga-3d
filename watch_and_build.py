# -*- coding: utf-8 -*-
"""
守望脚本: 等 style_all.py 全部完成(或状态文件刷新)后自动组装整片。
触发条件(满足其一):
  A) _正式输出 已累计到 EXPECT 张
  B) _style_state.json 的修改时间晚于本脚本启动时间(说明本轮风格化已收尾, 可能含少量失败)
用法: python watch_and_build.py
"""
import os, subprocess, sys, time

PROJ   = r"<项目根>"
OUTDIR = os.path.join(PROJ, "04_关键帧_风格化", "_正式输出")
STATE  = os.path.join(PROJ, "04_关键帧_风格化", "_style_state.json")
PY     = r"%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BUILD  = os.path.join(PROJ, "build_episode.py")
EXPECT = 657
TIMEOUT = 18 * 3600


def count():
    try:
        return len([f for f in os.listdir(OUTDIR) if f.endswith(".png")])
    except OSError:
        return 0


def main():
    start = time.time()
    print(f"守望中: 等待整片风格化完成 (目标 {EXPECT} 张) ...", flush=True)
    last_report = 0
    while True:
        c = count()
        fresh_state = os.path.isfile(STATE) and os.path.getmtime(STATE) > start
        if c >= EXPECT or fresh_state:
            print(f"✓ 触发条件满足 (已风格化 {c} 张, 状态文件{'已刷新' if fresh_state else '未变'})", flush=True)
            break
        if time.time() - start > TIMEOUT:
            print(f"✗ 超时退出 (已风格化 {c} 张)"); sys.exit(2)
        if time.time() - last_report > 900:
            last_report = time.time()
            print(f"  ... 已风格化 {c}/{EXPECT} 张, 等待中", flush=True)
        time.sleep(120)

    print("开始组装整片 ...", flush=True)
    r = subprocess.run([PY, BUILD], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(r.stdout[-5000:])
    if r.returncode != 0:
        print("✗ 组装失败:", r.stderr[-2000:])
        sys.exit(r.returncode)
    print("✓ 全流程完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
