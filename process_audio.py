# -*- coding: utf-8 -*-
"""
天纪项目 · 音频专业处理链
把原始讲课录音（B站源）处理成适合发布的成品音轨。

处理顺序:
  1) 48kHz 重采样 + 70Hz 高通（去低频嗡鸣/桌面震动）
  2) RNNoise 神经网络降噪（arnndn，语音专用，比 FFT 降噪干净）
  3) 清晰度 EQ：削 200Hz 浑浊、900Hz 闷声，提 3.2kHz 齿音清晰度
  4) deesser 齿音抑制
  5) 温和动态压缩（讲课人声，3:1，慢启动避免抽气）
  6) 两遍 EBU R128 响度标准化 → -16 LUFS / TP -1.5 dB（B站抖音通用）
  7) 末尾 0.5s 淡出

用法:
  python process_audio.py                      # 处理整轨 → _音频处理/audio_final.wav + 报告
  python process_audio.py --remux 视频.mp4 输出.mp4   # 处理并合入视频
  python process_audio.py --ab 60              # 生成 60 秒 原声/处理后 A-B 对比片

依赖: ffmpeg + rnnoise 模型 (_tools/rnnoise/*.rnnn)
"""
import os
import argparse, json, os, re, subprocess, sys, time

PROJ = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(PROJ, "_tools", "ffmpeg-master-latest-win64-gpl", "bin")
FFMPEG = os.path.join(BIN, "ffmpeg.exe")
FFPROBE = os.path.join(BIN, "ffprobe.exe")
RN_DIR = os.path.join(PROJ, "_tools", "rnnoise")      # 作为 cwd，避免路径里的盘符冒号
SRC_DEF = (r"D:\B站视频下载\倪海厦-天纪"
           r"\【天纪】 倪海厦主讲 24集(全)_P1_第一集(01)_B站_BV1S94y127Sv.mp4")
WORK = os.path.join(PROJ, "_音频处理")

MODEL = "sh.rnnn"          # rnnoise 语音模型（sh=speech）
TARGET_I, TARGET_TP, TARGET_LRA = -16.0, -1.5, 11.0


def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败 rc={r.returncode}\n{' '.join(cmd[:6])}...\n{r.stderr[-2000:]}")
    return r


def ffdur(path):
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def measure(path, ss=None, t=None):
    """返回 (I, LRA, TruePeak)"""
    cmd = [FFMPEG, "-hide_banner", "-nostats"]
    if ss is not None:
        cmd += ["-ss", str(ss)]
    if t is not None:
        cmd += ["-t", str(t)]
    cmd += ["-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"]
    out = run(cmd).stderr
    def grab(pat):
        m = re.findall(pat, out)
        return float(m[-1]) if m else None
    I = grab(r"I:\s*(-?\d+\.\d+)\s*LUFS")
    lra = grab(r"LRA:\s*(-?\d+\.\d+)\s*LU")
    tp = grab(r"Peak:\s*(-?\d+\.\d+)\s*dBFS")
    return I, lra, tp


def denoise_chain(model=MODEL):
    return (f"aresample=48000,highpass=f=70,"
            f"arnndn=m={model}:mix=0.9,"
            f"equalizer=f=200:width_type=q:w=1:g=-2,"
            f"equalizer=f=900:width_type=q:w=1.2:g=-1.5,"
            f"equalizer=f=3200:width_type=q:w=1.5:g=2.5,"
            f"deesser=i=0.4:m=0.5:f=0.5,"
            f"acompressor=threshold=-20dB:ratio=3:attack=6:release=180:makeup=3")


def pass_measure_loudnorm(wav):
    """第二遍：测量降噪后素材的响度参数"""
    af = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json")
    out = run([FFMPEG, "-hide_banner", "-nostats", "-i", wav, "-af", af,
               "-f", "null", "-"]).stderr
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", out, re.S)
    if not m:
        raise RuntimeError("loudnorm 测量失败:\n" + out[-1500:])
    return json.loads(m.group(0))


