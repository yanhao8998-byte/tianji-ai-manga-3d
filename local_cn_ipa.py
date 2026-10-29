# -*- coding: utf-8 -*-
"""
天纪项目 · 本地管线 v2：ControlNet(深度+姿态) + IPAdapter(角色参考) img2img 重跑
- 深度图: DepthAnything vits14 ；姿态图: DWPose
- 角色锚定: IPAdapter PLUS FACE，参考图 = 玄鹤先生秘境定妆图
- 输出: 成图 + 深度图 + 姿态图（便于人工核验控制信号）

用法: python local_cn_ipa.py [denoise] [strength_depth] [strength_pose] [ipa_weight]
"""
import os
import json, os, shutil, sys, time, urllib.request, urllib.parse, uuid, glob

HOST  = "http://127.0.0.1:8189"
COMFY = r"D:\AI\ComfyUI_windows_portable\ComfyUI"
CIN   = os.path.join(COMFY, "input")
CKPT  = "v1-5-pruned-emaonly.safetensors"
PROJ = os.path.dirname(os.path.abspath(__file__))
STY   = os.path.join(PROJ, "04_关键帧_风格化")
IN_DIR  = os.path.join(STY, "_试跑_待处理")
OUT_DIR = os.path.join(STY, "_本地试跑_CN_IPA")
REF_IMG = os.path.join(PROJ, "00_素材", "参考图_玄鹤先生_秘境.png")
SUB     = "tianji_v2"

POS = ("a dignified elderly chinese sage, white hair tied in a topknot, long white beard, "
       "thin round-framed glasses, moon-white flowing robe, sitting half-submerged on a rolling sea of clouds, "
       "surrounded by green mountains and a great river waterfall, birds and blooming flowers, "
       "misty immortal paradise, 3d animated movie style, stylized render, warm golden volumetric light, "
       "cinematic lighting, highly detailed, masterpiece, best quality")
NEG = ("blackboard, chalkboard, classroom, whiteboard, text, letters, watermark, signature, logo, ui, frame, "
       "low quality, worst quality, blurry, jpeg artifacts, deformed face, disfigured, extra fingers, "
       "bad anatomy, photo, realistic skin texture, oversaturated")


def api(path, data=None):
    req = urllib.request.Request(HOST + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def fill_defaults(node_name, provided):
    """查询 object_info，为未显式提供的 required 参数填默认值（无默认则用第一个枚举项）"""
    info = api(f"/object_info/{urllib.parse.quote(node_name)}").get(node_name)
    out = dict(provided)
    for k, spec in info["input"].get("required", {}).items():
        if k in out:
            continue
        if isinstance(spec, list) and len(spec) >= 2 and isinstance(spec[1], dict) and "default" in spec[1]:
            out[k] = spec[1]["default"]
        elif isinstance(spec, list) and spec and isinstance(spec[0], list) and spec[0]:
            out[k] = spec[0][0]
    return out


def build(name_in, ref_in, denoise, sd, sp, iw, seed, sd_end=0.85, sp_end=0.80):
    g = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "20": {"class_type": "LoadImage", "inputs": {"image": name_in}},
        "21": {"class_type": "ImageScale", "inputs": {"image": ["20", 0], "upscale_method": "lanczos",
                                                       "width": 768, "height": 576, "crop": "disabled"}},
        "22": {"class_type": "LoadImage", "inputs": {"image": ref_in}},
        "30": {"class_type": "DepthAnythingPreprocessor",
               "inputs": fill_defaults("DepthAnythingPreprocessor",
                                       {"image": ["21", 0], "resolution": 768,
                                        "ckpt_name": "depth_anything_vits14.pth"})},
        "31": {"class_type": "DWPreprocessor",
               "inputs": fill_defaults("DWPreprocessor",
                                       {"image": ["21", 0], "resolution": 768,
                                        "detect_hand": "enable", "detect_body": "enable", "detect_face": "enable",
                                        "bbox_detector": "yolox_l.onnx", "pose_estimator": "dw-ll_ucoco_384.onnx"})},
        "32": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "control_v11f1p_sd15_depth.pth"}},
        "33": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "control_v11p_sd15_openpose.pth"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": POS, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["4", 1]}},
        "50": {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": ["6", 0], "negative": ["7", 0], "control_net": ["32", 0],
                          "image": ["30", 0], "strength": sd, "start_percent": 0.0, "end_percent": sd_end}},
        "51": {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": ["50", 0], "negative": ["50", 1], "control_net": ["33", 0],
                          "image": ["31", 0], "strength": sp, "start_percent": 0.0, "end_percent": sp_end}},
        "40": {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": ["4", 0], "preset": "PLUS FACE (portraits)"}},
        "41": {"class_type": "IPAdapterAdvanced",
               "inputs": {"model": ["40", 0], "ipadapter": ["40", 1], "image": ["22", 0],
                          "weight": iw, "weight_type": "ease in-out", "combine_embeds": "concat",
                          "start_at": 0.0, "end_at": 0.85, "embeds_scaling": "V only"}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["4", 2]}},
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 26, "cfg": 7.0,
                                                   "sampler_name": "dpmpp_2m", "scheduler": "karras",
                                                   "denoise": denoise, "model": ["41", 0],
                                                   "positive": ["51", 0], "negative": ["51", 1],
                                                   "latent_image": ["12", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9":  {"class_type": "SaveImage", "inputs": {"filename_prefix": "v2_out",   "images": ["8", 0]}},
        "60": {"class_type": "SaveImage", "inputs": {"filename_prefix": "v2_depth", "images": ["30", 0]}},
        "61": {"class_type": "SaveImage", "inputs": {"filename_prefix": "v2_pose",  "images": ["31", 0]}},
    }
    return g


