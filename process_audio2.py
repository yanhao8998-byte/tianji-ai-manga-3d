# -*- coding: utf-8 -*-
"""
天纪项目 · 音频「修仙版」处理链
把讲课原声塑造成修仙老者的嗓音，并与原声明显区别开。

链路:
  A 净化  48kHz 重采样 / 65Hz 高通 / RNNoise 降噪 / 去浑浊 / 齿音抑制
  B 变声  rubberband 降调(pitch<1) + 共振峰下移(formant=shifted) + 平滑瞬态
          → 声线更低、更沉，音色与原讲者明显不同
  C 音色  lowshelf 补胸腔厚度 / 350~400Hz 去闷箱音 / 7.5kHz 收暗去毛刺 / 2.6kHz 微提清晰
  D 空间  afir 卷积混响（自合成 2s 殿堂 IR，含 28ms 预延迟 + 早期反射 + 低通扩散）
          → 尾韵悠扬，带洞府/大殿空间感
  E 响度  两遍 EBU R128 线性标准化 → -16 LUFS / TP -1.5 dBFS

用法:
  python process_audio2.py --make-ir                 # 仅生成混响 IR
  python process_audio2.py --ab 45                   # 三段对比片：原声 / 修仙A / 修仙B
  python process_audio2.py --preset A                # 全片处理 → audio_final.wav
  python process_audio2.py --remux 视频.mp4 输出.mp4  # 处理并合入视频

依赖: ffmpeg(librubberband + afir + arnndn) + _tools/rnnoise/sh.rnnn
"""
import os
import argparse, json, math, os, random, re, struct, subprocess, sys, time, wave
import paths

PROJ = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(PROJ, "_tools", "ffmpeg-master-latest-win64-gpl", "bin")
FFMPEG = os.path.join(BIN, "ffmpeg.exe")
FFPROBE = os.path.join(BIN, "ffprobe.exe")
RN_DIR = paths.RN_DIR          # cwd：sh.rnnn 与 IR 都用相对名，避开盘符冒号
WORK = paths.AUDIO_DIR
SRC_DEF = paths.SRC_VIDEO

MODEL = "sh.rnnn"
IR_NAME = "ir_hall.wav"          # 相对 RN_DIR（旧·大混响）
IR_CLEAR = "ir_room.wav"         # 相对 RN_DIR（紧凑房间感，通透版用）
IR_SHORT = "ir_tight.wav"        # 相对 RN_DIR（0.45s 短尾，近干版用）
IR_CLEAN = "ir_clean.wav"        # 相对 RN_DIR（0.22s 极短尾，人声突出用）
IR_DRY   = "ir_dry.wav"          # 相对 RN_DIR（0.10s 近乎干声，混响再降用）
IR_PATH = os.path.join(RN_DIR, IR_NAME)
TARGET_I, TARGET_TP, TARGET_LRA = -16.0, -1.5, 11.0

