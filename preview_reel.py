# -*- coding: utf-8 -*-
"""关键帧运镜预览片：把已风格化的静止关键帧做成带推镜+交叉溶解的动态预览。
（纯 ffmpeg，不需要任何模型；作为 SVD 出片前的即时预览）"""
import os, sys, glob, subprocess

FF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tools/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe")
IN_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "04_关键帧_风格化/_本地试跑_CN_IPA")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "06_成片/关键帧运镜预览.mp4")
FPS = 12
SEG = 3.2      # 每段秒数
XF = 0.7       # 交叉溶解秒数

imgs = sorted(glob.glob(os.path.join(IN_DIR, "*.png")))
if not imgs:
    print("✗ 无输入"); sys.exit(1)
n = len(imgs)
print(f"{n} 张图 → 每段 {SEG}s，溶解 {XF}s，预计总长 {n*SEG-(n-1)*XF:.1f}s")

args = [FF, "-y"]
for p in imgs:
    args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{SEG}", "-i", p]

parts = []
for i in range(n):
    # 先放大再推镜，避免糊；奇偶交替推近/拉远
    if i % 2 == 0:
        z = "min(1.0+0.0011*on,1.16)"
    else:
        z = "max(1.16-0.0011*on,1.0)"
    parts.append(
        f"[{i}:v]scale=1920:1440:flags=lanczos,"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s=768x576:fps={FPS},setsar=1,format=yuv420p[v{i}]"
    )

prev = "v0"
for k in range(1, n):
    off = k * (SEG - XF)
    out = f"x{k}" if k < n - 1 else "vout"
    parts.append(f"[{prev}][v{k}]xfade=transition=fade:duration={XF}:offset={off:.2f}[{out}]")
    prev = out

filter_complex = ";".join(parts)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
args += ["-filter_complex", filter_complex, "-map", "[vout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", OUT]

r = subprocess.run(args, capture_output=True)
if r.returncode != 0:
    print("✗ ffmpeg 失败:")
    print(r.stderr.decode("utf-8", "replace")[-1500:])
    sys.exit(1)

dur = subprocess.run([FF.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
                      "-show_entries", "format=duration", "-of", "csv=p=0", OUT],
                     capture_output=True).stdout.decode().strip()
print(f"✓ {OUT}")
print(f"   {float(dur or 0):.1f} 秒   {os.path.getsize(OUT)/1024/1024:.1f} MB")
