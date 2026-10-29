# -*- coding: utf-8 -*-
"""声线客观分析：基频 F0（自相关）、频谱重心、频段能量配比。
用法: python measure_voice.py file1.wav [file2.wav ...]
"""
import sys, wave, os
import numpy as np

def read_mono(path, max_sec=180):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        n = min(w.getnframes(), int(sr * max_sec))
        raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr

def f0_track(x, sr, fmin=60, fmax=350, fl=0.040, hop=0.020):
    n = int(sr * fl); h = int(sr * hop)
    f0s, energies = [], []
    for i in range(0, len(x) - n, h):
        fr = x[i:i + n]
        e = float(np.sqrt(np.mean(fr ** 2)))
        energies.append(e)
        if e < 0.012:            # 静音/清音跳过
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[n - 1:]
        if ac[0] <= 0:
            continue
        lo, hi = int(sr / fmax), int(sr / fmin)
        if hi >= len(ac):
            hi = len(ac) - 1
        seg = ac[lo:hi]
        if len(seg) == 0:
            continue
        k = int(np.argmax(seg)) + lo
        if ac[0] > 0 and ac[k] / ac[0] > 0.32:      # 周期性够强才算浊音
            f0s.append(sr / k)
    return np.array(f0s), np.array(energies)

def centroid(x, sr):
    n = 2048; h = 1024
    w = np.hanning(n)
    cs = []
    for i in range(0, len(x) - n, h):
        fr = x[i:i + n] * w
        if np.sqrt(np.mean(fr ** 2)) < 0.01:
            continue
        sp = np.abs(np.fft.rfft(fr)) ** 2
        fq = np.fft.rfftfreq(n, 1.0 / sr)
        tot = sp.sum()
        if tot <= 0:
            continue
        cs.append((sp * fq).sum() / tot)
    return float(np.median(cs)) if cs else 0.0

def band_ratio(x, sr, lo, hi):
    """指定频段的 RMS 相对全频 RMS 的 dB 比值"""
    n = 4096; h = 2048
    fq = np.fft.rfftfreq(n, 1.0 / sr)
    m = (fq >= lo) & (fq < hi)
    bs, ts = [], []
    for i in range(0, len(x) - n, h):
        fr = x[i:i + n] * np.hanning(n)
        sp = np.abs(np.fft.rfft(fr)) ** 2
        if sp.sum() <= 0:
            continue
        bs.append(sp[m].sum()); ts.append(sp.sum())
    if not bs:
        return 0.0
    return 10 * np.log10((np.mean(bs) + 1e-20) / (np.mean(ts) + 1e-20))

def report(path):
    x, sr = read_mono(path)
    f0, en = f0_track(x, sr)
    c = centroid(x, sr)
    print(f"\n=== {os.path.basename(path)} ===")
    if len(f0):
        print(f"  F0 中位数 {np.median(f0):6.1f} Hz   (P25 {np.percentile(f0,25):.0f} / P75 {np.percentile(f0,75):.0f})"
              f"   浊音帧 {len(f0)}")
    else:
        print("  F0: 未检出")
    print(f"  频谱重心(明亮度) {c:7.0f} Hz")
    print(f"  低频 80-1k   {band_ratio(x,sr,80,1000):6.1f} dB")
    print(f"  中频 1k-3k   {band_ratio(x,sr,1000,3000):6.1f} dB")
    print(f"  清晰 1k-4k   {band_ratio(x,sr,1000,4000):6.1f} dB")
    print(f"  齿音 4k-8k   {band_ratio(x,sr,4000,8000):6.1f} dB")
    print(f"  空气 >8k     {band_ratio(x,sr,8000,16000):6.1f} dB")
    return np.median(f0) if len(f0) else 0.0, c

if __name__ == "__main__":
    for p in sys.argv[1:]:
        try:
            report(p)
        except Exception as e:
            print(f"[{p}] 读取失败: {e}")
