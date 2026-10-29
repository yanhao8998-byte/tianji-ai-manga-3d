# -*- coding: utf-8 -*-
"""
脸部精修守护进程（治 denoise 0.78 下的脸部融化）
原理: 监视 _正式输出_v2 → YuNet 检测人脸 → 裁剪放大到 448px →
      低重绘度(0.30) + FACE-IPA 0.95 重画脸部 → 羽化贴回原帧（原子替换）
只动脸，不动道袍/场景。与 style_all_v2 并行跑（共享 ComfyUI 队列）。
全部 657 帧精修完成后写 _face_refine_done.json（守望脚本据此才组装）。
必须在 ComfyUI python_embeded 下运行。
"""
import cv2
import json
import numpy as np
import os
import shutil
import sys
import time
import urllib.parse
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_style2 as ls2   # 复用 api()/FACE_POS/NEG/CKPT/路径常量

PROJ = os.path.dirname(os.path.abspath(__file__))
STY = os.path.join(PROJ, "04_关键帧_风格化")
OUT = os.path.join(STY, "_正式输出_v2")
STATE = os.path.join(STY, "_face_refine_state.json")
DONE_FLAG = os.path.join(STY, "_face_refine_done.json")
DET = r"D:\AI\facedet\yunet2.onnx"   # cv2 读不了中文路径下的 ONNX，必须放英文目录
EXPECT = 657

FACE_SIZE = 448          # 脸部重绘分辨率
FACE_STEPS = 16
FACE_CFG = 5.0
FACE_DENOISE = 0.30
FACE_IW = 0.95           # FACE-IPA 权重
MIN_FACE = 56            # 小于该边长的脸不管（太小画不清）
MAX_FACES = 2            # 每帧最多精修几张脸


def imread_u(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_u(path, img):
    """中文路径安全写盘 + 原子替换"""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return False
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(buf.tobytes())
    os.replace(tmp, path)
    return True


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return set(json.load(f)["done"])
    except Exception:
        return set()


def save_state(done):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done), "ts": time.strftime("%F %T")}, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def detect_faces(img):
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(DET, "", (w, h), 0.5)
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    if faces is None:
        return []
    out = []
    for f in faces:
        x, y, fw, fh, conf = int(f[0]), int(f[1]), int(f[2]), int(f[3]), float(f[14])
        if fw < MIN_FACE:
            continue
        out.append((conf, x, y, fw, fh))
    out.sort(key=lambda t: -t[0])
    return out[:MAX_FACES]


