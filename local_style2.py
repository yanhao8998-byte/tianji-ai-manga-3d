# -*- coding: utf-8 -*-
"""
天纪项目 · 风格化引擎 v2（治"虚" + 治"面部失真"）

与 v1 的差别:
  1) 基础分辨率提高（默认 768x576，可到 896x672）
  2) denoise 降到 0.55-0.60、cfg 降到 5.0-5.5  → 保留原始五官结构，不再重绘坏脸
  3) 新增 hires 二次高清重绘：解码后放大再用低 denoise 轻采样 → 找回细节（治"太虚"）
  4) 可选面部精修：cv2 检测人脸 → 裁剪放大 → IPAdapter-Face 低denoise重绘 → 羽化贴回

必须在 ComfyUI 的 python_embeded 下运行（需要 cv2/PIL/numpy）。

用法:
  python local_style2.py --in 图.png --out 图_out.png [选项]
     --w 768 --h 576            基础分辨率
     --steps 28 --cfg 5.5 --denoise 0.60
     --pose-res 640 --sd 0.60 --sp 0.50 --iw 0.80
     --hires 1.0                二次重绘倍数（<=1 关闭；1.0/1.5/2.0）
     --hires-denoise 0.30 --hires-steps 12 --hires-sd 0.45 --hires-sp 0.40
     --face                     开启面部精修
     --face-denoise 0.35 --face-weight 0.9 --face-size 512
     --tag 名字                  输出文件后缀标记（批量用）
"""
import os
import argparse, json, os, shutil, sys, time, urllib.parse, urllib.request, uuid

HOST = "http://127.0.0.1:8189"
COMFY = r"D:\AI\ComfyUI_windows_portable\ComfyUI"
CIN = os.path.join(COMFY, "input")
PROJ = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(PROJ, "00_素材", "参考图_玄鹤先生_秘境.png")
CKPT = "v1-5-pruned-emaonly.safetensors"
SUB = "tianji_v2"

POS = ("a dignified elderly chinese sage, white hair tied in a topknot, long white beard, "
       "thin round-framed glasses, warm kind eyes, clear facial features, expressive eyes, "
       "wearing a flowing moon-white hanfu robe with wide sleeves and a pale green sash, "
       "sitting half-submerged on a rolling sea of clouds, "
       "surrounded by green mountains and a great river waterfall, birds and blooming flowers, "
       "misty immortal paradise, 3d animated movie style, pixar style character design, "
       "stylized render, warm golden volumetric light, cinematic lighting, "
       "sharp focus, highly detailed face, masterpiece, best quality")
NEG = ("blackboard, chalkboard, classroom, whiteboard, text, letters, watermark, signature, logo, ui, frame, "
       "dark suit, business suit, modern clothing, jacket, coat, "
       "low quality, worst quality, blurry, soft focus, jpeg artifacts, deformed face, disfigured, "
       "distorted face, extra eyes, cross-eyed, mutated hands, extra fingers, bad anatomy, "
       "photo, realistic skin texture, oversaturated, plastic skin")
FACE_POS = ("detailed face portrait of a dignified elderly chinese sage, white hair topknot, long white beard, "
            "thin round-framed glasses, warm kind eyes, well-defined facial features, "
            "3d animated movie style character, clean render, sharp focus, masterpiece, best quality")
FACE_NEG = ("deformed face, distorted features, blurry, soft, low quality, asymmetric eyes, "
            "extra eyes, bad anatomy, jpeg artifacts, photo, realistic skin")


def api(path, data=None, timeout=600):
    req = urllib.request.Request(HOST + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace").strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} {path}\n{body[:1500]}")
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


