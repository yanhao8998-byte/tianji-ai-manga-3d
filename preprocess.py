# -*- coding: utf-8 -*-
"""
《天纪》AI 漫剧 3D 化 —— 第一集预处理脚本
功能：诊断源视频（隔行 / 噪点 / 黑场）→ 去隔行 → 降噪 → 色彩校正 → 降帧 12fps → 产出「引导视频」

用法：
    python preprocess.py                     # 用默认源视频
    python preprocess.py "D:\\某个视频.mp4"   # 指定源视频
"""
import json
import os
import re
import subprocess
import sys
import paths

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = paths.SRC_VIDEO
GUIDE_DIR = paths.GUIDE_DIR
LOG_DIR = paths.LOG_DIR
PREVIEW_DIR = os.path.join(paths.KEYFRAME_DIR, "_预览")

os.makedirs(GUIDE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PREVIEW_DIR, exist_ok=True)

report = []


def say(msg=""):
    print(msg, flush=True)
    report.append(msg)


def find_tool(name):
    """在 _tools 下寻找 ffmpeg/ffprobe，其次 PATH。"""
    tools = os.path.join(ROOT, "_tools")
    if os.path.isdir(tools):
        for d in sorted(os.listdir(tools)):
            cand = os.path.join(tools, d, "bin", name + ".exe")
            if os.path.isfile(cand):
                return cand
    return name  # 交给 PATH


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def run(cmd, desc, timeout=3600, capture=True):
    """执行命令；capture=True 返回 stdout+stderr 文本。"""
    say(f"\n$ {desc}")
    say("  " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            timeout=timeout,
        )
        out = ""
        if capture and p.stdout:
            out = p.stdout.decode("utf-8", errors="replace")
        return p.returncode, out
    except FileNotFoundError:
        return 127, f"[未找到可执行文件] {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "[超时]"


def probe(src):
    code, out = run([FFPROBE, "-v", "error", "-print_format", "json",
                     "-show_streams", "-show_format", src], "ffprobe 读取媒体信息")
    if code != 0:
        say(out[-2000:])
        sys.exit(1)
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fmt = info.get("format", {})
    dur = float(fmt.get("duration", 0) or 0)
    w, h = int(v["width"]), int(v["height"])
    fps_s = v.get("r_frame_rate", "25/1")
    num, _, den = fps_s.partition("/")
    fps = float(num) / float(den or 1)
    say("")
    say("=" * 62)
    say("源视频信息")
    say("=" * 62)
    say(f"  路径      : {src}")
    say(f"  大小      : {int(fmt.get('size', 0)) / 1048576:.1f} MB")
    say(f"  时长      : {int(dur // 60)} 分 {dur % 60:.1f} 秒（{dur:.0f} 秒）")
    say(f"  画面      : {w}×{h}  帧率 {fps:.3f} fps")
    say(f"  视频编码  : {v.get('codec_name')}  profile={v.get('profile')}  pix_fmt={v.get('pix_fmt')}")
    say(f"  色彩空间  : {v.get('color_space')} / {v.get('color_transfer')} / {v.get('color_primaries')}")
    say(f"  场序      : field_order={v.get('field_order')}")
    if a:
        say(f"  音频      : {a.get('codec_name')} {a.get('sample_rate')}Hz {a.get('channels')}ch")
    else:
        say("  音频      : 无")
    return dict(w=w, h=h, fps=fps, dur=dur, has_audio=bool(a), v=v, a=a)


def diagnose_interlace(src, fps):
    """idet 检测 + signalstats YDIF 噪点/隔行指标"""
    say("")
    say("=" * 62)
    say("诊断 1：隔行扫描检测（idet，取样 600 帧）")
    say("=" * 62)
    code, out = run([FFMPEG, "-hide_banner", "-i", src, "-an",
                     "-vf", "idet", "-frames:v", "600", "-f", "null", "-"],
                    "idet 分析前 600 帧")
    tff = bff = prog = undet = 0
    for line in out.splitlines():
        if "Multi frame detection" in line:
            m = re.search(r"TFF:\s*(\d+).*?BFF:\s*(\d+).*?Progressive:\s*(\d+).*?Undetermined:\s*(\d+)", line)
            if m:
                tff, bff, prog, undet = (int(x) for x in m.groups())
    total = tff + bff + prog + undet
    if total:
        say(f"  TFF(顶场优先)={tff}  BFF(底场优先)={bff}  Progressive(逐行)={prog}  未定={undet}")
        say(f"  → 隔行帧占比 {(tff + bff) / total * 100:.1f}%")
    need_yadif = (tff + bff) > total * 0.05 if total else True
    say(f"  → 结论：{'需要去隔行（yadif）' if need_yadif else '基本逐行，可省去去隔行'}")

    say("")
    say("=" * 62)
    say("诊断 2：噪点 / 场差水平（signalstats YDIF，取样 120 帧）")
    say("=" * 62)
    code, out = run([FFMPEG, "-hide_banner", "-i", src, "-an",
                     "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YDIF",
                     "-frames:v", "120", "-f", "null", "-"],
                    "YDIF 采样（数值越大＝噪点/场差异越强）")
    vals = [float(m.group(1)) for m in re.finditer(r"lavfi\.signalstats\.YDIF=([\d.]+)", out)]
    ydif = sum(vals) / len(vals) if vals else float("nan")
    if vals:
        say(f"  样本数={len(vals)}  平均 YDIF={ydif:.2f}  最大={max(vals):.2f}")
        if ydif < 3:
            deny = "轻"
            hq = "2:2:3:3"
        elif ydif < 8:
            deny = "中"
            hq = "4:3:6:4.5"
        else:
            deny = "重"
            hq = "6:4:9:6"
    else:
        deny, hq = "未知", "4:3:6:4.5"
    say(f"  → 噪点等级：{deny}，建议降噪参数 hqdn3d={hq}")
    return need_yadif, ydif, hq