# 预设：pitch 越小越低沉；wet 越大余韵越多；profile=clear 通透 / dark 厚重
PRESETS = {
    "T":  dict(pitch=0.92, wet=0.12, dry=0.95, formant="shifted", profile="clear",
               ir=IR_CLEAR, tag="修仙通透版（推荐）"),
    "T2": dict(pitch=0.93, wet=0.07, dry=0.96, formant="shifted", profile="clear",
               ir=IR_CLEAR, tag="通透·更干更亮"),
    "D":  dict(pitch=0.92, wet=0.06, dry=0.97, formant="shifted", profile="clear",
               ir=IR_SHORT, tag="近干·纯人声（无回声）"),
    "D2": dict(pitch=0.92, wet=0.025, dry=0.98, formant="shifted", profile="clear2",
               ir=IR_SHORT, tag="超净清晰版", hp=105, deess=0.46),
    "D3": dict(pitch=0.92, wet=0.025, dry=0.98, formant="shifted", profile="clear",
               ir=IR_SHORT, tag="近干D音色·混响再降", hp=100, deess=0.40),
    "D2f": dict(pitch=0.92, wet=0.025, dry=0.98, formant="preserved", profile="clear2",
                ir=IR_SHORT, tag="超净·字音自然版", hp=105, deess=0.46),
    "D4": dict(pitch=0.92, wet=0.02, dry=0.985, formant="shifted", profile="clear3",
               ir=IR_CLEAN, tag="极致人声·低混响低噪（低沉）", hp=112, deess=0.50, gate=True),
    "D4f": dict(pitch=0.92, wet=0.02, dry=0.985, formant="preserved", profile="clear3",
                ir=IR_CLEAN, tag="极致人声·低混响低噪（字音自然）", hp=112, deess=0.50, gate=True),
    # ---- 老者音色档（针对"白发白须老者"形象，压低音高+共振峰下移）----
    "P1": dict(pitch=0.87, wet=0.02, dry=0.985, formant="shifted", profile="elder",
               ir=IR_CLEAN, tag="仙风老者·清晰平衡", hp=105, deess=0.46, gate=True),
    "P2": dict(pitch=0.84, wet=0.02, dry=0.985, formant="shifted", profile="elder2",
               ir=IR_CLEAN, tag="仙风老者·更老更沉", hp=105, deess=0.46, gate=True),
    "P3": dict(pitch=0.87, wet=0.02, dry=0.985, formant="preserved", profile="elder",
               ir=IR_CLEAN, tag="老者·字音最清楚", hp=105, deess=0.46, gate=True),
    "P3n": dict(pitch=0.87, wet=0.009, dry=0.993, formant="preserved", profile="elder",
                ir=IR_DRY, tag="老者·字音清楚＋混响再降", hp=105, deess=0.46, gate=True),
    "P3n2": dict(pitch=0.87, wet=0.004, dry=0.996, formant="preserved", profile="elder",
                 ir=IR_DRY, tag="老者·字音清楚＋几乎无混响", hp=105, deess=0.46, gate=True),
    "A":  dict(pitch=0.92, wet=0.28, dry=0.88, formant="shifted", profile="dark",
               ir=IR_NAME, tag="旧A 厚重"),
    "B":  dict(pitch=0.90, wet=0.46, dry=0.85, formant="shifted", profile="dark",
               ir=IR_NAME, tag="旧B 深悠扬"),
    "C":  dict(pitch=0.94, wet=0.24, dry=0.88, formant="preserved", profile="dark",
               ir=IR_NAME, tag="旧C 自然厚声"),
}


# ---------------------------------------------------------------- 基础工具
def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败 rc={r.returncode}\n{' '.join(cmd[:8])}...\n{r.stderr[-2500:]}")
    return r


def ffdur(path):
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def measure(path, ss=None, t=None):
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
    return (grab(r"I:\s*(-?\d+\.\d+)\s*LUFS"),
            grab(r"LRA:\s*(-?\d+\.\d+)\s*LU"),
            grab(r"Peak:\s*(-?\d+\.\d+)\s*dBFS"))


def zcr(path, ss=0, t=10):
    """零交叉率（客观校验：降调后 ZCR 应下降）"""
    r = run([FFMPEG, "-hide_banner", "-nostats", "-ss", str(ss), "-t", str(t),
             "-i", path, "-af", "astats=metadata=1:reset=0", "-f", "null", "-"])
    m = re.findall(r"Zero crossings rate:\s*([\d.]+)", r.stderr)
    return float(m[-1]) if m else None


