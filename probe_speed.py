# -*- coding: utf-8 -*-
"""
天纪项目 · 提速探针
在同一张帧上跑不同配置，报告各配置耗时，用于定位瓶颈并选出量产配置。

用法:
  python probe_speed.py                 # 跑内置配置矩阵
"""
import os
import json, os, shutil, sys, time, urllib.request, urllib.parse, uuid

HOST  = "http://127.0.0.1:8189"
COMFY = r"D:\AI\ComfyUI_windows_portable\ComfyUI"
CIN   = os.path.join(COMFY, "input")
CKPT  = "v1-5-pruned-emaonly.safetensors"
PROJ = os.path.dirname(os.path.abspath(__file__))
STY   = os.path.join(PROJ, "04_关键帧_风格化")
SRC   = os.path.join(STY, "_本地试跑_CN_IPA", "f017100.png")
REF   = os.path.join(PROJ, "00_素材", "参考图_玄鹤先生_秘境.png")
SUB   = "tianji_probe"
LCM   = "lcm-lora-sdv1-5.safetensors"

POS = ("a dignified elderly chinese sage, white hair tied in a topknot, long white beard, "
       "thin round-framed glasses, moon-white flowing robe, sitting half-submerged on a rolling sea of clouds, "
       "surrounded by green mountains and a great river waterfall, birds and blooming flowers, "
       "misty immortal paradise, 3d animated movie style, stylized render, warm golden volumetric light, "
       "cinematic lighting, highly detailed, masterpiece, best quality")
NEG = ("blackboard, chalkboard, classroom, whiteboard, text, letters, watermark, signature, logo, ui, frame, "
       "low quality, worst quality, blurry, jpeg artifacts, deformed face, disfigured, extra fingers, "
       "bad anatomy, photo, realistic skin texture, oversaturated")


