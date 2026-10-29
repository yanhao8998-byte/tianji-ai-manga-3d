# -*- coding: utf-8 -*-
"""
把 SVD 输出的动画片段（webp / png序列）转成 mp4 并拼接成片。
用法:
  python build_video.py [片段目录] [输出mp4] [fps]
"""
import os, sys, glob, subprocess, shutil

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tools/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe")

IN_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "07_视频片段")
OUT_MP4 = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "06_成片/天纪漫剧_SVD样片.mp4")
OUT_FPS = sys.argv[3] if len(sys.argv) > 3 else "14"


def run(args, label):
    r = subprocess.run(args, capture_output=True)
    if r.returncode != 0:
        print(f"   ✗ {label}: {r.stderr.decode('utf-8', 'replace')[-400:]}")
        return False
    return True


def main():
    clips = sorted([p for p in glob.glob(os.path.join(IN_DIR, "*.*"))
                    if os.path.splitext(p)[1].lower() in (".webp", ".png", ".gif", ".mp4")])
    clips = [c for c in clips if not os.path.basename(c).startswith("_")]
    if not clips:
        print(f"✗ {IN_DIR} 下没有片段"); return 1

    work = os.path.join(IN_DIR, "_mp4")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.dirname(OUT_MP4), exist_ok=True)

    print(f"片段 {len(clips)} 个 → 目标帧率 {OUT_FPS}fps\n")
    parts = []
    for i, c in enumerate(clips, 1):
        base = os.path.splitext(os.path.basename(c))[0]
        out = os.path.join(work, f"c{i:03d}_{base}.mp4")
        # 动图 → mp4：按目标帧率重采样，yuv420p 保证兼容
        ok = run([FFMPEG, "-y", "-i", c,
                  "-vf", f"fps={OUT_FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                  "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], base)
        if ok:
            parts.append(out)
            print(f"   ✓ [{i}/{len(clips)}] {base}")

    if not parts:
        print("✗ 无成功转换的片段"); return 1

    # 拼接
    lst = os.path.join(work, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.replace(os.sep, '/')}'\n")
    ok = run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst,
              "-c", "copy", OUT_MP4], "concat")
    if not ok:
        ok = run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                  "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", OUT_MP4], "concat-reencode")
    if not ok:
        print("✗ 拼接失败"); return 1

    sz = os.path.getsize(OUT_MP4) / 1024 / 1024
    dur = subprocess.run([FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
                          "-show_entries", "format=duration", "-of", "csv=p=0", OUT_MP4],
                         capture_output=True).stdout.decode().strip()
    print(f"\n✓ 成片 {OUT_MP4}")
    print(f"   {len(parts)} 段   {float(dur or 0):.1f} 秒   {sz:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