# ---------------------------------------------------------------- 合成 IR
def make_ir(path=IR_PATH, sr=48000, rt60=1.9, predelay_ms=28,
            lp_hz=6000.0, hp_hz=150.0, seed=7, er_scale=1.0):
    """合成混响 IR：预延迟 + 早期反射(er_scale 控制强弱) + 指数衰减扩散噪声"""
    pre = int(sr * predelay_ms / 1000)
    n = int(sr * rt60 * 1.15)
    er = tuple((t, g * er_scale) for t, g in
               ((11, 0.50), (17, 0.42), (23, 0.34), (31, 0.28),
                (41, 0.22), (53, 0.17), (67, 0.12), (83, 0.09)))
    rnd = random.Random(seed)
    chans = []
    for c in range(2):
        buf = [0.0] * (pre + n)
        for t_ms, g in er:
            i = pre + int(sr * t_ms / 1000)
            if i < len(buf):
                jitter = 1 if (c == 0 or rnd.random() < 0.5) else -1
                buf[i] += g * jitter
        for i in range(n):
            env = math.exp(-6.9078 * (i / sr) / rt60)      # -60dB @ rt60
            buf[pre + i] += rnd.uniform(-1.0, 1.0) * env * 0.85
        # 一阶低通（去高频毛刺）
        a = math.exp(-2 * math.pi * lp_hz / sr)
        y = 0.0
        for i in range(len(buf)):
            y = (1 - a) * buf[i] + a * y
            buf[i] = y
        # 一阶高通（去低频轰鸣）
        a2 = math.exp(-2 * math.pi * hp_hz / sr)
        yh = 0.0
        xp = 0.0
        for i in range(len(buf)):
            x = buf[i]
            yh = x - xp + a2 * yh
            xp = x
            buf[i] = yh
        # 单位能量归一化：sum(h^2)=1 → 卷积后整体能量守恒，干湿比可控
        e = math.sqrt(sum(v * v for v in buf)) or 1.0
        chans.append([v / e for v in buf])
    nch = len(chans[0])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(nch):
            frames += struct.pack("<hh", int(max(-1, min(1, chans[0][i])) * 32767),
                                  int(max(-1, min(1, chans[1][i])) * 32767))
        w.writeframes(bytes(frames))
    print(f"✓ 混响 IR → {path}  ({rt60}s 衰减 / {predelay_ms}ms 预延迟 / {nch / sr:.2f}s)")
    return path


# ---------------------------------------------------------------- 各段滤镜
def clean_chain(model=MODEL, hp=90, deess=0.38, gate=False):
    """净化：高通 + 强 RNNoise + 残留底噪 FFT 抑制 +（可选）噪声门 + 去浑浊 + 齿音
    gate=True 时追加轻噪声门，把停顿处的残余底噪压到听不见（门限取 -45dB，只吃真空档，
    不碰语句尾音），这是"降低噪音"最直接的一环。"""
    s = (f"aresample=48000,highpass=f={hp},"
         f"arnndn=m={model}:mix=1.0,"
         f"afftdn=nr=10:nf=-30:tn=1,")
    if gate:
        s += "agate=threshold=0.0056:ratio=1.6:range=0.05:attack=15:release=300,"
    s += (f"equalizer=f=250:width_type=q:w=1.1:g=-3.4,"
          f"deesser=i={deess}:m=0.5:f=0.5")
    return s


