# -*- coding: utf-8 -*-
"""
天纪项目 · 整片组装 v2（画质修复 + 去重影版）

── v2 基线（对应"人物失真、太虚"）────────────────────────────
  1) 运镜工作尺寸按素材实际分辨率自适应: CW/CH = 素材尺寸 × 1.125
     （v1 固定 1728×1296，对 640×480 素材等于先放大 2.7 倍再裁切——"虚"的主因）
  2) zoompan 之后加 unsharp 轻锐化，找回高清重绘后的细节
  3) 运镜幅度 0.10 → 0.06，横移更缓
  4) 音频用处理后的成品音轨 _音频处理/audio_final.wav
  5) x264 crf 18 / preset medium

── 本版新增（对应"人物身后拖多条半透明重影"）────────────────────
  重影的真凶不是段与段的接缝，而是**段内**每张关键帧之间的 0.5s xfade：
  相邻关键帧姿态差异大时，交叉溶解期间两个姿态同时以半透明存在，
  观感就是"一个人身上叠了三四层轮廓"。

  A) 段内去重影：每张关键帧的运镜流在 xfade 之前先过 tmix 运动模糊。
     有了运动模糊打底，溶解窗口里的两份画面会被"抹开"成拖影，
     而不再是两副清晰轮廓叠加。同时段内 FADE 0.5 → 0.75，溶解更缓。
  B) 段间去硬切：74 段原本 concat -c copy 硬拼，现在改成 xfade 交叉溶解。
     74 个输入一次性喂给 ffmpeg 会爆内存/命令行长度，故用**递归归约**：
       第0层 = 74 段 → 每 10 段合成为 1 个中间片
       第1层 = 8 个中间片 → 继续两两 xfade
       直到剩下 1 个。每一层用 ffprobe 量真实时长递推 offset，数学精确，
       不丢帧不重叠。
  C) 修末段黑屏：末张关键帧是 fNNNNNN，若某段起止帧超出素材范围导致取不到帧，
     原逻辑回落到"纯黑帧"。现在改为**夹取到最近的一张可用关键帧**并做慢推，
     片尾不再有约 15s 黑屏。

用法:
  python build_episode2.py [风格化目录] [输出mp4] [临时目录] [最终mp4] [段间溶解秒数]

缓存: 分段小片写在 <临时目录>/v3/ 下（与旧缓存隔离，不会误用旧参数产物）。
"""
import os
import csv, os, subprocess, sys, glob, re, time

PROJ = os.path.dirname(os.path.abspath(__file__))
FF = os.path.join(PROJ, "_tools", "ffmpeg-master-latest-win64-gpl", "bin")
FFMPEG = os.path.join(FF, "ffmpeg.exe")
FFPROBE = os.path.join(FF, "ffprobe.exe")
STORY = os.path.join(PROJ, "02_镜头分段", "分镜表.csv")
STY_DEF = os.path.join(PROJ, "04_关键帧_风格化", "_正式输出")
OUT_DEF = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集v2_无声.mp4")
FINAL = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集_画质修复版.mp4")
AUDIO_DEF = os.path.join(PROJ, "_音频处理", "audio_final.wav")
TMP = os.path.join(PROJ, "06_成片", "_组装临时")

W, H, FPS = 1440, 1080, 12
FADE = 0.75               # 段内关键帧溶解（v2 是 0.5；调缓以配合运动模糊去重影）
ZOOM_MAX = 0.06
HEADROOM = 1.125
SHARPEN = "unsharp=5:5:0.45:5:5:0.0"
MOTION_BLUR = 4          # tmix 帧数：段内溶解的运动模糊强度（1=关）
SEG_FADE = 0.4            # 段与段之间的交叉溶解秒数
XFADE_GROUP = 10          # 归约时每层每组的段数
CACHE_TAG = "v3"          # 分段缓存目录标签，改参数时同步 bump 以免吃到旧产物


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1500:])
    return r


def img_size(fp):
    r = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", fp])
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def dur_of(fp):
    return float(run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", fp]).stdout.strip())


def enc_args():
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(FPS)]