def main():
    denoise = float(sys.argv[1]) if len(sys.argv) > 1 else 0.70
    sd      = float(sys.argv[2]) if len(sys.argv) > 2 else 0.70
    sp      = float(sys.argv[3]) if len(sys.argv) > 3 else 0.55
    iw      = float(sys.argv[4]) if len(sys.argv) > 4 else 0.85
    in_dir  = sys.argv[5] if len(sys.argv) > 5 else IN_DIR
    out_dir = sys.argv[6] if len(sys.argv) > 6 else OUT_DIR
    sd_end  = float(sys.argv[7]) if len(sys.argv) > 7 else 0.85
    sp_end  = float(sys.argv[8]) if len(sys.argv) > 8 else 0.80

    print("=" * 70)
    print(f"denoise={denoise}  depth强度={sd}(end{sd_end})  pose强度={sp}(end{sp_end})  IPA={iw}")
    print(f"输入: {in_dir}")
    print(f"输出: {out_dir}")
    ss = api("/system_stats"); d = ss["devices"][0]
    print(f"GPU: {d['name']}  空闲 VRAM {d['vram_free']/1024**3:.1f}/{d['vram_total']/1024**3:.1f} GB")

    files = sorted(glob.glob(os.path.join(in_dir, "*.png")))
    if not files:
        print("✗ 无输入"); return 1
    dst_root = os.path.join(CIN, SUB)
    if os.path.isdir(dst_root):
        shutil.rmtree(dst_root)
    os.makedirs(dst_root, exist_ok=True)
    for sub in ("", "_深度图", "_姿态图"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    # 参考图（定妆图）复制进 ComfyUI input
    ref_name = "玄鹤先生_定妆参考.png"
    shutil.copy2(REF_IMG, os.path.join(dst_root, ref_name))

    ok = 0
    for i, fp in enumerate(files, 1):
        base = os.path.basename(fp)
        shutil.copy2(fp, os.path.join(dst_root, base))
        seed = 20261013 + int("".join(ch for ch in base if ch.isdigit()) or "0")
        wf = build(f"{SUB}/{base}", f"{SUB}/{ref_name}", denoise, sd, sp, iw, seed, sd_end, sp_end)
        t0 = time.time()
        print(f"[{i}/{len(files)}] {base} ...", end=" ", flush=True)
        pid = api("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})["prompt_id"]
        got = False
        while time.time() - t0 < 1200:
            time.sleep(3)
            h = api(f"/history/{pid}")
            if pid not in h:
                continue
            st = h[pid]["status"]
            if st.get("completed"):
                for node, out in h[pid]["outputs"].items():
                    for im in out.get("images", []):
                        q = ("?filename=" + urllib.parse.quote(im["filename"]) +
                             "&subfolder=" + urllib.parse.quote(im.get("subfolder", "")) +
                             "&type=" + im["type"])
                        pre = im["filename"].split("_")[1] if "_" in im["filename"] else ""
                        if pre == "depth":
                            dst = os.path.join(out_dir, "_深度图", base)
                        elif pre == "pose":
                            dst = os.path.join(out_dir, "_姿态图", base)
                        else:
                            dst = os.path.join(out_dir, base)
                        with urllib.request.urlopen(HOST + "/view" + q, timeout=180) as resp, open(dst, "wb") as f:
                            f.write(resp.read())
                got = True
                break
            if st.get("status_str") == "error":
                print("✗ 报错")
                for m in st.get("messages", [])[-4:]:
                    print("    ", str(m)[:400])
                break
        print(f"✓ {time.time()-t0:.0f}s" if got else "✗ 失败/超时")
        ok += int(got)
    print("=" * 70)
    print(f"完成 {ok}/{len(files)}  →  {out_dir}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