def voice_chain(pitch=0.92, formant="shifted", profile="clear"):
    """变声 + 音色塑形
    profile=clear  通透：人声前凸、咬字明亮
    profile=clear2 超净清晰：更狠去糊 + 更强咬字/齿音清晰度
    profile=dark   厚重：胸腔更厚、高频收暗
    """
    fm = "shifted" if formant == "shifted" else "preserved"
    rb = (f"rubberband=pitch={pitch}:formant={fm}:transients=smooth:"
          f"detector=soft:window=long:smoothing=on:pitchq=quality,")
    if profile == "clear2":
        return rb + (
            "lowshelf=f=150:g=0.4,"                        # 几乎不加低频，避免糊
            "equalizer=f=300:width_type=q:w=1.0:g=-4.0,"   # 去闷糊（狠）
            "equalizer=f=650:width_type=q:w=1.2:g=-2.2,"   # 去箱声（狠）
            "equalizer=f=1600:width_type=q:w=1.3:g=-0.8,"
            "equalizer=f=2600:width_type=q:w=1.4:g=2.4,"
            "equalizer=f=3800:width_type=q:w=1.3:g=3.6,"   # 咬字主清晰区（加强）
            "equalizer=f=5200:width_type=q:w=1.4:g=2.4,"   # 通透
            "highshelf=f=9500:g=1.8,"                      # 空气感
            "crystalizer=i=1.4,"
            "acompressor=threshold=-18dB:ratio=3.5:attack=5:release=180:makeup=3")
    if profile == "clear3":
        # 极致人声：把"挡在人声前面"的低中频全部让开，再把 2.5-7kHz 咬字带整体抬起，
        # 配更紧的压缩 → 人声更靠前、更亮、字字分明（低频只留一点点厚度）
        return rb + (
            "lowshelf=f=140:g=-0.5,"                       # 砍掉无用的超低频，避免掩蔽
            "equalizer=f=250:width_type=q:w=1.0:g=-3.6,"   # 去闷糊
            "equalizer=f=480:width_type=q:w=1.1:g=-2.4,"   # 去箱声
            "equalizer=f=900:width_type=q:w=1.2:g=-1.6,"   # 去喉音/鼻音堆积
            "equalizer=f=1800:width_type=q:w=1.3:g=1.4,"
            "equalizer=f=3100:width_type=q:w=1.3:g=4.2,"   # 人声前凸主频（最狠）
            "equalizer=f=4600:width_type=q:w=1.3:g=3.0,"   # 辅音清晰
            "equalizer=f=6800:width_type=q:w=1.4:g=2.2,"   # 齿音细节
            "highshelf=f=10000:g=2.2,"                     # 空气感
            "crystalizer=i=1.8,"                           # 瞬态锐度
            "acompressor=threshold=-20dB:ratio=3.6:attack=6:release=170:makeup=4.5")
    if profile in ("elder", "elder2"):
        # 老者音色：低音高 + 共振峰下移（撑大"声道"→ 老者的胸腔与体量感），
        # 收暗 7kHz 以上（年龄感的来源），但保留 2.6-4.3kHz 咬字区，
        # 避免掉进"又老又糊"的坑；末尾加一丝极轻颤动（老态）。
        if profile == "elder":
            return rb + (
                "lowshelf=f=150:g=2.0,"                        # 老者胸腔共鸣
                "equalizer=f=300:width_type=q:w=1.0:g=-3.2,"   # 去闷糊
                "equalizer=f=600:width_type=q:w=1.2:g=-1.8,"   # 去箱声
                "equalizer=f=1000:width_type=q:w=1.2:g=-1.0,"  # 退喉音堆积
                "equalizer=f=2900:width_type=q:w=1.4:g=2.8,"   # 咬字清晰（温和型，不过亮）
                "equalizer=f=4300:width_type=q:w=1.3:g=1.2,"   # 辅音清晰
                "highshelf=f=7000:g=-2.0,"                     # 收暗超高频 → 年龄感
                "highshelf=f=11500:g=-2.4,"                    # 砍掉"年轻感"空气频
                "tremolo=f=4.3:d=0.045,"                       # 老态微颤
                "acompressor=threshold=-18dB:ratio=3.2:attack=10:release=220:makeup=3.5")
        return rb + (
            "lowshelf=f=160:g=3.0,"                        # 更厚的胸腔
            "equalizer=f=300:width_type=q:w=1.0:g=-3.8,"
            "equalizer=f=650:width_type=q:w=1.2:g=-2.2,"
            "equalizer=f=1100:width_type=q:w=1.2:g=-1.2,"
            "equalizer=f=2600:width_type=q:w=1.4:g=2.6,"
            "equalizer=f=4000:width_type=q:w=1.3:g=1.2,"
            "highshelf=f=6500:g=-3.0,"                     # 更暗
            "highshelf=f=11000:g=-3.0,"
            "tremolo=f=4.1:d=0.06,"
            "acompressor=threshold=-19dB:ratio=3.0:attack=12:release=250:makeup=4")
    if profile == "clear":
        return rb + (
            "lowshelf=f=150:g=1.6,"                     # 保留适量胸腔厚度
            "equalizer=f=320:width_type=q:w=1.0:g=-2.6,"   # 去闷糊
            "equalizer=f=700:width_type=q:w=1.2:g=-1.2,"   # 去箱声
            "equalizer=f=1700:width_type=q:w=1.3:g=-0.8,"  # 稍微退让鼻音区
            "equalizer=f=3200:width_type=q:w=1.5:g=2.6,"   # 人声前凸/咬字清晰
            "equalizer=f=5600:width_type=q:w=1.4:g=1.4,"   # 通透
            "highshelf=f=9000:g=1.2,"                      # 空气感
            "acompressor=threshold=-18dB:ratio=3:attack=8:release=200:makeup=3")
    return rb + (
        "lowshelf=f=170:g=3.2,"
        "equalizer=f=380:width_type=q:w=1.1:g=-2.0,"
        "equalizer=f=1100:width_type=q:w=1.3:g=-1.2,"
        "equalizer=f=2600:width_type=q:w=1.4:g=1.7,"
        "highshelf=f=7500:g=-2.5,"
        "acompressor=threshold=-19dB:ratio=2.5:attack=10:release=220:makeup=2")


