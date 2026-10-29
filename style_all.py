# -*- coding: utf-8 -*-
"""
天纪项目 · 整片风格化（可断点续跑）
把 03_关键帧_原图 下全部关键帧批量风格化为玄鹤先生·云上秘境 3D 动画风格。

特性:
  - 断点续跑: 已存在的输出自动跳过（可随时中断/重启）
  - 失败自动重试 1 次
  - 实时进度 + 速度 + 剩余时间估算
  - 状态写入 _style_state.json，可查已处理集合

用法:
  python style_all.py [选项]
     --denoise 0.70   重绘幅度
     --steps   26     采样步数
     --cfg     7.0    提示词引导
     --w 768 --h 576  输出分辨率
     --pose-res 768   DWPose 分辨率
     --lcm 0/1        是否启用 LCM-LoRA 加速（步数建议 8、cfg 1.5）
     --limit N        只跑前 N 张（试跑用）
     --in  DIR        输入目录（默认 03_关键帧_原图）
     --out DIR        输出目录（默认 04_关键帧_风格化/_正式输出）
     --sd 0.70 --sp 0.55 --iw 0.85   控制强度
"""
import os
import argparse, json, os, shutil, sys, time, urllib.request, urllib.parse, uuid, glob

HOST  = "http://127.0.0.1:8189"
COMFY = r"D:\AI\ComfyUI_windows_portable\ComfyUI"
CIN   = os.path.join(COMFY, "input")
CKPT  = "v1-5-pruned-emaonly.safetensors"
PROJ = os.path.dirname(os.path.abspath(__file__))
STY   = os.path.join(PROJ, "04_关键帧_风格化")
REF   = os.path.join(PROJ, "00_素材", "参考图_玄鹤先生_秘境.png")
LCM   = "lcm-lora-sdv1-5.safetensors"
SUB   = "tianji_all"

POS = ("a dignified elderly chinese sage, white hair tied in a topknot, long white beard, "
       "thin round-framed glasses, moon-white flowing robe, sitting half-submerged on a rolling sea of clouds, "
       "surrounded by green mountains and a great river waterfall, birds and blooming flowers, "
       "misty immortal paradise, 3d animated movie style, stylized render, warm golden volumetric light, "
       "cinematic lighting, highly detailed, masterpiece, best quality")
NEG = ("blackboard, chalkboard, classroom, whiteboard, text, letters, watermark, signature, logo, ui, frame, "
       "low quality, worst quality, blurry, jpeg artifacts, deformed face, disfigured, extra fingers, "
       "bad anatomy, photo, realistic skin texture, oversaturated")


def api(path, data=None, timeout=180):
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