def load_story():
    shots = []
    with open(STORY, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            shots.append({"no": int(row["段号"]), "t0": float(row["起(秒)"]),
                          "t1": float(row["止(秒)"]),
                          "f0": int(row["起帧"]), "f1": int(row["止帧"])})
    return shots


def zoom_expr(i, n):
    if i % 3 == 0:      # 推近
        z = f"1+{ZOOM_MAX}*on/{n}"; x = "(iw-iw/zoom)/2"; y = "(ih-ih/zoom)/2"
    elif i % 3 == 1:    # 拉远
        z = f"1+{ZOOM_MAX}-{ZOOM_MAX}*on/{n}"; x = "(iw-iw/zoom)/2"; y = "(ih-ih/zoom)/2"
    else:               # 缓慢横移
        z = f"1+{ZOOM_MAX * 0.5:.3f}"; x = f"(iw-iw/zoom)*on/{n}"; y = "(ih-ih/zoom)/2"
    return z, x, y


def build_shot(shot, keys, out_mp4, cw, ch):
    dur = shot["t1"] - shot["t0"]
    n = len(keys)
    if n == 0:
        run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
             "-i", f"color=c=black:s={W}x{H}:d={dur:.3f}:r={FPS}"] + enc_args() +
            [out_mp4])
        return dur, 0

    d_each = (dur + (n - 1) * FADE) / n
    frames = max(2, round(d_each * FPS))
    real_d = frames / FPS

    inputs, flines = [], []
    for i, kfp in enumerate(keys):
        inputs += ["-i", kfp]
        z, x, y = zoom_expr(i, frames)
        blur = f",tmix=frames={MOTION_BLUR}" if MOTION_BLUR > 1 else ""
        flines.append(
            f"[{i}:v]scale={cw}:{ch}:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},"
            f"{SHARPEN}{blur},format=yuv420p,setsar=1[v{i}]")
    if n == 1:
        flines.append("[v0]null[out]")
    else:
        offset = real_d - FADE
        flines.append(f"[v0][v1]xfade=transition=fade:duration={FADE}:offset={offset:.3f}[x1]")
        for i in range(2, n):
            offset += real_d - FADE
            flines.append(f"[x{i-1}][v{i}]xfade=transition=fade:duration={FADE}:offset={offset:.3f}[x{i}]")
        flines.append(f"[x{n-1}]null[out]")
    graph = ";".join(flines)
    total = real_d * n - FADE * (n - 1)
    p = subprocess.run([FFMPEG, "-y", "-v", "error"] + inputs +
                       ["-filter_complex", graph, "-map", "[out]",
                        "-t", f"{total:.3f}"] + enc_args() + [out_mp4],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1500:])
    return total, n


def xfade_chain(clips, fade, out_path):
    """把若干同规格小片按 xfade 串成一条。offset 用实测时长递推，精确不丢帧。"""
    inputs, labels = [], []
    for p in clips:
        inputs += ["-i", p]
    durs = [dur_of(p) for p in clips]

    if len(clips) == 1:
        run([FFMPEG, "-y", "-v", "error", "-i", clips[0], "-c", "copy", out_path])
        return durs[0]

    # 相邻两段都很短时，溶解不能超过短段时长的一半，否则吃掉画面
    f = fade
    for i in range(len(durs) - 1):
        f = min(f, durs[i] / 2.0, durs[i + 1] / 2.0)
    f = max(f, 0.08)

    flines = []
    prev = "0:v"
    cum = durs[0]
    for i in range(1, len(clips)):
        offset = cum - f * i
        lbl = f"x{i}" if i < len(clips) - 1 else "out"
        flines.append(f"[{prev}][{i}:v]xfade=transition=fade:"
                      f"duration={f:.3f}:offset={offset:.3f}[{lbl}]")
        prev = lbl
        cum += durs[i]
    total = cum - f * (len(clips) - 1)
    run([FFMPEG, "-y", "-v", "error"] + inputs +
        ["-filter_complex", ";".join(flines), "-map", f"[{prev}]",
         "-t", f"{total:.3f}"] + enc_args() + [out_path])
    return total