def reverb_chain(dry=0.88, wet=0.30, ir=IR_NAME):
    # 注意：本机 ffmpeg 构建的 afir 对 irgain/length/maxir/precision 参数有 bug（会静音），
    # 因此只使用 gtype/irnorm/dry/wet/irfmt，且 dry/wet 避免取 0 或 1。
    return (f"afir=gtype=none:irnorm=-1:dry={dry}:wet={wet}:irfmt=input,"
            f"deesser=i=0.22:m=0.4:f=0.5")


def pass_measure_loudnorm(wav):
    af = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json")
    out = run([FFMPEG, "-hide_banner", "-nostats", "-i", wav, "-af", af,
               "-f", "null", "-"]).stderr
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", out, re.S)
    if not m:
        raise RuntimeError("loudnorm 测量失败:\n" + out[-1500:])
    return json.loads(m.group(0))


def loudnorm_linear(ms, dur=None, fade=True):
    ln = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:"
          f"measured_I={ms['input_i']}:measured_TP={ms['input_tp']}:"
          f"measured_LRA={ms['input_lra']}:measured_thresh={ms['input_thresh']}:"
          f"offset={ms['target_offset']}:linear=true:print_format=summary")
    if fade and dur:
        ln += f",afade=t=out:st={max(0.0, dur - 0.6):.3f}:d=0.6"
    return ln


def _cleanup_norm_temps():
    """删掉本函数产生的迭代临时文件（每个 ~400MB，别攒着）"""
    try:
        for f in os.listdir(WORK):
            if f.startswith("_x_norm_") or f.endswith(".wav.fix.wav"):
                try:
                    os.remove(os.path.join(WORK, f))
                except OSError:
                    pass
    except OSError:
        pass