def build(name_in, ref_in, W, H, pose_res, steps, cfg, denoise, use_lcm, sd, sp, iw,
          sd_end=0.85, sp_end=0.80):
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
    model_src = ["4", 0]
    if use_lcm:
        g["45"] = {"class_type": "LoraLoader",
                   "inputs": fill_defaults("LoraLoader", {"lora_name": LCM, "strength_model": 1.0,
                                                          "strength_clip": 1.0,
                                                          "model": ["4", 0], "clip": ["4", 1]})}
        model_src = ["45", 0]
        g["6"]["inputs"]["clip"] = ["45", 1]
        g["7"]["inputs"]["clip"] = ["45", 1]

    pos, neg = ["6", 0], ["7", 0]
    # 深度 ControlNet
    g["30"] = {"class_type": "DepthAnythingPreprocessor",
               "inputs": fill_defaults("DepthAnythingPreprocessor",
                                       {"image": ["21", 0], "resolution": W,
                                        "ckpt_name": "depth_anything_vits14.pth"})}
    g["32"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "control_v11f1p_sd15_depth.pth"}}
    g["50"] = {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": pos, "negative": neg, "control_net": ["32", 0],
                          "image": ["30", 0], "strength": sd, "start_percent": 0.0, "end_percent": sd_end}}
    pos, neg = ["50", 0], ["50", 1]
    # 姿态 ControlNet
    g["31"] = {"class_type": "DWPreprocessor",
               "inputs": fill_defaults("DWPreprocessor",
                                       {"image": ["21", 0], "resolution": pose_res,
                                        "detect_hand": "disable", "detect_body": "enable", "detect_face": "enable",
                                        "bbox_detector": "yolox_l.onnx", "pose_estimator": "dw-ll_ucoco_384.onnx"})}
    g["33"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "control_v11p_sd15_openpose.pth"}}
    g["51"] = {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": pos, "negative": neg, "control_net": ["33", 0],
                          "image": ["31", 0], "strength": sp, "start_percent": 0.0, "end_percent": sp_end}}
    pos, neg = ["51", 0], ["51", 1]
    # IPAdapter 角色锚定
    g["40"] = {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": model_src, "preset": "PLUS FACE (portraits)"}}
    g["41"] = {"class_type": "IPAdapterAdvanced",
               "inputs": {"model": ["40", 0], "ipadapter": ["40", 1], "image": ["22", 0],
                          "weight": iw, "weight_type": "ease in-out", "combine_embeds": "concat",
                          "start_at": 0.0, "end_at": 0.85, "embeds_scaling": "V only"}}

    g["3"] = {"class_type": "KSampler", "inputs": {"seed": 20261013, "steps": steps, "cfg": cfg,
                                                   "sampler_name": "lcm" if use_lcm else "dpmpp_2m",
                                                   "scheduler": "sgm_uniform" if use_lcm else "karras",
                                                   "denoise": denoise, "model": ["41", 0],
                                                   "positive": pos, "negative": neg,
                                                   "latent_image": ["12", 0]}}
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}}
    g["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "all_out", "images": ["8", 0]}}
    return g


def style_one(base, args, ref_name, dst_root):
    """返回 (成功?, 耗时秒)"""
    wf = build(f"{SUB}/{base}", f"{SUB}/{ref_name}", args.w, args.h, args.pose_res,
               args.steps, args.cfg, args.denoise, args.lcm, args.sd, args.sp, args.iw)
    out_png = os.path.join(args.out, base)
    t0 = time.time()
    pid = api("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})["prompt_id"]
    while time.time() - t0 < 1800:
        time.sleep(2)
        h = api(f"/history/{pid}")
        if pid not in h:
            continue
        st = h[pid]["status"]
        if st.get("completed"):
            for node, out in h[pid]["outputs"].items():
                for im in out.get("images", []):
                    if "all_out" not in im.get("filename", ""):
                        continue
                    q = ("?filename=" + urllib.parse.quote(im["filename"]) +
                         "&subfolder=" + urllib.parse.quote(im.get("subfolder", "")) +
                         "&type=" + im["type"])
                    with urllib.request.urlopen(HOST + "/view" + q, timeout=180) as resp, open(out_png, "wb") as f:
                        f.write(resp.read())
            return True, time.time() - t0
        if st.get("status_str") == "error":
            return False, time.time() - t0
    return False, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--denoise", type=float, default=0.70)
    ap.add_argument("--steps",   type=int,   default=26)
    ap.add_argument("--cfg",     type=float, default=7.0)
    ap.add_argument("--w",       type=int,   default=768)
    ap.add_argument("--h",       type=int,   default=576)
    ap.add_argument("--pose-res", dest="pose_res", type=int, default=768)
    ap.add_argument("--lcm",     type=int,   default=0)
    ap.add_argument("--sd",      type=float, default=0.70)
    ap.add_argument("--sp",      type=float, default=0.55)
    ap.add_argument("--iw",      type=float, default=0.85)
    ap.add_argument("--limit",   type=int,   default=0)
    ap.add_argument("--in",  dest="inp", default=os.path.join(PROJ, "03_关键帧_原图"))
    ap.add_argument("--out", dest="out", default=os.path.join(STY, "_正式输出"))
    args = ap.parse_args()
    args.lcm = bool(args.lcm)

    os.makedirs(args.out, exist_ok=True)
    # 清除可能残留的状态文件（否则守望脚本会误判为"已完成"）
    state_path = os.path.join(STY, "_style_state.json")
    if os.path.isfile(state_path):
        try:
            os.remove(state_path)
        except OSError:
            pass
    files = sorted(glob.glob(os.path.join(args.inp, "*.png")))
    if args.limit:
        files = files[:args.limit]

    # 断点续跑：跳过已完成的
    todo = [f for f in files if not (os.path.isfile(os.path.join(args.out, os.path.basename(f)))
                                     and os.path.getsize(os.path.join(args.out, os.path.basename(f))) > 20000)]
    done_n = len(files) - len(todo)

    print("=" * 74)
    print(f"整片风格化  {'LCM加速' if args.lcm else '标准'}  {args.w}x{args.h}  "
          f"{args.steps}步 cfg{args.cfg} denoise{args.denoise} pose{args.pose_res}")
    print(f"输入 {args.inp}   共 {len(files)} 张")
    print(f"输出 {args.out}")
    print(f"已完成 {done_n} 张，本次待跑 {len(todo)} 张")
    print("=" * 74)

    if not todo:
        print("✓ 全部已完成，无需处理")
        return 0

    ss = api("/system_stats")
    if ss.get("devices"):
        d = ss["devices"][0]
        print(f"GPU {d['name']}  空闲 VRAM {d['vram_free']/1024**3:.1f}/{d['vram_total']/1024**3:.1f} GB")

    dst_root = os.path.join(CIN, SUB)
    os.makedirs(dst_root, exist_ok=True)
    ref_name = "玄鹤先生_定妆参考.png"
    shutil.copy2(REF, os.path.join(dst_root, ref_name))

    t_start = time.time()
    ok = 0
    fail_list = []
    for i, fp in enumerate(todo, 1):
        base = os.path.basename(fp)
        shutil.copy2(fp, os.path.join(dst_root, base))
        succ = False
        for attempt in (1, 2):
            succ, dt = style_one(base, args, ref_name, dst_root)
            if succ:
                break
            time.sleep(3)
        if succ:
            ok += 1
        else:
            fail_list.append(base)
        el = time.time() - t_start
        avg = el / i
        eta = avg * (len(todo) - i)
        print(f"[{i}/{len(todo)}] {base} {'✓' if succ else '✗'} {dt:5.0f}s  "
              f"| 均 {avg:5.1f}s 已用 {el/60:5.1f}m 剩余约 {eta/60:5.1f}m", flush=True)

    print("=" * 74)
    print(f"完成 {ok}/{len(todo)}   总耗时 {(time.time()-t_start)/60:.1f} 分钟   平均 "
          f"{(time.time()-t_start)/max(1,len(todo)):.1f} 秒/张")
    if fail_list:
        print(f"失败 {len(fail_list)} 张: {fail_list[:20]}")
    print(f"→ {args.out}")
    # 状态写入
    with open(os.path.join(STY, "_style_state.json"), "w", encoding="utf-8") as f:
        json.dump({"total": len(files), "done_this_run": ok, "failed": fail_list,
                   "config": vars(args), "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                  f, ensure_ascii=False, indent=1)
    return 0 if not fail_list else 2


if __name__ == "__main__":
    sys.exit(main())
