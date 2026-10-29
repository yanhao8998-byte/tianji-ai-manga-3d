# -*- coding: utf-8 -*-
"""
《天纪》AI 漫剧 3D 化 —— 阶段 2/3：镜头分段 + 关键帧选取

实测背景：源片是「单一长镜头」（全片仅 1 个硬切点），
因此分段采用「硬切点 + 固定时长分块」的混合策略，
关键帧密度由画面运动量（逐帧差分）自适应决定——静止讲段稀疏、动作段加密。

产出：
  02_镜头分段/分镜表.csv, 分镜表.txt
  03_关键帧_原图/f%06d.png, 关键帧清单.csv, _预览/关键帧总览.jpg
  logs/运动曲线.csv
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
FF = os.path.join(ROOT, "_tools", "ffmpeg-master-latest-win64-gpl", "bin", "ffmpeg.exe")
GUIDE = os.path.join(ROOT, "01_引导视频", "guide.mp4")
SHOT_DIR = os.path.join(ROOT, "02_镜头分段")
KF_DIR = os.path.join(ROOT, "03_关键帧_原图")
LOG_DIR = os.path.join(ROOT, "logs")
PREVIEW_DIR = os.path.join(KF_DIR, "_预览")

FPS = 12
BLOCK_SECONDS = 30          # 长镜头下的分块时长
CUT_THRESHOLD = 0.15        # 硬切阈值（0.3 时全片仅 1 个，故放宽复核）
TARGET_KEYFRAMES = 700      # 关键帧总量目标（成本上限）
MAX_INTERVAL = 48           # 关键帧间隔上限（4 秒）——讲课时口型持续变化，
                            # 传播超过 4 秒容易表情走样
BATCH = 80                  # ffmpeg select 表达式单批上限（645 项会触发内存分配失败）

for d in (SHOT_DIR, KF_DIR, LOG_DIR, PREVIEW_DIR):
    os.makedirs(d, exist_ok=True)

report = []


def say(m=""):
    print(m, flush=True)
    report.append(m)


def run(args, desc, capture=True, binary=False):
    say(f"$ {desc}")
    p = subprocess.run(args, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.STDOUT if (capture and not binary) else subprocess.DEVNULL)
    if binary:
        return p.returncode, p.stdout
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace")


def probe_duration():
    ffp = os.path.join(os.path.dirname(FF), "ffprobe.exe")
    p = subprocess.run([ffp, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", GUIDE], stdout=subprocess.PIPE)
    return float(p.stdout.decode().strip())


# ---------------------------------------------------------------- 阶段 2
def detect_cuts():
    """硬切点检测（ffmpeg select 的 scene 分数）"""
    vf = f"select=gt(scene\\,{CUT_THRESHOLD}),showinfo"
    code, out = run([FF, "-hide_banner", "-i", GUIDE, "-an", "-vf", vf,
                     "-fps_mode", "passthrough", "-f", "null", "-"],
                    f"硬切点检测（scene 阈值 {CUT_THRESHOLD}）")
    times = [float(m) for m in re.findall(r"pts_time:([\d.]+)", out)]
    say(f"  检出硬切点 {len(times)} 个：{[round(t, 1) for t in times[:20]]}")
    return times


def motion_curve(total_frames):
    """流式解码低分辨率灰度帧，逐帧差分得到运动曲线（0–255）"""
    W, H = 96, 72
    size = W * H
    step = 4                      # 抽样 1/4 像素，兼顾速度与精度
    say(f"$ 解码运动曲线（{W}×{H} 灰度，逐帧差分）")
    t0 = time.time()
    p = subprocess.Popen([FF, "-hide_banner", "-i", GUIDE, "-an",
                          "-vf", f"scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    diffs = [0.0]
    prev = None
    idx = 0
    buf_target = size
    while True:
        chunk = p.stdout.read(buf_target * 64)
        if not chunk:
            break
        n = len(chunk) // size
        for i in range(n):
            f = chunk[i * size:(i + 1) * size]
            if prev is not None:
                s = 0
                for k in range(0, size, step):
                    d = f[k] - prev[k]
                    s += d if d >= 0 else -d
                diffs.append(s / (size / step))
            prev = f
            idx += 1
        if idx % 3000 < 64:
            print(f"    ...已处理 {idx} 帧", flush=True)
    p.wait()
    say(f"  完成：{idx} 帧，耗时 {time.time() - t0:.0f}s，"
        f"运动值 mean={sum(diffs) / len(diffs):.2f} max={max(diffs):.1f}")
    return diffs


def build_shots(cut_times, duration, total_frames):
    """硬切点 + 固定时长分块，合并过短段"""
    bounds = [0.0] + [t for t in cut_times] + [duration]
    bounds = sorted(set(bounds))
    # 长镜头兜底：插入固定分块点
    extra = []
    t = BLOCK_SECONDS
    while t < duration:
        if not any(abs(t - b) < 3 for b in bounds):
            extra.append(t)
        t += BLOCK_SECONDS
    bounds = sorted(set(bounds + extra))
    # 合并 <8 秒的段
    merged = [bounds[0]]
    for b in bounds[1:]:
        if b - merged[-1] < 8 and b != duration:
            continue
        merged.append(b)
    shots = []
    for i in range(len(merged) - 1):
        s, e = merged[i], merged[i + 1]
        sf, ef = int(round(s * FPS)), int(round(e * FPS))
        if ef - sf < 2:
            continue
        shots.append(dict(id=len(shots) + 1, start=s, end=e, sf=sf, ef=ef, n=ef - sf))
    say("")
    say(f"分段完成：共 {len(shots)} 段（长镜头按 {BLOCK_SECONDS} 秒分块；"
        f"段长 {(min(x['n'] for x in shots) / FPS):.0f}–{(max(x['n'] for x in shots) / FPS):.0f} 秒）")
    return shots


# ---------------------------------------------------------------- 阶段 3
def pick_keyframes(shots, diffs):
    """按运动量自适应决定关键帧间隔"""
    for s in shots:
        seg = diffs[s["sf"]:s["ef"]] or [0.0]
        seg_sorted = sorted(seg)
        mean = sum(seg) / len(seg)
        p90 = seg_sorted[int(len(seg_sorted) * 0.9)] if seg_sorted else 0.0
        s["motion_mean"] = mean
        s["motion_p90"] = p90
        if mean < 2.5:
            s["level"], s["interval"] = "静止", 36
        elif mean < 6:
            s["level"], s["interval"] = "小幅动作", 18
        else:
            s["level"], s["interval"] = "动作较多", 10
        s["spike"] = max(18.0, p90 * 1.8)   # 突变帧也强制加关键帧

    def plan():
        picks = []
        for s in shots:
            i = s["sf"]
            picks.append(i)
            while i < s["ef"]:
                nxt = i + s["interval"]
                # 若区间内出现突变，提前落点
                for k in range(i + 1, min(nxt, s["ef"])):
                    if diffs[k] >= s["spike"]:
                        nxt = k
                        break
                if nxt >= s["ef"]:
                    break
                picks.append(nxt)
                i = nxt
        return sorted(set(picks))

    picks = plan()
    # 超出预算则整体拉开间隔
    scale = 1.0
    while len(picks) > TARGET_KEYFRAMES and scale < 6:
        scale += 0.35
        for s in shots:
            s["interval"] = int(round(s["interval"] * (1 + 0.35)))
        picks = plan()
    # 间隔封顶：静止段也不让传播超过 MAX_INTERVAL 帧
    for s in shots:
        s["interval"] = min(s["interval"], MAX_INTERVAL)
    picks = plan()
    while len(picks) > TARGET_KEYFRAMES * 1.25:
        for s in shots:
            s["interval"] = min(int(round(s["interval"] * 1.2)), MAX_INTERVAL * 2)
        picks = plan()
        if all(s["interval"] >= MAX_INTERVAL for s in shots):
            break
    picks = [p for p in picks if p < (shots[-1]["ef"] if shots else 0)]
    say("")
    say(f"关键帧规划：{len(picks)} 张"
        f"（目标 {TARGET_KEYFRAMES}，缩放系数 {scale:.2f}，间隔上限 {MAX_INTERVAL} 帧）")
    from collections import Counter
    c = Counter(s["level"] for s in shots)
    say(f"  段落构成：{dict(c)}")
    say(f"  间隔分布：{sorted(set(s['interval'] for s in shots))}")
    return picks


def extract_keyframes(picks):
    """分批抽帧（select 表达式每批 ≤BATCH 项，避免内存分配失败）→ 重命名为全局帧号"""
    tmp = os.path.join(KF_DIR, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    for f in os.listdir(KF_DIR):
        if f.startswith("f") and f.endswith(".png"):
            os.remove(os.path.join(KF_DIR, f))

    batches = [picks[i:i + BATCH] for i in range(0, len(picks), BATCH)]
    say("")
    say(f"$ 分批抽帧：{len(picks)} 张 / 每批 {BATCH} 项 = {len(batches)} 批")
    t0 = time.time()
    written = []
    for bi, batch in enumerate(batches, 1):
        expr = "select=" + "+".join(f"eq(n\\,{p})" for p in batch)
        code, out = run([FF, "-hide_banner", "-y", "-i", GUIDE, "-an",
                         "-vf", expr, "-fps_mode", "passthrough",
                         "-f", "image2", os.path.join(tmp, f"b{bi:02d}_%04d.png")],
                        f"批 {bi}/{len(batches)}（{len(batch)} 张）")
        got = sorted(f for f in os.listdir(tmp) if f.startswith(f"b{bi:02d}_"))
        if len(got) != len(batch):
            say(f"  ⚠ 批 {bi} 输出 {len(got)} 张，期望 {len(batch)} 张")
        for src_name, frame_idx in zip(got, batch):
            dst = os.path.join(KF_DIR, f"f{frame_idx:06d}.png")
            os.replace(os.path.join(tmp, src_name), dst)
            written.append(frame_idx)
    try:
        os.rmdir(tmp)
    except OSError:
        pass
    written.sort()
    say(f"  ✓ 完成 {len(written)} 张，耗时 {time.time() - t0:.0f}s")
    return written


def contact_sheet(frames, cols=8, rows=6):
    """用 concat 解复用器拼一张总览图，便于肉眼检查"""
    n = cols * rows
    sample = frames[::max(1, len(frames) // n)][:n]
    lst = os.path.join(LOG_DIR, "_sheet_list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for fr in sample:
            p = os.path.join(KF_DIR, f"f{fr:06d}.png").replace("\\", "/")
            f.write(f"file '{p}'\nduration 0.1\n")
        p = os.path.join(KF_DIR, f"f{sample[-1]:06d}.png").replace("\\", "/")
        f.write(f"file '{p}'\nduration 0.1\n")
    out = os.path.join(PREVIEW_DIR, "关键帧总览.jpg")
    code, _ = run([FF, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                   "-vf", f"scale=240:-1,tile={cols}x{rows}", "-frames:v", "1", "-q:v", "3", out],
                  f"生成总览图（{len(sample)} 张，{cols}×{rows}）")
    if code == 0 and os.path.isfile(out):
        say(f"  ✓ 总览图：{out}")
    return out


def main():
    say("《天纪》AI 漫剧 3D 化 · 阶段 2/3：镜头分段 + 关键帧选取")
    dur = probe_duration()
    total = int(round(dur * FPS))
    say(f"引导视频：{dur:.1f}s / {FPS}fps / 共 {total} 帧")

    cuts = detect_cuts()
    diffs = motion_curve(total)
    if len(diffs) < total:
        diffs += [diffs[-1]] * (total - len(diffs))

    shots = build_shots(cuts, dur, total)
    picks = pick_keyframes(shots, diffs)
    frames = extract_keyframes(picks)

    # 清单
    csv = os.path.join(SHOT_DIR, "分镜表.csv")
    with open(csv, "w", encoding="utf-8-sig") as f:
        f.write("段号,起(秒),止(秒),起帧,止帧,帧数,运动均值,运动等级,关键帧间隔\n")
        for s in shots:
            f.write(f"{s['id']},{s['start']:.2f},{s['end']:.2f},{s['sf']},{s['ef']},"
                    f"{s['n']},{s['motion_mean']:.2f},{s['level']},{s['interval']}\n")
    kcsv = os.path.join(KF_DIR, "关键帧清单.csv")
    with open(kcsv, "w", encoding="utf-8-sig") as f:
        f.write("帧号,时间(秒),文件名\n")
        for fr in frames:
            f.write(f"{fr},{fr / FPS:.2f},f{fr:06d}.png\n")
    with open(os.path.join(LOG_DIR, "运动曲线.csv"), "w", encoding="utf-8") as f:
        f.write("帧号,运动值\n")
        for i, d in enumerate(diffs):
            f.write(f"{i},{d:.2f}\n")

    txt = os.path.join(SHOT_DIR, "分镜表.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write(f"《天纪》第一集 分镜表（{len(shots)} 段 / 关键帧 {len(frames)} 张）\n")
        f.write("=" * 70 + "\n")
        for s in shots:
            f.write(f"  第{s['id']:>3}段  {s['start']:7.1f}s – {s['end']:7.1f}s  "
                    f"({s['n']:>4}帧)  运动 {s['motion_mean']:5.2f} {s['level']:<6} "
                    f"间隔 {s['interval']:>2}帧\n")
    say("")
    say(f"  分镜表：{csv}")
    say(f"  关键帧清单：{kcsv}")

    if frames:
        contact_sheet(frames)
    say("")
    say("=" * 62)
    say(f"完成：{len(shots)} 段 / {len(frames)} 张关键帧")
    say(f"  云端出图成本预估（0.8–3 元/张）：¥{len(frames) * 0.8:.0f} – ¥{len(frames) * 3:.0f}")
    with open(os.path.join(LOG_DIR, "阶段23日志.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))


if __name__ == "__main__":
    main()
