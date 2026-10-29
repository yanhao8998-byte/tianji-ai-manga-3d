# -*- coding: utf-8 -*-
"""
阶段 4 回填对齐工具
====================
用途：把从云端（即梦/Vidu）下载回来的风格化图片，自动对齐到全局帧号。

工作纪律（务必遵守）：
  一批上传 -> 该批下载完成 -> 立即回填对齐 -> 再开始下一批。
  批与批绝不混放，否则顺序对齐会错位。

用法：
  python stage4_align.py            # 对齐 _下载原始/ 里的所有批次 + 全局校验
  python stage4_align.py --check    # 只校验不复制

目录约定：
  03_关键帧_原图/fNNNNNN.png         原始关键帧（帧号 = 真相来源）
  04_关键帧_风格化/_下载原始/第NN批/  你从云端下载的原始压缩包解压到这里
  04_关键帧_风格化/fNNNNNN.png       对齐后的成品（EbSynth 用）
  04_关键帧_风格化/_弃稿/fNNNNNN/    每帧多出的抽卡备选稿放这里
"""
import os
import csv, io, os, shutil, struct, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
S3     = os.path.join(ROOT, "03_关键帧_原图")
S4     = os.path.join(ROOT, "04_关键帧_风格化")
RAW    = os.path.join(S4, "_下载原始")
OK_DIR = S4                       # 对齐成品直接放 S4 根（与原图同名 fNNNNNN.png）
REJ    = os.path.join(S4, "_弃稿")
LOGDIR = os.path.join(ROOT, "logs")

def say(s=""):
    print(s)
    log_lines.append(str(s))

log_lines = []
def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return w, h
    except Exception:
        return None

def load_batches():
    """从分批清单.csv 读 {批名: [帧号...]}"""
    p = os.path.join(S4, "分批清单.csv")
    out = {}
    for r in csv.DictReader(io.open(p, encoding="utf-8-sig")):
        frames = [int(x.strip().lstrip("f")) for x in r["文件名列表"].split()]
        out[r["批次"]] = frames
    return out

def main():
    check_only = "--check" in sys.argv
    batches = load_batches()
    say("=" * 64)
    say("阶段 4 回填对齐  " + ("[仅校验]" if check_only else "[对齐+校验]"))
    say("=" * 64)

    # 1) 对齐各批次
    for bname, frames in batches.items():
        bdir = os.path.join(RAW, bname)
        if not os.path.isdir(bdir):
            continue
        files = [f for f in os.listdir(bdir)
                 if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                 and not f.startswith("_")]
        say(f"\n[{bname}] 期望 {len(frames)} 张 / 下载目录实有 {len(files)} 张")
        if not files:
            say("  （空，跳过）")
            continue
        if len(files) != len(frames):
            say(f"  ✗ 数量不匹配！请确认该批是否完整下载（多传/漏传都先补齐再跑）。未做对齐。")
            say(f"    提示：检查 {bdir}")
            continue
        if check_only:
            continue
        # 按修改时间排序（下载时间序 = 上传顺序序，前提是本批没有混入其他批的文件）
        files.sort(key=lambda f: os.path.getmtime(os.path.join(bdir, f)))
        done = dup = 0
        for fname, fno in zip(files, frames):
            src = os.path.join(bdir, fname)
            w, h = png_size(src) or (0, 0)
            if (w, h) != (1440, 1080):
                say(f"  ⚠ f{fno:06d} <- {fname} 尺寸 {w}x{h} ≠ 1440x1080，仍复制但请留意")
            dst = os.path.join(OK_DIR, f"f{fno:06d}.png")
            if os.path.exists(dst):
                # 已有同名 -> 旧的移入弃稿留底
                dup += 1
                dd = os.path.join(REJ, f"f{fno:06d}")
                os.makedirs(dd, exist_ok=True)
                shutil.move(dst, os.path.join(dd, os.path.basename(dst)))
            shutil.copy2(src, dst)
            done += 1
        say(f"  ✓ 对齐完成 {done} 张（含替换旧稿 {dup} 张，旧稿在 _弃稿）")
        # 对齐完的本批移入 _已对齐 防止重复处理
        fin = os.path.join(RAW, "_已对齐", bname)
        os.makedirs(os.path.dirname(fin), exist_ok=True)
        try:
            shutil.move(bdir, fin)
            say(f"  ✓ 原始下载件已归档到 _下载原始/_已对齐/{bname}")
        except Exception as e:
            say(f"  （归档失败：{e}，不影响对齐结果）")

    # 2) 全局校验
    say("\n" + "=" * 64)
    need = []
    for r in csv.DictReader(io.open(os.path.join(S3, "关键帧清单.csv"), encoding="utf-8-sig")):
        if not r["备注"].startswith("已排除"):
            need.append(int(r["帧号"]))
    have, bad = [], []
    for fno in need:
        p = os.path.join(OK_DIR, f"f{fno:06d}.png")
        if os.path.exists(p):
            sz = png_size(p)
            if sz != (1440, 1080):
                bad.append((fno, sz))
            else:
                have.append(fno)
        else:
            pass
    say(f"可转绘关键帧：{len(need)} 张")
    say(f"已风格化对齐：{len(have)} 张（{len(have)*100//len(need)}%）")
    say(f"待处理：{len(need)-len(have)-len(bad)} 张；尺寸异常：{len(bad)} 张")
    if bad:
        say("  尺寸异常清单：" + ", ".join(f"f{f:06d}({w}x{h})" for f,(w,h) in bad[:20]))
    pct = len(have) * 100 // len(need)
    say("\n进度条：[" + "#" * (pct // 2) + "." * (50 - pct // 2) + f"] {pct}%")

    os.makedirs(LOGDIR, exist_ok=True)
    with io.open(os.path.join(LOGDIR, "对齐报告.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    say(f"\n报告已写入 logs\\对齐报告.txt")

if __name__ == "__main__":
    main()