def normalize_safe(src, out_wav, target_i=TARGET_I, target_tp=TARGET_TP,
                   fade_out=True, verbose=True, max_iter=3):
    """ebur128 自测 + 恒定增益 + 限幅 的响度归一化（替代 loudnorm linear 通路）。

    2026-10-14 踩坑：本机 ffmpeg 的 loudnorm 在 36 分钟长音轨上两处失效——
      ① linear=true 会给出"随时间线性爬升"的增益（0s -36dB → 2100s +7dB），成品前段几乎无声；
      ② 它的测量遍次也会报错（同一 mid 报 I=-46.7，ebur128 实测 -63.9，差 17 dB），
         导致按错误读数算增益、成品整体偏小。
    改用本函数：ebur128 实测 → volume 恒定增益 → alimiter 兜底 → 复测迭代（≤3 次）。
    """
    lim = 10 ** (target_tp / 20.0)
    dur = ffdur(src)
    cur = src
    total = 0.0
    for it in range(max_iter):
        i, l, p = measure(cur)
        gain = target_i - i
        if abs(gain) <= 0.4 and p <= target_tp + 0.1:
            break
        last = it == max_iter - 1
        af = f"volume={gain:.2f}dB,alimiter=limit={lim:.4f}:attack=5:release=60:level=0"
        if last and fade_out and dur:
            af += f",afade=t=out:st={max(0.0, dur - 0.6):.3f}:d=0.6"
        dst = out_wav if last else os.path.join(WORK, f"_x_norm_{os.getpid()}_{it}.wav")
        run([FFMPEG, "-hide_banner", "-v", "warning", "-y", "-i", cur,
             "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", dst])
        total += gain
        if verbose:
            print(f"      归一化 iter{it + 1}: I={i:.2f} → gain {gain:+.2f} dB"
                  f"{' (含尾部淡出)' if last else ''}")
        if last:
            _cleanup_norm_temps()
            return {"gain": total}
        cur = dst
    # 循环内已达标（break 出来）：从 cur 落盘，并补尾部淡出（cur 不能先删）
    af = "anull"
    if fade_out and dur:
        af = f"afade=t=out:st={max(0.0, dur - 0.6):.3f}:d=0.6"
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y", "-i", cur,
         "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", out_wav])
    _cleanup_norm_temps()
    if verbose:
        print(f"      归一化: 累计 gain {total:+.2f} dB（已达标，补尾部淡出）")
    return {"gain": total}