def diagnose_black(src):
    say("")
    say("=" * 62)
    say("诊断 3：黑场 / 静帧检测（blackdetect）")
    say("=" * 62)
    code, out = run([FFMPEG, "-hide_banner", "-i", src, "-an",
                     "-vf", "blackdetect=d=0.5:pix_th=0.10", "-f", "null", "-"],
                    "黑场检测")
    segs = re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)", out)
    if segs:
        say(f"  检出 {len(segs)} 段黑场：")
        for s, e in segs[:8]:
            say(f"    {float(s):.1f}s – {float(e):.1f}s（{float(e) - float(s):.1f}s）")
    else:
        say("  未检出黑场（素材连续性好）")
    return segs


def preprocess(src, need_yadif, hq, out_path):
    say("")
    say("=" * 62)
    say("预处理：去隔行 → 降噪 → 色彩校正 → 缩放 → 降帧 12fps")
    say("=" * 62)
    chain = []
    if need_yadif:
        chain.append("yadif=1")
    chain.append(f"hqdn3d={hq}")
    chain.append("eq=contrast=1.06:saturation=1.10:gamma=1.02")
    chain.append("scale=1440:1080:flags=lanczos")
    chain.append("fps=12")
    vf = ",".join(chain)
    say(f"  滤镜链：{vf}")

    cmd = [FFMPEG, "-hide_banner", "-y", "-i", src,
           "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-g", "24",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", out_path]
    code, out = run(cmd, "编码引导视频（耗时较长，请耐心等）", timeout=14400)
    tail = out.strip().splitlines()[-6:] if out else []
    for line in tail:
        say("  " + line)
    if code != 0:
        say(f"  ✗ 编码失败，退出码 {code}")
        return False
    if os.path.isfile(out_path):
        size = os.path.getsize(out_path) / 1048576
        say(f"  ✓ 已输出 {out_path}（{size:.1f} MB）")
        return True
    say("  ✗ 未找到输出文件")
    return False


def make_preview(guide_path, dur):
    say("")
    say("抽 3 张预览帧（10% / 50% / 90% 位置）供肉眼检查")
    ok = True
    for tag, ratio in (("10pct", 0.10), ("50pct", 0.50), ("90pct", 0.90)):
        ts = dur * ratio
        out = os.path.join(PREVIEW_DIR, f"预览_{tag}.png")
        code, _ = run([FFMPEG, "-hide_banner", "-y", "-ss", f"{ts:.2f}",
                       "-i", guide_path, "-frames:v", "1", out], f"抽帧 @{ts:.0f}s")
        if code != 0 or not os.path.isfile(out):
            ok = False
            say(f"  ✗ {tag} 抽帧失败")
    if ok:
        say(f"  ✓ 预览帧已存到 {PREVIEW_DIR}")
    return ok


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    if not os.path.isfile(src):
        print(f"找不到源视频：{src}")
        sys.exit(1)

    say("《天纪》AI 漫剧 3D 化 · 第一集预处理")
    say(f"ffmpeg : {FFMPEG}")
    say(f"ffprobe: {FFPROBE}")
    say(f"源文件 : {src}")

    info = probe(src)
    need_yadif, ydif, hq = diagnose_interlace(src, info["fps"])
    diagnose_black(src)

    guide = os.path.join(GUIDE_DIR, "guide.mp4")
    ok = preprocess(src, need_yadif, hq, guide)
    if ok:
        make_preview(guide, info["dur"])

    say("")
    say("=" * 62)
    say("完成状态")
    say("=" * 62)
    say(f"  引导视频：{'✓ ' + guide if ok else '✗ 生成失败'}")
    say(f"  预估帧数（12fps）：约 {info['dur'] * 12:.0f} 帧")
    say(f"  建议关键帧数：300–800 张")

    log_path = os.path.join(LOG_DIR, "预处理日志.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"\n完整日志：{log_path}")


if __name__ == "__main__":
    main()