def api(path, data=None, timeout=120):
    req = urllib.request.Request(HOST + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode(errors="replace").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def fill_defaults(node_name, provided):
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


def build(name_in, ref_in, W, H, pose_res, steps, cfg, denoise, use_lcm,
          use_pose=True, use_depth=True, use_ipa=True, sd=0.70, sp=0.55, iw=0.85):
    g = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "20": {"class_type": "LoadImage", "inputs": {"image": name_in}},
        "21": {"class_type": "ImageScale", "inputs": {"image": ["20", 0], "upscale_method": "lanczos",
                                                       "width": W, "height": H, "crop": "disabled"}},
        "22": {"class_type": "LoadImage", "inputs": {"image": ref_in}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": POS, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["4", 1]}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["4", 2]}},
    }
    # 模型来源：可选套用 LCM-LoRA
    model_src = ["4", 0]
    if use_lcm:
        g["45"] = {"class_type": "LoraLoader",
                   "inputs": fill_defaults("LoraLoader", {"lora_name": LCM, "strength_model": 1.0,
                                                          "strength_clip": 1.0, "model": ["4", 0], "clip": ["4", 1]})}
        model_src = ["45", 0]
        # 文本编码也要走 LoRA 后的 clip
        g["6"]["inputs"]["clip"] = ["45", 1]
        g["7"]["inputs"]["clip"] = ["45", 1]

    pos, neg = ["6", 0], ["7", 0]
    if use_depth:
        g["30"] = {"class_type": "DepthAnythingPreprocessor",
                   "inputs": fill_defaults("DepthAnythingPreprocessor",
                                           {"image": ["21", 0], "resolution": W,
                                            "ckpt_name": "depth_anything_vits14.pth"})}
        g["32"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "control_v11f1p_sd15_depth.pth"}}
        g["50"] = {"class_type": "ControlNetApplyAdvanced",
                   "inputs": {"positive": pos, "negative": neg, "control_net": ["32", 0],
                              "image": ["30", 0], "strength": sd, "start_percent": 0.0, "end_percent": 0.85}}
        pos, neg = ["50", 0], ["50", 1]
    if use_pose:
        g["31"] = {"class_type": "DWPreprocessor",
                   "inputs": fill_defaults("DWPreprocessor",
                                           {"image": ["21", 0], "resolution": pose_res,
                                            "detect_hand": "disable", "detect_body": "enable", "detect_face": "enable",
                                            "bbox_detector": "yolox_l.onnx", "pose_estimator": "dw-ll_ucoco_384.onnx"})}
        g["33"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "control_v11p_sd15_openpose.pth"}}
        g["51"] = {"class_type": "ControlNetApplyAdvanced",
                   "inputs": {"positive": pos, "negative": neg, "control_net": ["33", 0],
                              "image": ["31", 0], "strength": sp, "start_percent": 0.0, "end_percent": 0.80}}
        pos, neg = ["51", 0], ["51", 1]
    if use_ipa:
        g["40"] = {"class_type": "IPAdapterUnifiedLoader",
                   "inputs": {"model": model_src, "preset": "PLUS FACE (portraits)"}}
        g["41"] = {"class_type": "IPAdapterAdvanced",
                   "inputs": {"model": ["40", 0], "ipadapter": ["40", 1], "image": ["22", 0],
                              "weight": iw, "weight_type": "ease in-out", "combine_embeds": "concat",
                              "start_at": 0.0, "end_at": 0.85, "embeds_scaling": "V only"}}
        model_src = ["41", 0]

    g["3"] = {"class_type": "KSampler", "inputs": {"seed": 20261013, "steps": steps, "cfg": cfg,
                                                   "sampler_name": "lcm" if use_lcm else "dpmpp_2m",
                                                   "scheduler": "sgm_uniform" if use_lcm else "karras",
                                                   "denoise": denoise, "model": model_src,
                                                   "positive": pos, "negative": neg,
                                                   "latent_image": ["12", 0]}}
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}}
    g["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "probe_out", "images": ["8", 0]}}
    return g


def run_one(tag, **kw):
    os.makedirs(os.path.join(CIN, SUB), exist_ok=True)
    base = os.path.basename(SRC)
    shutil.copy2(SRC, os.path.join(CIN, SUB, base))
    ref_name = "玄鹤先生_定妆参考.png"
    shutil.copy2(REF, os.path.join(CIN, SUB, ref_name))
    wf = build(f"{SUB}/{base}", f"{SUB}/{ref_name}", **kw)
    t0 = time.time()
    pid = api("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})["prompt_id"]
    while time.time() - t0 < 1800:
        time.sleep(2)
        h = api(f"/history/{pid}")
        if pid not in h:
            continue
        st = h[pid]["status"]
        if st.get("completed"):
            dt = time.time() - t0
            print(f"   ✓ {tag}: {dt:.0f}s")
            return dt
        if st.get("status_str") == "error":
            print(f"   ✗ {tag}: 报错")
            for m in st.get("messages", [])[-5:]:
                print("      ", str(m)[:300])
            return None
    print(f"   ✗ {tag}: 超时")
    return None


def main():
    # CLI 单配置模式: probe_speed.py W H pose_res steps cfg denoise lcm
    if len(sys.argv) >= 7:
        W, H, pr, st = map(int, sys.argv[1:5])
        cf = float(sys.argv[5])
        dn = float(sys.argv[6])
        lcm = len(sys.argv) > 7 and sys.argv[7] == "1"
        api("/free", {"unload_models": True, "free_memory": True})
        t = run_one(f"CLI {W}x{H} pose{pr} {st}步 cfg{cf} lcm={lcm}",
                    W=W, H=H, pose_res=pr, steps=st, cfg=cf, denoise=dn, use_lcm=lcm)
        return 0 if t else 2

    cfgs = [
        ("A 基线 768x576 pose768 26步", dict(W=768, H=576, pose_res=768, steps=26, cfg=7.0, denoise=0.70, use_lcm=False)),
        ("B 只降pose到512",            dict(W=768, H=576, pose_res=512, steps=26, cfg=7.0, denoise=0.70, use_lcm=False)),
        ("C 降分辨率640x480",          dict(W=640, H=480, pose_res=512, steps=26, cfg=7.0, denoise=0.70, use_lcm=False)),
        ("D LCM 8步 cfg1.5",           dict(W=768, H=576, pose_res=512, steps=8,  cfg=1.5, denoise=0.70, use_lcm=True)),
        ("E LCM 640x480 6步",          dict(W=640, H=480, pose_res=512, steps=6,  cfg=1.5, denoise=0.70, use_lcm=True)),
        ("F 仅深度+IPA(去pose)",       dict(W=640, H=480, pose_res=512, steps=26, cfg=7.0, denoise=0.70, use_lcm=False, use_pose=False)),
    ]
    api("/free", {"unload_models": True, "free_memory": True})
    print("=" * 62)
    print(f"{'配置':<30s} {'耗时':>8s}")
    print("-" * 62)
    res = []
    for tag, kw in cfgs:
        t = run_one(tag, **kw)
        res.append((tag, t))
        print("-" * 62)
    print("=" * 62)
    base = next((t for tag, t in res if tag.startswith("A")), None)
    for tag, t in res:
        if t is None:
            print(f"  {tag:<30s}    失败")
        elif base:
            print(f"  {tag:<30s} {t:6.0f}s   {base/t:4.2f}x")
        else:
            print(f"  {tag:<30s} {t:6.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
