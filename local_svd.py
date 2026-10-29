# -*- coding: utf-8 -*-
"""
SVD 图生视频 · ComfyUI API 批量工具
把已风格化的关键帧逐张变成动态视频片段（Stable Video Diffusion img2vid）

用法:
  python local_svd.py [输入目录] [输出目录] [宽] [高] [帧数] [运动强度] [步数]
默认:
  输入 <项目根>/04_关键帧_风格化/_本地试跑_CN_IPA
  输出 <项目根>/07_视频片段
  768 576 14 90 25
"""
import os, sys, json, glob, time, shutil, urllib.request, urllib.error

HOST   = "http://127.0.0.1:8189"
COMFY  = r"D:/AI/ComfyUI_windows_portable/ComfyUI"
CIN    = os.path.join(COMFY, "input")
COUT   = os.path.join(COMFY, "output")
CKPT   = "svd.safetensors"
SUB    = "tianji_svd"

IN_DIR  = sys.argv[1] if len(sys.argv) > 1 else r"<项目根>/04_关键帧_风格化/_本地试跑_CN_IPA"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else r"<项目根>/07_视频片段"
W       = int(sys.argv[3]) if len(sys.argv) > 3 else 768
H       = int(sys.argv[4]) if len(sys.argv) > 4 else 576
FRAMES  = int(sys.argv[5]) if len(sys.argv) > 5 else 14
MOTION  = int(sys.argv[6]) if len(sys.argv) > 6 else 90
STEPS   = int(sys.argv[7]) if len(sys.argv) > 7 else 25
FPS     = 7


def fill_defaults(cls, given):
    """按 /object_info 补全必需参数默认值，避免手写漏项"""
    try:
        d = json.load(urllib.request.urlopen(f"{HOST}/object_info/{cls}", timeout=15))[cls]
        req = d.get("input", {}).get("required", {})
        out = dict(given)
        for k, v in req.items():
            if k in out:
                continue
            if isinstance(v[0], list):
                out[k] = v[0][0]
            else:
                meta = v[1] if len(v) > 1 and isinstance(v[1], dict) else {}
                out[k] = meta.get("default", 0)
        return out
    except Exception as e:
        print(f"   ! 补默认值失败 {cls}: {e}")
        return given


def build(name_in, seed):
    return {
        "1": {"class_type": "ImageOnlyCheckpointLoader",
              "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "VideoLinearCFGGuidance",
              "inputs": {"model": ["1", 0], "min_cfg": 1.0}},
        "3": {"class_type": "LoadImage",
              "inputs": {"image": f"{SUB}/{name_in}"}},
        "4": {"class_type": "ImageScale",
              "inputs": fill_defaults("ImageScale",
                       {"image": ["3", 0], "upscale_method": "lanczos",
                        "width": W, "height": H, "crop": "disabled"})},
        "5": {"class_type": "SVD_img2vid_Conditioning",
              "inputs": {"clip_vision": ["1", 1], "init_image": ["4", 0], "vae": ["1", 2],
                         "width": W, "height": H, "video_frames": FRAMES,
                         "motion_bucket_id": MOTION, "fps": FPS,
                         "augmentation_level": 0.0}},
        "6": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "seed": seed, "steps": STEPS, "cfg": 2.5,
                         "sampler_name": "euler", "scheduler": "karras",
                         "positive": ["5", 0], "negative": ["5", 1],
                         "latent_image": ["5", 2], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode",
              "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveAnimatedWEBP",
              "inputs": fill_defaults("SaveAnimatedWEBP",
                       {"images": ["7", 0], "filename_prefix": f"svd/{os.path.splitext(name_in)[0]}",
                        "fps": float(FPS), "lossless": False, "quality": 92, "method": "default"})},
    }


def wait(pid, timeout=3600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = json.load(urllib.request.urlopen(f"{HOST}/history/{pid}", timeout=15))
            if pid in h:
                return h[pid], time.time() - t0
        except Exception:
            pass
        time.sleep(3)
    return None, time.time() - t0


def main():
    print("=" * 74)
    print(f"SVD 图生视频   {W}x{H}  {FRAMES}帧  motion={MOTION}  steps={STEPS}  fps={FPS}")
    print(f"输入 {IN_DIR}")
    print(f"输出 {OUT_DIR}")
    print("=" * 74)

    # 服务探活
    try:
        urllib.request.urlopen(f"{HOST}/system_stats", timeout=10)
    except Exception as e:
        print(f"✗ ComfyUI({HOST}) 不可达: {e}")
        return 1

    files = sorted(glob.glob(os.path.join(IN_DIR, "*.png")))
    if not files:
        print("✗ 无输入图")
        return 1

    # 拷贝到 ComfyUI/input
    dst_in = os.path.join(CIN, SUB)
    if os.path.isdir(dst_in):
        shutil.rmtree(dst_in)
    os.makedirs(dst_in, exist_ok=True)
    for f in files:
        shutil.copy2(f, os.path.join(dst_in, os.path.basename(f)))
    os.makedirs(OUT_DIR, exist_ok=True)

    ok = 0
    total = 0.0
    for idx, f in enumerate(files, 1):
        base = os.path.basename(f)
        print(f"\n[{idx}/{len(files)}] {base}")
        seed = 100000 + idx * 137
        wf = build(base, seed)
        try:
            req = urllib.request.Request(f"{HOST}/prompt",
                                         data=json.dumps({"prompt": wf}).encode(),
                                         headers={"Content-Type": "application/json"})
            pid = json.load(urllib.request.urlopen(req, timeout=30))["prompt_id"]
        except urllib.error.HTTPError as e:
            print("   ✗ 提交失败:", e.read().decode()[:600])
            continue
        res, dt = wait(pid)
        if not res:
            print("   ✗ 超时未完成")
            continue
        st = res.get("status", {})
        if st.get("status_str") == "error" or st.get("completed") is False:
            for m in st.get("messages", []):
                if m[0] == "execution_error":
                    print("   ✗ 执行错误:", json.dumps(m[1], ensure_ascii=False)[:500])
            continue
        # 收集产物
        got = 0
        for node_out in res.get("outputs", {}).values():
            for im in node_out.get("images", []):
                src = os.path.join(COUT, im.get("subfolder", ""), im["filename"])
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(OUT_DIR, os.path.basename(src)))
                    got += 1
        print(f"   ✓ {dt:.1f}s  产物 {got} 个")
        ok += 1
        total += dt

    print("\n" + "=" * 74)
    print(f"完成 {ok}/{len(files)}   总耗时 {total/60:.1f} 分钟   平均 {total/max(ok,1):.1f}s/条")
    print(f"→ {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
