# -*- coding: utf-8 -*-
"""
本机 ComfyUI img2img 批量「试构图」工具（SD1.5 底模，零成本预览）
- 作用：把关键帧原图做低成本风格化预览，用于本地验证构图/提示词/策略，正式成片仍走云端。
- 特点：纯 img2img（无 ControlNet/IPAdapter），以原帧为底 → 保构图、保姿态、保守原人物特征。

用法：
  python local_im2img.py                         # 默认跑 04_关键帧_风格化\\_试跑_待处理 的 5 张
  python local_im2img.py <输入目录> <输出目录> [denoise] [宽] [高]

依赖：本机 ComfyUI 服务已在 127.0.0.1:8188 运行。
"""
import os
import json, os, shutil, sys, time, urllib.request, urllib.parse, uuid, glob

HOST      = "http://127.0.0.1:8188"
COMFY     = r"D:\AI\ComfyUI_windows_portable\ComfyUI"
COMFY_IN  = os.path.join(COMFY, "input")
CKPT      = "v1-5-pruned-emaonly.safetensors"
PROJ = os.path.dirname(os.path.abspath(__file__))
DEF_IN    = os.path.join(PROJ, "04_关键帧_风格化", "_试跑_待处理")
DEF_OUT   = os.path.join(PROJ, "04_关键帧_风格化", "_本地试跑")
SUB       = "tianji_trial"          # ComfyUI/input 下的子目录名

# 项目锁定提示词（英文，SD1.5 CLIP 只认英文）
POS = ("a dignified elderly chinese sage, white hair tied in a topknot, long white beard, "
       "thin round-framed glasses, moon-white flowing robe, sitting half-submerged on a rolling sea of clouds, "
       "surrounded by green mountains and a great river waterfall, birds and blooming flowers, "
       "misty immortal paradise, 3d animated movie style, stylized render, warm golden volumetric light, "
       "cinematic lighting, highly detailed, masterpiece, best quality")
NEG = ("blackboard, chalkboard, classroom, whiteboard, text, letters, watermark, signature, logo, ui, frame, "
       "low quality, worst quality, blurry, jpeg artifacts, deformed face, disfigured, extra fingers, "
       "bad anatomy, photo, realistic skin texture, oversaturated")


def api(path, data=None):
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def build_wf(name_in, denoise, w, h, seed):
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "10": {"class_type": "LoadImage", "inputs": {"image": name_in}},
        "11": {"class_type": "ImageScale", "inputs": {
            "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled",
            "image": ["10", 0]}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["11", 0], "vae": ["4", 2]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": POS, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": 22, "cfg": 7.0, "sampler_name": "dpmpp_2m", "scheduler": "karras",
            "denoise": denoise, "model": ["4", 0], "positive": ["6", 0],
            "negative": ["7", 0], "latent_image": ["12", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "local_trial", "images": ["8", 0]}},
    }


def main():
    args = sys.argv[1:]
    in_dir  = args[0] if len(args) > 0 else DEF_IN
    out_dir = args[1] if len(args) > 1 else DEF_OUT
    denoise = float(args[2]) if len(args) > 2 else 0.45
    w = int(args[3]) if len(args) > 3 else 768
    h = int(args[4]) if len(args) > 4 else 576

    print("=" * 66)
    print(f"输入目录: {in_dir}")
    print(f"输出目录: {out_dir}")
    print(f"参数: denoise={denoise}  尺寸={w}x{h}  步数=22  sampler=dpmpp_2m/karras")

    ss = api("/system_stats")
    d = ss["devices"][0]
    print(f"GPU: {d['name']}  VRAM {d['vram_free']/1024**3:.1f}/{d['vram_total']/1024**3:.1f} GB 空闲")

    files = sorted(glob.glob(os.path.join(in_dir, "*.png")))
    if not files:
        print("✗ 输入目录没有 PNG"); return 1

    # 复制到 ComfyUI/input/<SUB>
    dst_root = os.path.join(COMFY_IN, SUB)
    if os.path.isdir(dst_root):
        shutil.rmtree(dst_root)
    os.makedirs(dst_root, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    ok = 0
    report = []
    for i, fp in enumerate(files, 1):
        base = os.path.basename(fp)
        shutil.copy2(fp, os.path.join(dst_root, base))
        name_in = f"{SUB}/{base}"
        seed = 100000 + int("".join(ch for ch in base if ch.isdigit()) or "0")
        wf = build_wf(name_in, denoise, w, h, seed)

        t0 = time.time()
        print(f"[{i}/{len(files)}] {base} ...", end=" ", flush=True)
        r = api("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})
        pid = r["prompt_id"]
        got = None
        while time.time() - t0 < 900:
            time.sleep(3)
            hist = api(f"/history/{pid}")
            if pid in hist:
                st = hist[pid]["status"]
                if st.get("completed"):
                    for node, out in hist[pid]["outputs"].items():
                        for im in out.get("images", []):
                            q = ("?filename=" + urllib.parse.quote(im["filename"]) +
                                 "&subfolder=" + urllib.parse.quote(im.get("subfolder", "")) +
                                 "&type=" + im["type"])
                            dst = os.path.join(out_dir, base)
                            with urllib.request.urlopen(HOST + "/view" + q, timeout=120) as resp, \
                                 open(dst, "wb") as f:
                                f.write(resp.read())
                            got = dst
                            break
                    break
                if st.get("status_str") == "error":
                    print("✗ 执行报错")
                    for m in st.get("messages", [])[-3:]:
                        print("    ", str(m)[:240])
                    break
        if got:
            ok += 1
            print(f"✓ {time.time()-t0:.0f}s")
            report.append({"frame": base, "ok": True, "sec": round(time.time() - t0, 1)})
        else:
            print("✗ 超时/失败")
            report.append({"frame": base, "ok": False})

    with open(os.path.join(out_dir, "_试跑报告.json"), "w", encoding="utf-8") as f:
        json.dump({"denoise": denoise, "size": [w, h], "items": report}, f, ensure_ascii=False, indent=2)

    print("=" * 66)
    print(f"完成 {ok}/{len(files)}，产物在 {out_dir}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