def add_stage(g, src_img, ref_img, model_src, W, H, pose_res, steps, cfg, denoise,
              sd, sp, iw, seed, base_id, sd_end, sp_end, ipa_end, tag, bw=0.0):
    """往图里加一段「控制网 + IPAdapter + 采样」，返回 (最终图节点, 下一个可用id)"""
    n = base_id
    def nid():
        nonlocal n
        n += 1
        return str(n)

    enc = nid()
    g[enc] = {"class_type": "ImageScale", "inputs": {"image": src_img, "upscale_method": "lanczos",
                                                     "width": W, "height": H, "crop": "disabled"}}
    pos, neg = nid(), nid()
    g[pos] = {"class_type": "CLIPTextEncode", "inputs": {"text": POS, "clip": model_src[1]}}
    g[neg] = {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": model_src[1]}}
    lat = nid()
    g[lat] = {"class_type": "VAEEncode", "inputs": {"pixels": [enc, 0], "vae": model_src[2]}}

    # 深度
    d_pre = nid(); d_ld = nid(); d_ap = nid()
    g[d_pre] = {"class_type": "DepthAnythingPreprocessor",
                "inputs": fill_defaults("DepthAnythingPreprocessor",
                                        {"image": [enc, 0], "resolution": W,
                                         "ckpt_name": "depth_anything_vits14.pth"})}
    g[d_ld] = {"class_type": "ControlNetLoader",
               "inputs": {"control_net_name": "control_v11f1p_sd15_depth.pth"}}
    g[d_ap] = {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": [pos, 0], "negative": [neg, 0], "control_net": [d_ld, 0],
                          "image": [d_pre, 0], "strength": sd, "start_percent": 0.0, "end_percent": sd_end}}
    # 姿态
    p_pre = nid(); p_ld = nid(); p_ap = nid()
    g[p_pre] = {"class_type": "DWPreprocessor",
                "inputs": fill_defaults("DWPreprocessor",
                                        {"image": [enc, 0], "resolution": pose_res,
                                         "detect_hand": "disable", "detect_body": "enable",
                                         "detect_face": "enable",
                                         "bbox_detector": "yolox_l.onnx",
                                         "pose_estimator": "dw-ll_ucoco_384.onnx"})}
    g[p_ld] = {"class_type": "ControlNetLoader",
               "inputs": {"control_net_name": "control_v11p_sd15_openpose.pth"}}
    g[p_ap] = {"class_type": "ControlNetApplyAdvanced",
               "inputs": {"positive": [d_ap, 0], "negative": [d_ap, 1], "control_net": [p_ld, 0],
                          "image": [p_pre, 0], "strength": sp, "start_percent": 0.0, "end_percent": sp_end}}
    # IPAdapter：脸部锚（PLUS FACE）必开；bw>0 时再串一个全身形象锚（PLUS），
    # 把参考图的服装/造型（月白道袍）也传过去 —— 双 IPAdapter 串联是标准做法
    ip_l = nid(); ip_a = nid()
    g[ip_l] = {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": model_src[0],
                          "preset": "PLUS FACE (portraits)"}}
    g[ip_a] = {"class_type": "IPAdapterAdvanced",
               "inputs": {"model": [ip_l, 0], "ipadapter": [ip_l, 1], "image": ref_img,
                          "weight": iw, "weight_type": "ease in-out", "combine_embeds": "concat",
                          "start_at": 0.0, "end_at": ipa_end, "embeds_scaling": "V only"}}
    model_out = [ip_a, 0]
    if bw > 0.01:
        b_l = nid(); b_a = nid()
        g[b_l] = {"class_type": "IPAdapterUnifiedLoader",
                  "inputs": {"model": [ip_a, 0], "preset": "PLUS (high strength)"}}
        g[b_a] = {"class_type": "IPAdapterAdvanced",
                  "inputs": {"model": [b_l, 0], "ipadapter": [b_l, 1], "image": ref_img,
                             "weight": bw, "weight_type": "ease in-out", "combine_embeds": "concat",
                             "start_at": 0.0, "end_at": min(1.0, ipa_end), "embeds_scaling": "V only"}}
        model_out = [b_a, 0]
    ks = nid(); dec = nid()
    g[ks] = {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": cfg,
                                                  "sampler_name": "dpmpp_2m", "scheduler": "karras",
                                                  "denoise": denoise, "model": model_out,
                                                  "positive": [p_ap, 0], "negative": [p_ap, 1],
                                                  "latent_image": [lat, 0]}}
    g[dec] = {"class_type": "VAEDecode", "inputs": {"samples": [ks, 0], "vae": model_src[2]}}
    return dec, n