def run_graph(g, out_png, timeout=900):
    pid = ls2.api("/prompt", {"prompt": g, "client_id": str(uuid.uuid4())})["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        h = ls2.api(f"/history/{pid}")
        if pid not in h:
            continue
        st = h[pid]["status"]
        if st.get("completed"):
            for node, o in h[pid]["outputs"].items():
                for im in o.get("images", []):
                    if "v2_face" not in im.get("filename", ""):
                        continue
                    q = ("?filename=" + urllib.parse.quote(im["filename"]) +
                         "&subfolder=" + urllib.parse.quote(im.get("subfolder", "")) +
                         "&type=" + im["type"])
                    with urllib.request.urlopen(ls2.HOST + "/view" + q, timeout=300) as resp, \
                            open(out_png, "wb") as f:
                        f.write(resp.read())
                    return True
            return False
        if st.get("status_str") == "error":
            for m in st.get("messages", [])[-2:]:
                print("    ", str(m)[:200], flush=True)
            return False
    return False


def refine_face_region(face_in_png, ref_name, seed, out_png):
    """裁剪好的脸 → 低重绘度重画 → 输出 png"""
    g = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ls2.CKPT}},
        "20": {"class_type": "LoadImage", "inputs": {"image": face_in_png}},
        "22": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ls2.FACE_POS, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": ls2.FACE_NEG, "clip": ["4", 1]}},
        "21": {"class_type": "ImageScale", "inputs": {"image": ["20", 0], "upscale_method": "lanczos",
                                                      "width": FACE_SIZE, "height": FACE_SIZE,
                                                      "crop": "center"}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["4", 2]}},
        "40": {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": ["4", 0], "preset": "PLUS FACE (portraits)"}},
        "41": {"class_type": "IPAdapterAdvanced",
               "inputs": {"model": ["40", 0], "ipadapter": ["40", 1], "image": ["22", 0],
                          "weight": FACE_IW, "weight_type": "ease in-out",
                          "combine_embeds": "concat", "start_at": 0.0, "end_at": 1.0,
                          "embeds_scaling": "V only"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": FACE_STEPS,
                                                   "cfg": FACE_CFG, "sampler_name": "dpmpp_2m",
                                                   "scheduler": "karras", "denoise": FACE_DENOISE,
                                                   "model": ["41", 0], "positive": ["6", 0],
                                                   "negative": ["7", 0], "latent_image": ["12", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "v2_face", "images": ["8", 0]}},
    }
    return run_graph(g, out_png)


def process_frame(path, ref_name, root_in):
    """对一帧做脸部精修（原地原子替换）。返回 'ok'/'noface'/'fail'"""
    img = imread_u(path)
    if img is None:
        return "fail"
    faces = detect_faces(img)
    if not faces:
        return "noface"
    H, W = img.shape[:2]
    patched = img.copy()
    n_done = 0
    for idx, (conf, x, y, fw, fh) in enumerate(faces):
        cx, cy = x + fw / 2, y + fh / 2
        ew, eh = min(fw * 2.2, W), min(fh * 2.1, H)          # 带发际线/下巴余量
        a1 = int(max(0, cx - ew / 2)); b1 = int(max(0, cy - eh * 0.55))
        a2 = int(min(W, a1 + ew)); b2 = int(min(H, b1 + eh))
        crop = img[b1:b2, a1:a2]
        if crop.size == 0 or min(crop.shape[:2]) < 32:
            continue
        name_in = f"_face_in_{idx}.png"
        ok, buf = cv2.imencode(".png", crop)
        if not ok:
            continue
        with open(os.path.join(root_in, name_in), "wb") as f:
            f.write(buf.tobytes())
        tmp_face = os.path.join(root_in, "_face_out_tmp.png")
        if not refine_face_region(f"{ls2.SUB}/{name_in}", f"{ls2.SUB}/{ref_name}", 20261013, tmp_face):
            continue
        new_face = cv2.imread(tmp_face)
        if new_face is None:
            continue
        new_face = cv2.resize(new_face, (a2 - a1, b2 - b1), interpolation=cv2.INTER_LANCZOS4)
        mask = np.zeros((b2 - b1, a2 - a1), np.float32)
        my = max(4, int((b2 - b1) * 0.14)); mx = max(4, int((a2 - a1) * 0.14))
        mask[my:-my, mx:-mx] = 1.0
        k = max(3, (min(my, mx) // 2) * 2 + 1)
        mask = cv2.GaussianBlur(mask, (k, k), 0)[..., None]
        roi = patched[b1:b2, a1:a2].astype(np.float32)
        merged = roi * (1 - mask) + new_face.astype(np.float32) * mask
        patched[b1:b2, a1:a2] = np.clip(merged, 0, 255).astype(np.uint8)
        n_done += 1
    if n_done == 0:
        return "fail"
    return "ok" if imwrite_u(path, patched) else "fail"


def main():
    os.makedirs(OUT, exist_ok=True)
    root_in = os.path.join(ls2.CIN, ls2.SUB)
    os.makedirs(root_in, exist_ok=True)
    ref_name = "玄鹤先生_定妆参考.png"
    shutil.copy2(ls2.REF, os.path.join(root_in, ref_name))

    if os.path.isfile(DONE_FLAG):
        print("精修已完成过，退出", flush=True)
        return 0

    done = load_state()
    print(f"脸部精修守护启动  已精修 {len(done)} 帧", flush=True)
    n_ok = n_noface = n_fail = 0
    while True:
        files = [f for f in os.listdir(OUT) if f.endswith(".png")]
        todo = [f for f in sorted(files)
                if f not in done and os.path.getmtime(os.path.join(OUT, f)) < time.time() - 30
                and os.path.getsize(os.path.join(OUT, f)) > 20000]
        for f in todo:
            t0 = time.time()
            r = process_frame(os.path.join(OUT, f), ref_name, root_in)
            done.add(f)
            save_state(done)
            if r == "ok":
                n_ok += 1
                print(f"  [精修] {f} ✓ {time.time()-t0:.0f}s", flush=True)
            elif r == "noface":
                n_noface += 1
            else:
                n_fail += 1
                print(f"  [精修] {f} ✗ 失败", flush=True)
        if len(files) >= EXPECT and all(f in done for f in files if f.endswith(".png")):
            with open(DONE_FLAG, "w", encoding="utf-8") as f:
                f.write(time.strftime("%F %T"))
            print(f"✓ 全部 {len(files)} 帧精修完成（ok {n_ok} / 无脸 {n_noface} / 失败 {n_fail}）", flush=True)
            return 0
        time.sleep(8)


if __name__ == "__main__":
    sys.exit(main())