def reduce_join(parts, fade, workdir):
    """递归归约：每层把 XFADE_GROUP 个片段合成 1 个，直到只剩 1 个。"""
    level = 0
    cur = list(parts)
    while len(cur) > 1:
        nxt = []
        for gi in range(0, len(cur), XFADE_GROUP):
            grp = cur[gi:gi + XFADE_GROUP]
            if len(grp) == 1:
                nxt.append(grp[0])
                continue
            op = os.path.join(workdir, f"_join_l{level}_{gi // XFADE_GROUP:02d}.mp4")
            if os.path.isfile(op) and os.path.getsize(op) > 10000:
                nxt.append(op)
                continue
            d = xfade_chain(grp, fade, op)
            print(f"  拼接层{level} 组{gi // XFADE_GROUP:02d} "
                  f"{len(grp)} 段 → {d:.2f}s", flush=True)
            nxt.append(op)
        if len(nxt) == len(cur):
            break                      # 全部命中缓存，无法再降层
        cur = nxt
        level += 1
    return cur[0]


def main():
    sty = sys.argv[1] if len(sys.argv) > 1 else STY_DEF
    out = sys.argv[2] if len(sys.argv) > 2 else OUT_DEF
    if len(sys.argv) > 3:
        global TMP
        TMP = sys.argv[3]
    final_path = sys.argv[4] if len(sys.argv) > 4 else FINAL
    seg_fade = float(sys.argv[5]) if len(sys.argv) > 5 else SEG_FADE

    cache = os.path.join(TMP, CACHE_TAG)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    os.makedirs(cache, exist_ok=True)

    styled = {}
    for fp in glob.glob(os.path.join(sty, "*.png")):
        m = re.match(r"f(\d+)\.png$", os.path.basename(fp))
        if m:
            styled[int(m.group(1))] = fp
    print(f"已风格化 {len(styled)} 张")
    if not styled:
        print("✗ 无素材"); return 1

    sample = next(iter(styled.values()))
    sw, sh = img_size(sample)
    cw = max(int(round(sw * HEADROOM / 2) * 2), int(W * 1.05))
    ch = max(int(round(sh * HEADROOM / 2) * 2), int(H * 1.05))
    print(f"素材 {sw}x{sh} → 运镜工作尺寸 {cw}x{ch}（放大 {cw / sw:.2f}x，v1 为 2.70x）")

    shots = load_story()
    print(f"分镜 {len(shots)} 段")
    avail = sorted(styled)
    t0 = time.time()
    parts = []
    n_clamped = 0
    for s in shots:
        keys = [styled[f] for f in avail if s["f0"] <= f < s["f1"]]
        if not keys:
            keys = [styled[f] for f in avail if s["f0"] <= f <= s["f1"]]
        if not keys:
            # 修末段黑屏：整段落在素材范围外时，夹取到最近的一张，
            # 用慢推镜头把这一段撑满，而不是回落到纯黑。
            nearest = min(avail, key=lambda f: abs(f - s["f0"]))
            keys = [styled[nearest]] * 3
            n_clamped += 1
        out_p = os.path.join(cache, f"shot{s['no']:02d}.mp4")
        if os.path.isfile(out_p) and os.path.getsize(out_p) > 10000:
            parts.append(out_p)
            continue
        dur, n = build_shot(s, keys, out_p, cw, ch)
        parts.append(out_p)
        el = time.time() - t0
        print(f"  段{s['no']:02d} [{s['t0']:7.1f}-{s['t1']:7.1f}s] {n:2d}帧 → {dur:6.2f}s "
              f"| 已用 {el/60:5.1f}m", flush=True)
    if n_clamped:
        print(f"注：{n_clamped} 段超出素材范围，已夹取最近关键帧慢推（不再出黑屏）")

    # 段间交叉溶解（替代原 concat -c copy 硬切）
    print(f"段间交叉溶解 {seg_fade}s ...")
    merged = reduce_join(parts, seg_fade, cache)

    run([FFMPEG, "-y", "-v", "error", "-i", merged, "-c", "copy", out])
    vd = dur_of(out)

    if not os.path.isfile(AUDIO_DEF):
        print(f"⚠ 未找到处理后的音轨 {AUDIO_DEF}")
    final = final_path if out != OUT_DEF else FINAL
    print(f"视频总长 {vd:.1f}s, 合入处理后音频 ...")
    run([FFMPEG, "-y", "-v", "error", "-i", out, "-i", AUDIO_DEF,
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
         "-t", f"{vd:.3f}", "-movflags", "+faststart", final])

    sz = os.path.getsize(final) / 1024**2
    print("=" * 70)
    print(f"✓ 成片 {final}")
    print(f"  {vd:.1f} 秒  {sz:.1f} MB")
    print(f"  总耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())