def build(name_in, ref_in, o):
    """返回 (graph, 最终图节点id)"""
    g = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "20": {"class_type": "LoadImage", "inputs": {"image": name_in}},
        "22": {"class_type": "LoadImage", "inputs": {"image": ref_in}},
    }
    model_src = (["4", 0], ["4", 1], ["4", 2])   # model, clip, vae

    # ---- 第一段：基础风格化 ----
    img, n = add_stage(g, ["20", 0], ["22", 0], model_src, o.w, o.h, o.pose_res, o.steps, o.cfg,
                       o.denoise, o.sd, o.sp, o.iw, o.seed, 29, o.sd_end, o.sp_end, o.ipa_end,
                       "base", bw=o.bw)

    # ---- 第二段：hires 二次高清重绘 ----
    if o.hires > 0.01:
        hw, hh = int(round(o.w * o.hires / 8) * 8), int(round(o.h * o.hires / 8) * 8)
        img2, n = add_stage(g, [img, 0], ["22", 0], model_src, hw, hh, min(1024, max(o.pose_res, hw // 2)),
                            o.hires_steps, o.hires_cfg, o.hires_denoise, o.hires_sd, o.hires_sp,
                            o.hires_iw, o.seed + 1, n, 0.65, 0.60, 0.70, "hires", bw=o.bw * 0.8)
        img = img2

    g["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "v2_out", "images": [img, 0]}}
    return g


def run_graph(g, out_png, timeout=1800):
    t0 = time.time()
    pid = api("/prompt", {"prompt": g, "client_id": str(uuid.uuid4())})["prompt_id"]
    while time.time() - t0 < timeout:
        time.sleep(2)
        h = api(f"/history/{pid}")
        if pid not in h:
            continue
        st = h[pid]["status"]
        if st.get("completed"):
            for node, out in h[pid]["outputs"].items():
                for im in out.get("images", []):
                    if "v2_out" not in im.get("filename", ""):
                        continue
                    q = ("?filename=" + urllib.parse.quote(im["filename"]) +
                         "&subfolder=" + urllib.parse.quote(im.get("subfolder", "")) +
                         "&type=" + im["type"])
                    with urllib.request.urlopen(HOST + "/view" + q, timeout=300) as resp, \
                            open(out_png, "wb") as f:
                        f.write(resp.read())
                    return True, time.time() - t0
            return False, time.time() - t0
        if st.get("status_str") == "error":
            print("  ✗ 工作流错误")
            for m in st.get("messages", [])[-3:]:
                print("    ", str(m)[:300])
            return False, time.time() - t0
    return False, time.time() - t0


# ---------------- 面部精修 ----------------
def detect_face(img_path):
    """返回 (x1,y1,x2,y2) 或 None"""
    import cv2
    img = cv2.imread(img_path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        cc = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        if cc.empty():
            continue
        faces = cc.detectMultiScale(gray, 1.08, 6, minSize=(48, 48))
        if len(faces):
            # 取最大的
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            return int(x), int(y), int(x + w), int(y + h)
    return None


def face_refine(img_path, ref_name, o, out_path):
    """裁剪人脸 → 放大重绘 → 羽化贴回"""
    import cv2
    import numpy as np
    box = detect_face(img_path)
    if box is None:
        return False
    img = cv2.imread(img_path)
    H, W = img.shape[:2]
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    fw, fh = (x2 - x1) * 1.9, (y2 - y1) * 2.1          # 带发际线与下巴的余量
    fw, fh = min(fw, W), min(fh, H)
    a1 = int(max(0, cx - fw / 2)); b1 = int(max(0, cy - fh * 0.55))
    a2 = int(min(W, a1 + fw)); b2 = int(min(H, b1 + fh))
    crop = img[b1:b2, a1:a2]
    if crop.size == 0 or min(crop.shape[:2]) < 32:
        return False

    # 存到 ComfyUI input
    face_in = os.path.join(CIN, SUB, "_face_in.png")
    os.makedirs(os.path.dirname(face_in), exist_ok=True)
    cv2.imwrite(face_in, crop)

    g = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "20": {"class_type": "LoadImage", "inputs": {"image": f"{SUB}/_face_in.png"}},
        "22": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": FACE_POS, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": FACE_NEG, "clip": ["4", 1]}},
        "21": {"class_type": "ImageScale", "inputs": {"image": ["20", 0], "upscale_method": "lanczos",
                                                      "width": o.face_size, "height": o.face_size,
                                                      "crop": "center"}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["4", 2]}},
        "40": {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": ["4", 0], "preset": "PLUS FACE (portraits)"}},
        "41": {"class_type": "IPAdapterAdvanced",
               "inputs": {"model": ["40", 0], "ipadapter": ["40", 1], "image": ["22", 0],
                          "weight": o.face_weight, "weight_type": "ease in-out",
                          "combine_embeds": "concat", "start_at": 0.0, "end_at": 1.0,
                          "embeds_scaling": "V only"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": o.seed + 7, "steps": o.face_steps,
                                                   "cfg": o.face_cfg, "sampler_name": "dpmpp_2m",
                                                   "scheduler": "karras", "denoise": o.face_denoise,
                                                   "model": ["41", 0], "positive": ["6", 0],
                                                   "negative": ["7", 0], "latent_image": ["12", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "v2_face", "images": ["8", 0]}},
    }
    tmp = os.path.join(CIN, SUB, "_face_out.png")
    p = api("/prompt", {"prompt": g, "client_id": str(uuid.uuid4())})["prompt_id"]
    t0 = time.time(); ok = False
    while time.time() - t0 < 900:
        time.sleep(2)
        h = api(f"/history/{p}")
        if p not in h:
            continue
        st = h[p]["status"]
        if st.get("completed"):
            for node, out in h[p]["outputs"].items():
                for im in out.get("images", []):
                    if "v2_face" not in im.get("filename", ""):
                        continue
                    q = ("?filename=" + urllib.parse.quote(im["filename"]) +
                         "&subfolder=" + urllib.parse.quote(im.get("subfolder", "")) +
                         "&type=" + im["type"])
                    with urllib.request.urlopen(HOST + "/view" + q, timeout=300) as resp, \
                            open(tmp, "wb") as f:
                        f.write(resp.read())
                    ok = True
            break
        if st.get("status_str") == "error":
            return False
    if not ok:
        return False

    new_face = cv2.imread(tmp)
    if new_face is None:
        return False
    new_face = cv2.resize(new_face, (a2 - a1, b2 - b1), interpolation=cv2.INTER_LANCZOS4)

    # 羽化蒙版合成
    mask = np.zeros((b2 - b1, a2 - a1), np.float32)
    my = max(4, int((b2 - b1) * 0.16)); mx = max(4, int((a2 - a1) * 0.16))
    mask[my:-my, mx:-mx] = 1.0
    k = max(3, (min(my, mx) // 2) * 2 + 1)
    mask = cv2.GaussianBlur(mask, (k, k), 0)[..., None]
    roi = img[b1:b2, a1:a2].astype(np.float32)
    merged = roi * (1 - mask) + new_face.astype(np.float32) * mask
    img[b1:b2, a1:a2] = np.clip(merged, 0, 255).astype(np.uint8)
    cv2.imwrite(out_path, img)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--w", type=int, default=768)
    ap.add_argument("--h", type=int, default=576)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--cfg", type=float, default=5.5)
    ap.add_argument("--denoise", type=float, default=0.60)
    ap.add_argument("--pose-res", dest="pose_res", type=int, default=640)
    ap.add_argument("--sd", type=float, default=0.60)
    ap.add_argument("--sp", type=float, default=0.50)
    ap.add_argument("--iw", type=float, default=0.80)
    ap.add_argument("--sd-end", dest="sd_end", type=float, default=0.80)
    ap.add_argument("--sp-end", dest="sp_end", type=float, default=0.75)
    ap.add_argument("--ipa-end", dest="ipa_end", type=float, default=0.80)
    ap.add_argument("--bw", type=float, default=0.0,
                    help="全身形象锚（PLUS）权重：>0 时把参考图服装/造型也传过去")
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--hires", type=float, default=0.0)
    ap.add_argument("--hires-denoise", dest="hires_denoise", type=float, default=0.30)
    ap.add_argument("--hires-steps", dest="hires_steps", type=int, default=12)
    ap.add_argument("--hires-cfg", dest="hires_cfg", type=float, default=5.0)
    ap.add_argument("--hires-sd", dest="hires_sd", type=float, default=0.45)
    ap.add_argument("--hires-sp", dest="hires_sp", type=float, default=0.40)
    ap.add_argument("--hires-iw", dest="hires_iw", type=float, default=0.75)
    ap.add_argument("--face", action="store_true")
    ap.add_argument("--face-size", dest="face_size", type=int, default=512)
    ap.add_argument("--face-denoise", dest="face_denoise", type=float, default=0.35)
    ap.add_argument("--face-steps", dest="face_steps", type=int, default=20)
    ap.add_argument("--face-cfg", dest="face_cfg", type=float, default=5.0)
    ap.add_argument("--face-weight", dest="face_weight", type=float, default=0.9)
    ap.add_argument("--save-base", dest="save_base", action="store_true")
    o = ap.parse_args()

    root = os.path.join(CIN, SUB)
    os.makedirs(root, exist_ok=True)
    base = os.path.basename(o.inp)
    dst = os.path.join(root, base)
    if os.path.abspath(o.inp) != os.path.abspath(dst):
        shutil.copy2(o.inp, dst)
    ref_name = "玄鹤先生_定妆参考.png"
    shutil.copy2(REF, os.path.join(root, ref_name))

    g = build(f"{SUB}/{base}", f"{SUB}/{ref_name}", o)
    ok, dt = run_graph(g, o.out)
    if not ok:
        print(f"✗ 失败 {base}")
        return 1
    print(f"✓ 基础{'+hires' if o.hires > 0.01 else ''}  {dt:.0f}s  → {o.out}")

    if o.face:
        t1 = time.time()
        if face_refine(o.out, ref_name, o, o.out):
            print(f"✓ 面部精修  {time.time() - t1:.0f}s（已贴回）")
        else:
            print("· 面部精修：未检测到人脸，跳过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
