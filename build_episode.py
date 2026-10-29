# -*- coding: utf-8 -*-
"""
天纪项目 · 整片组装
把风格化关键帧按分镜表组装成完整第一集:
  - 每个镜头段内: 关键帧 Ken Burns 运镜(推/拉/横移轮换) + 0.5s 交叉溶解
  - 输出 1440x1080 @ 12fps
  - 74 段拼接后合入原视频讲解音频(音画对齐)
  - 缺帧的时段自动用黑场补齐保证音画同步

用法:
  python build_episode.py [风格化目录] [输出mp4]
"""
import csv, os, subprocess, sys, glob, re, json, time

PROJ     = r"<项目根>"
FF       = os.path.join(PROJ, "_tools", "ffmpeg-master-latest-win64-gpl", "bin")
FFMPEG   = os.path.join(FF, "ffmpeg.exe")
STORY    = os.path.join(PROJ, "02_镜头分段", "分镜表.csv")
STY_DEF  = os.path.join(PROJ, "04_关键帧_风格化", "_正式输出")
OUT_DEF  = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集_无声.mp4")
SRC_VID  = r"D:\B站视频下载\倪海厦-天纪\【天纪】 倪海厦主讲 24集(全)_P1_第一集(01)_B站_BV1S94y127Sv.mp4"
FINAL    = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集.mp4")
TMP      = os.path.join(PROJ, "06_成片", "_组装临时")

W, H, FPS, FADE = 1440, 1080, 12, 0.5
CW, CH = 1728, 1296          # zoompan 工作尺寸(留运镜余量)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1500:])
    return r


def load_story():
    shots = []
    with open(STORY, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            shots.append({"no": int(row["段号"]), "t0": float(row["起(秒)"]), "t1": float(row["止(秒)"]),
                          "f0": int(row["起帧"]), "f1": int(row["止帧"])})
    return shots


def zoom_expr(i, n):
    """返回 zoompan 的 z/x/y 表达式，按序号轮换运镜方式"""
    if i % 3 == 0:      # 推近
        z = f"1+0.10*on/{n}"; x = f"(iw-iw/zoom)/2"; y = f"(ih-ih/zoom)/2"
    elif i % 3 == 1:    # 拉远
        z = f"1.10-0.10*on/{n}"; x = f"(iw-iw/zoom)/2"; y = f"(ih-ih/zoom)/2"
    else:               # 缓慢横移(带轻推)
        z = "1.07"; x = f"(iw-iw/zoom)*on/{n}"; y = f"(ih-ih/zoom)/2"
    return z, x, y


def build_shot(shot, keys, out_mp4):
    """单个镜头段: N 张关键帧 -> 交叉溶解的一段视频"""
    dur = shot["t1"] - shot["t0"]
    n = len(keys)
    os.makedirs(TMP, exist_ok=True)
    if n == 0:
        run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
             "-i", f"color=c=black:s={W}x{H}:d={dur:.3f}:r={FPS}",
             "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p", out_mp4])
        return dur, 0
    # 每张的时长: 总时长 + (N-1)*FADE 再均分, 使 xfade 链后恰好等于段时长
    d_each = (dur + (n - 1) * FADE) / n
    frames = max(2, round(d_each * FPS))
    real_d = frames / FPS

    inputs, flines = [], []
    for i, kfp in enumerate(keys):
        inputs += ["-i", kfp]
        z, x, y = zoom_expr(i, frames)
        flines.append(
            f"[{i}:v]scale={CW}:{CH}:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},"
            f"format=yuv420p,setsar=1[v{i}]")
    # xfade 链
    if n == 1:
        flines.append(f"[v0]null[out]")
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
                        "-t", f"{total:.3f}", "-r", str(FPS),
                        "-c:v", "libx264", "-preset", "faster", "-crf", "19",
                        "-pix_fmt", "yuv420p", out_mp4],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1500:])
    return total, n


def main():
    sty = sys.argv[1] if len(sys.argv) > 1 else STY_DEF
    out = sys.argv[2] if len(sys.argv) > 2 else OUT_DEF
    if len(sys.argv) > 3:
        global TMP
        TMP = sys.argv[3]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    os.makedirs(TMP, exist_ok=True)

    # 收集已风格化帧
    styled = {}
    for fp in glob.glob(os.path.join(sty, "*.png")):
        m = re.match(r"f(\d+)\.png$", os.path.basename(fp))
        if m:
            styled[int(m.group(1))] = fp
    print(f"已风格化 {len(styled)} 张")

    shots = load_story()
    print(f"分镜 {len(shots)} 段")
    t0 = time.time()
    parts = []
    for s in shots:
        keys = [styled[f] for f in sorted(styled) if s["f0"] <= f < s["f1"]]
        keys = keys or ([styled[f] for f in sorted(styled) if s["f0"] <= f <= s["f1"]])
        out_p = os.path.join(TMP, f"shot{s['no']:02d}.mp4")
        if os.path.isfile(out_p) and os.path.getsize(out_p) > 10000:
            print(f"  段{s['no']:02d} 已存在, 跳过")
            parts.append(out_p)
            continue
        dur, n = build_shot(s, keys, out_p)
        parts.append(out_p)
        el = time.time() - t0
        print(f"  段{s['no']:02d} [{s['t0']:7.1f}-{s['t1']:7.1f}s] {n:2d}帧 → {dur:6.2f}s "
              f"| 已用 {el/60:5.1f}m", flush=True)

    # 拼接
    lst = os.path.join(TMP, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    print("拼接全部片段 ...")
    run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", out])

    # 合入原声
    vd = float(run([os.path.join(FF, "ffprobe.exe"), "-v", "error", "-show_entries",
                    "format=duration", "-of", "csv=p=0", out]).stdout.strip())
    print(f"视频总长 {vd:.1f}s, 合入原声 ...")
    final = out.replace(".mp4", "_带原声.mp4") if out != OUT_DEF else FINAL
    run([FFMPEG, "-y", "-v", "error", "-i", out, "-i", SRC_VID,
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-t", f"{vd:.3f}", "-movflags", "+faststart", final])

    sz = os.path.getsize(final) / 1024**2
    print("=" * 70)
    print(f"✓ 成片 {final}")
    print(f"  {vd:.1f} 秒  {sz:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