# ---------------------------------------------------------------- 主处理
def transform(src, out_wav, pitch=0.92, wet=0.12, formant="shifted",
              profile="clear", ir=IR_CLEAR, dry=0.95, hp=90, deess=0.38,
              gate=False, ss=None, dur=None, verbose=True):
    """完整链路：净化 → 变声塑形 → 卷积混响 → 线性响度标准化"""
    t0 = time.time()
    dur = dur or (ffdur(src) - (ss or 0))
    # 中间文件必须每次唯一：并发跑多个预设时同名会互相覆盖（踩过坑）
    mid = os.path.join(WORK, f"_x_mid_{os.getpid()}_{int(t0)}.wav")

    cmd = [FFMPEG, "-hide_banner", "-v", "warning", "-y"]
    if ss is not None:
        cmd += ["-ss", str(ss)]
    cmd += ["-i", src, "-i", ir,
            "-filter_complex",
            f"[0:a]{clean_chain(hp=hp, deess=deess, gate=gate)},{voice_chain(pitch, formant, profile)}[v];"
            f"[v][1:a]{reverb_chain(dry, wet, ir)}[out]",
            "-map", "[out]", "-t", f"{dur:.3f}",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", mid]
    run(cmd, cwd=RN_DIR)

    # 第二遍：ebur128 自测 + 恒定增益 + 限幅（loudnorm 在长音轨上有爬坡/测量错 bug）
    normalize_safe(mid, out_wav, verbose=verbose)
    try:
        os.remove(mid)          # 中间文件 ~400MB，用完即删
    except OSError:
        pass

    i1, l1, p1 = measure(out_wav)
    # 兜底：若与目标偏差 > 0.6 dB 或真峰超标，再校一次
    if abs(i1 - TARGET_I) > 0.6 or p1 > TARGET_TP + 0.3:
        tweak = TARGET_I - i1
        run([FFMPEG, "-hide_banner", "-v", "warning", "-y", "-i", out_wav,
             "-af", f"volume={tweak:.2f}dB",
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", out_wav + ".fix.wav"])
        os.replace(out_wav + ".fix.wav", out_wav)
        i1, l1, p1 = measure(out_wav)
    if verbose:
        print(f"【成品】{i1:.1f} LUFS | LRA {l1:.1f} | TP {p1:.1f} dBFS | 用时 {time.time() - t0:.0f}s")
    return {"I": i1, "LRA": l1, "TP": p1, "pitch": pitch, "wet": wet}


def process_full(src, out_wav, preset="T", ss=None, dur=None):
    p = PRESETS[preset]
    print("=" * 74)
    print(f"修仙嗓音处理  预设 {preset}（{p['tag']}）  pitch={p['pitch']} dry={p['dry']} wet={p['wet']} "
          f"profile={p['profile']} ir={p['ir']}")
    print("=" * 74, flush=True)
    # 原始基线（供对比参考）
    i0, l0, p0 = measure(src, ss=ss, t=60)
    # ZCR 必须取"有声讲说段"对比：片头 0-10s 是近静音，噪声门会把它压到 -70dB，
    # 在静音上算零交叉率毫无意义（曾误报"降 70%"）。取 300s 处同一段比较。
    probe_ss = 300 if ss is None else ss
    z0 = zcr(src, ss=probe_ss)
    rep = transform(src, out_wav, p["pitch"], p["wet"], p["formant"],
                    p["profile"], p["ir"], dry=p["dry"],
                    hp=p.get("hp", 90), deess=p.get("deess", 0.38),
                    gate=p.get("gate", False), ss=ss, dur=dur)
    rep["dry"] = p["dry"]
    z1 = zcr(out_wav, ss=300, t=10) if ss is None else zcr(out_wav, ss=probe_ss)
    rep.update({"src": src, "preset": preset, "before": {"I": i0, "LRA": l0, "TP": p0},
                "zcr_before": z0, "zcr_after": z1,
                "zcr_drop_pct": (None if not (z0 and z1) else round((1 - z1 / z0) * 100, 1))})
    print(f"声线校验: 零交叉率 {z0} → {z1}（降 {(1 - z1 / z0) * 100:.1f}% ⇒ 音高变低）")
    return rep


# ---------------------------------------------------------------- A/B 对比片
def make_ab(src, out_mp4, seg=45, ss=300, presets=("T", "A")):
    """多段对比：原声 + 各预设（--presets 指定）"""
    os.makedirs(WORK, exist_ok=True)
    print(f"生成对比片：原声 {seg}s + " + " + ".join(f"{PRESETS[p]['tag']} {seg}s" for p in presets))
    outs = []
    for p in presets:
        w = os.path.join(WORK, f"_ab_{p}.wav")
        d = PRESETS[p]
        transform(src, w, d["pitch"], d["wet"], d["formant"], d["profile"], d["ir"],
                  dry=d["dry"], hp=d.get("hp", 90), deess=d.get("deess", 0.38),
                  gate=d.get("gate", False), ss=ss, dur=seg, verbose=False)
        outs.append(w)
    labels = ["BEFORE 原声"] + [f"{PRESETS[p]['tag']}" for p in presets]
    inputs = []
    for w in outs:
        inputs += ["-i", w]
    n = len(outs)
    fc = []
    fc.append(f"[0:a][1:a]{''.join(f'[{i + 2}:a]' for i in range(n - 1))}concat=n={n + 1}:v=0:a=1[a]")
    dt = ""
    for i, lab in enumerate(labels):
        dt += (f",drawtext=text='{lab}':font='Microsoft YaHei':fontcolor="
               f"{'white' if i == 0 else '0x7CE38B'}:fontsize=40:x=40:y=40:"
               f"enable='between(t\\,{i * seg}\\,{(i + 1) * seg})'")
    fc.append(f"color=c=0x101418:s=1280x720:r=12:d={seg * (n + 1)}{dt}[v]")
    cmd = [FFMPEG, "-hide_banner", "-v", "warning", "-y",
           "-ss", str(ss), "-t", str(seg), "-i", src] + inputs + [
        "-filter_complex", ";".join(fc),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "faster", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", out_mp4]
    run(cmd)
    print(f"→ {out_mp4}")
    return out_mp4


def remux(video, audio, out):
    vd = ffdur(video)
    run([FFMPEG, "-hide_banner", "-v", "warning", "-y",
         "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
         "-t", f"{vd:.3f}", "-movflags", "+faststart", out])
    return vd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEF)
    ap.add_argument("--out", default=os.path.join(WORK, "audio_final.wav"))
    ap.add_argument("--preset", default="T", choices=list(PRESETS))
    ap.add_argument("--pitch", type=float, default=None)
    ap.add_argument("--wet", type=float, default=None)
    ap.add_argument("--ab", type=int, default=0, help="每段秒数；生成三段对比片")
    ap.add_argument("--presets", default="T,A", help="对比用的预设，逗号分隔")
    ap.add_argument("--ss", type=int, default=300)
    ap.add_argument("--make-ir", action="store_true")
    ap.add_argument("--remux", nargs=2, metavar=("视频", "输出"))
    a = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)
    if not os.path.isfile(IR_PATH) or a.make_ir:
        # 旧·大殿混响（1.9s，早期反射强）
        make_ir(IR_PATH, rt60=1.9, predelay_ms=28, lp_hz=6000, hp_hz=150, er_scale=1.0)
    ir_room_path = os.path.join(RN_DIR, IR_CLEAR)
    if not os.path.isfile(ir_room_path) or a.make_ir:
        # 通透版：紧凑空间感（0.9s，反射弱、频响宽）
        make_ir(ir_room_path, rt60=0.9, predelay_ms=12, lp_hz=9000, hp_hz=140,
                er_scale=0.18, seed=21)
    ir_short_path = os.path.join(RN_DIR, IR_SHORT)
    if not os.path.isfile(ir_short_path) or a.make_ir:
        # 近干版：0.45s 短尾（尾音快速收掉，停顿不拖泥）
        make_ir(ir_short_path, rt60=0.45, predelay_ms=8, lp_hz=10000, hp_hz=150,
                er_scale=0.10, seed=33)
    ir_clean_path = os.path.join(RN_DIR, IR_CLEAN)
    if not os.path.isfile(ir_clean_path) or a.make_ir:
        # 人声突出版：0.22s 极短尾（只剩一丝空间感，几乎等同干声）
        make_ir(ir_clean_path, rt60=0.22, predelay_ms=5, lp_hz=11000, hp_hz=170,
                er_scale=0.05, seed=44)
    ir_dry_path = os.path.join(RN_DIR, IR_DRY)
    if not os.path.isfile(ir_dry_path) or a.make_ir:
        # 混响再降版：0.10s 微尾（只保留一丝空气感，听感等同纯干声）
        make_ir(ir_dry_path, rt60=0.10, predelay_ms=3, lp_hz=12000, hp_hz=180,
                er_scale=0.02, seed=55)
    if a.make_ir:
        return 0

    if a.ab:
        make_ab(a.src, os.path.join(WORK, "音频_通透版对比.mp4"), a.ab, a.ss,
                tuple(a.presets.split(",")))
        return 0

    p = dict(PRESETS[a.preset])
    if a.pitch is not None:
        p["pitch"] = a.pitch
    if a.wet is not None:
        p["wet"] = a.wet
    PRESETS["custom"] = p
    rep = process_full(a.src, a.out, "custom")
    with open(os.path.join(WORK, "audio_xianxia_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)

    if a.remux:
        print(f"\n合入视频: {os.path.basename(a.remux[1])}")
        vd = remux(a.remux[0], a.out, a.remux[1])
        print(f"✓ {a.remux[1]}  ({vd:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