def process(src, out_wav, model=MODEL, dur=None, verbose=True):
    os.makedirs(WORK, exist_ok=True)
    t0 = time.time()
    src_dur = ffdur(src)
    dur = dur or src_dur

    # --- 原始测量 ---
    if verbose:
        print("=" * 74)
        print(f"源文件 {os.path.basename(src)}")
        print(f"  时长 {src_dur:.1f}s")
    i0, l0, p0 = measure(src)
    if verbose:
        print(f"  【原始】响度 {i0:.1f} LUFS | 动态范围 {l0:.1f} LU | 真峰 {p0:.1f} dBFS")

    # --- 第 1 遍：降噪 / EQ / 压缩 ---
    mid = os.path.join(WORK, "_denoised.wav")
    if verbose:
        print("\n[1/3] 降噪 + 清晰度EQ + 压缩 ...")
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y", "-i", src,
         "-af", denoise_chain(model), "-t", f"{dur:.3f}",
         "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", mid], cwd=RN_DIR)
    im, lm, pm = measure(mid)
    if verbose:
        print(f"      降噪后 响度 {im:.1f} LUFS（处理前 {i0:.1f}）")

    # --- 第 2 遍：测量 loudnorm 参数 ---
    if verbose:
        print("\n[2/3] 测量响度标准化参数 ...")
    ms = pass_measure_loudnorm(mid)
    if verbose:
        print(f"      measured_I={ms['input_i']}  TP={ms['input_tp']}  LRA={ms['input_lra']}  "
              f"thresh={ms['input_thresh']}")

    # --- 第 3 遍：线性应用 + 淡出 ---
    if verbose:
        print("\n[3/3] 线性响度标准化 → 成品音轨 ...")
    ln = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:"
          f"measured_I={ms['input_i']}:measured_TP={ms['input_tp']}:"
          f"measured_LRA={ms['input_lra']}:measured_thresh={ms['input_thresh']}:"
          f"offset={ms['target_offset']}:linear=true:print_format=summary,"
          f"afade=t=out:st={max(0.0, dur - 0.5):.3f}:d=0.5")
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y", "-i", mid, "-af", ln,
         "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", out_wav])

    i1, l1, p1 = measure(out_wav)
    if verbose:
        print("\n" + "=" * 74)
        print(f"【成品】响度 {i1:.1f} LUFS | 动态范围 {l1:.1f} LU | 真峰 {p1:.1f} dBFS")
        print(f"  响度提升 {i1 - i0:+.1f} dB   用时 {time.time() - t0:.0f}s")
        print(f"→ {out_wav}")
    return {"before": {"I": i0, "LRA": l0, "TP": p0}, "mid": {"I": im},
            "after": {"I": i1, "LRA": l1, "TP": p1}, "src": src, "dur": src_dur}


def remux(video, audio, out):
    """把处理后的音轨合入视频（视频流直接 copy，无声）"""
    vd = ffdur(video)
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y",
         "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
         "-t", f"{vd:.3f}", "-movflags", "+faststart", out])
    return vd


def make_ab(src, out_mp4, dur=60, ss=300, model=MODEL):
    """生成 A/B 对比片：前半原声，后半处理后"""
    os.makedirs(WORK, exist_ok=True)
    print(f"生成 A/B 对比片（{dur}s 原声 + {dur}s 处理后）...")
    tmp = os.path.join(WORK, "_ab_proc.wav")
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y", "-ss", str(ss), "-t", str(dur),
         "-i", src, "-af", denoise_chain(model), "-ar", "48000", "-ac", "2",
         "-c:a", "pcm_s16le", tmp], cwd=RN_DIR)
    md = pass_measure_loudnorm(tmp)
    ln = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:"
          f"measured_I={md['input_i']}:measured_TP={md['input_tp']}:"
          f"measured_LRA={md['input_lra']}:measured_thresh={md['input_thresh']}:"
          f"offset={md['target_offset']}:linear=true")
    tmp2 = os.path.join(WORK, "_ab_norm.wav")
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y", "-i", tmp, "-af", ln,
         "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", tmp2])
    parts = os.path.join(WORK, "_ab_parts.txt")
    with open(parts, "w", encoding="utf-8") as f:
        f.write(f"file '{src.replace(chr(92), '/')}'\n")
    # 用 filter 拼接音轨 + 简单画面
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y",
         "-ss", str(ss), "-t", str(dur), "-i", src,
         "-i", tmp2,
         "-filter_complex",
         f"[0:a][1:a]concat=n=2:v=0:a=1[a];"
         f"color=c=0x101418:s=1280x720:r=12:d={dur * 2}"
         f",drawtext=text='BEFORE 原声':fontcolor=white:fontsize=42:x=40:y=40:enable='lt(t\\,{dur})'"
         f",drawtext=text='AFTER 处理后':fontcolor=0x7CE38B:fontsize=42:x=40:y=40:enable='gte(t\\,{dur})'[v]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "faster",
         "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
         "-shortest", "-movflags", "+faststart", out_mp4])
    print(f"→ {out_mp4}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEF)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--out", default=os.path.join(WORK, "audio_final.wav"))
    ap.add_argument("--remux", nargs=2, metavar=("视频", "输出"))
    ap.add_argument("--ab", type=int, default=0, help="秒数；生成 A/B 对比片")
    ap.add_argument("--ss", type=int, default=300)
    a = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)
    if a.ab:
        make_ab(a.src, os.path.join(WORK, "音频A_B对比.mp4"), a.ab, a.ss, a.model)
        return 0

    rep = process(a.src, a.out, a.model)
    with open(os.path.join(WORK, "audio_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)

    if a.remux:
        print(f"\n合入视频: {os.path.basename(a.remux[1])}")
        vd = remux(a.remux[0], a.out, a.remux[1])
        print(f"✓ {a.remux[1]}  ({vd:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
