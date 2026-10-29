# -*- coding: utf-8 -*-
"""生成《本地 AI 管线 v2 实测报告》：原图 vs 纯img2img vs ControlNet+IPAdapter"""
import os
import base64, os, subprocess

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tools\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe")
STY    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "04_关键帧_风格化")
ORIG   = os.path.join(STY, "_试跑_待处理")
V1_45  = os.path.join(STY, "_本地试跑")
V1_75  = os.path.join(STY, "_本地试跑_高重绘")
V2     = os.path.join(STY, "_本地试跑_CN_IPA")
OUT    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "本地AI管线v2实测报告.html")

def b64(p, q=82):
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", p, "-q:v", str(q), "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return base64.b64encode(r.stdout).decode()

META = [
    ("f000192.png", "全景·板书少",   "定基础构图与人物比例"),
    ("f000892.png", "写板书手势",    "手部还原考验"),
    ("f002352.png", "板书最多",      "板书策略主考题"),
    ("f017100.png", "正面·学生入镜", "定妆帧最佳候选"),
    ("f018172.png", "学生后脑勺入镜","弟子处理策略考题"),
]

def fig(b, cap, cls=""):
    if not b:
        return f'<figure class="{cls}"><div class="noimg">{cap}</div></figure>'
    return f'<figure class="{cls}"><img src="data:image/jpeg;base64,{b}"><figcaption>{cap}</figcaption></figure>'

cards = []
for name, scene, why in META:
    o  = b64(os.path.join(ORIG, name))
    a  = b64(os.path.join(V1_45, name))    if os.path.exists(os.path.join(V1_45, name)) else None
    c  = b64(os.path.join(V1_75, name))    if os.path.exists(os.path.join(V1_75, name)) else None
    v2 = b64(os.path.join(V2, name))       if os.path.exists(os.path.join(V2, name)) else None
    cards.append(f'''<section class="card">
<h3>{name.replace(".png","")} <span class="tag">{scene}</span> <span class="tag gray">{why}</span></h3>
<div class="grid4">
{fig(o,  "① 原图（真人教室）")}
{fig(a,  "② v1 纯img2img 0.45<br>几乎没变", "dim")}
{fig(c,  "③ v1 纯img2img 0.75<br>风格化但双影人、构图漂移", "dim") if c else fig(None, "③ v1 0.75（未跑）", "dim")}
{fig(v2, "④ v2 ControlNet+IPAdapter<br>风格化+构图锁定+角色锁定", "hot")}
</div></section>''')

HTML = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地 AI 管线 v2 实测报告（ControlNet + IPAdapter）</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#f6f4ef;color:#2c2a26;padding:32px 16px}}
.wrap{{max-width:1240px;margin:0 auto}}
h1{{font-size:25px;margin-bottom:6px}}
.sub{{color:#8a8378;margin-bottom:22px;font-size:14px}}
.verdict{{background:#fff;border:1px solid #e5ddce;border-left:5px solid #2e7d32;border-radius:10px;padding:20px 24px;margin-bottom:24px}}
.verdict h2{{font-size:17px;margin-bottom:10px;color:#2e7d32}}
.verdict p{{font-size:14.5px;line-height:1.95}}
.verdict b{{color:#111}}
.warn{{color:#c0392b;font-weight:bold}}
.card{{background:#fff;border:1px solid #e5ddce;border-radius:10px;padding:18px 20px;margin-bottom:22px}}
.card h3{{font-size:16px;margin-bottom:12px}}
.grid4{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
figure img{{width:100%;border-radius:6px;border:1px solid #ddd4c2;display:block}}
figcaption{{font-size:12px;color:#6f6a5f;margin-top:5px;line-height:1.5}}
figure.hot img{{border:3px solid #2e7d32}}
figure.hot figcaption{{color:#2e7d32;font-weight:bold}}
figure.dim figcaption{{color:#a39d90}}
.noimg{{background:#efece4;border:1px dashed #cfc7b4;border-radius:6px;height:140px;display:flex;align-items:center;justify-content:center;font-size:12px;color:#a39d90;text-align:center;padding:8px}}
.tag{{display:inline-block;background:#eef3ea;color:#4a7c3f;font-size:12px;padding:2px 10px;border-radius:20px;margin-left:6px;vertical-align:2px}}
.tag.gray{{background:#f0ede6;color:#8a8378}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}}
th,td{{border:1px solid #e5ddce;padding:8px 12px;text-align:left}}
th{{background:#f4f1e8}}
.ok{{color:#2e7d32;font-weight:bold}} .bad{{color:#c0392b;font-weight:bold}} .mid{{color:#b8860b;font-weight:bold}}
.sec{{background:#fff;border:1px solid #e5ddce;border-radius:10px;padding:18px 22px;margin-bottom:22px}}
.sec h2{{font-size:16px;margin-bottom:10px}}
.sec li{{font-size:14px;line-height:2;margin-left:20px}}
@media(max-width:860px){{.grid4{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<h1>本地 AI 管线 v2 实测报告 —— ControlNet + IPAdapter</h1>
<div class="sub">2026-10-13 16:30 ｜ 本地 GPU ｜ SD1.5 + DepthAnything + DWPose + IPAdapter(PLUS FACE) ｜ 768×576 ｜ 26步 dpmpp_2m/karras</div>

<div class="verdict">
<h2>结论：本地生产线「技术上走得通」，但当前配置不适合量产</h2>
<p><b>三个老问题全部解决</b>：双影人消失（姿态骨架只允许一个人）、站姿讲课被锁定（DWPose 姿态控制）、
圆框眼镜稳定出现（IPAdapter 角色锚定）。黑板区域被自然替换成山水楼阁，符合"板书消失→场景化"策略；
学生入镜也被正确保留为"弟子"。</p>
<p>但暴露 <b>3 个新问题</b>：① 场景语义不稳定——同一套提示词，有的帧画出"屋内挂画"，有的画出"露天楼台"，
连贯性不够；② 服装颜色逐帧漂移（青袍/黑袍随机）；③ <span class="warn">速度太慢</span>——单张约 135 秒
（DWPose 在 CPU 上跑 + 三模型串联），657 张要 <span class="warn">约 25 小时</span>。</p>
</div>

{''.join(cards)}

<section class="sec">
<h2>三档方案对比（同一张考题 f017100）</h2>
<table>
<tr><th>方案</th><th>风格化</th><th>构图/姿态</th><th>角色一致性</th><th>单张耗时</th><th>657 张估算</th><th>成本</th></tr>
<tr><td>v1 纯 img2img（denoise 0.45）</td><td class="bad">无</td><td class="ok">完美</td><td>原样（真人）</td><td>12 秒</td><td>2.2 小时</td><td>¥0</td></tr>
<tr><td>v1 纯 img2img（denoise 0.75）</td><td class="ok">有</td><td class="bad">漂移+双影人</td><td class="bad">丢失</td><td>12 秒</td><td>2.2 小时</td><td>¥0</td></tr>
<tr><td><b>v2 ControlNet+IPAdapter</b></td><td class="ok">有</td><td class="ok">锁定</td><td class="mid">大部分锁定（服装漂移）</td><td>135 秒</td><td class="warn">≈25 小时</td><td>¥0</td></tr>
</table>
</section>

<section class="sec">
<h2>本次补齐的管线清单</h2>
<ul>
<li><b>ControlNet v1.1</b>：control_v11p_sd15_openpose.pth（1.38GB）+ control_v11f1p_sd15_depth.pth（1.38GB）→ models/controlnet/</li>
<li><b>IPAdapter</b>：ip-adapter-plus_sd15 / ip-adapter-plus-face_sd15（各 93MB）→ models/ipadapter/；CLIP-ViT-H-14 图像编码器（2.4GB）→ models/clip_vision/</li>
<li><b>预处理器</b>：DepthAnything vits14（99MB）+ DWPose（yolox_l.onnx 217MB + dw-ll_ucoco_384.onnx 134MB）</li>
<li><b>自定义节点</b>：ComfyUI_IPAdapter_plus + comfyui_controlnet_aux（含 matplotlib / scikit-image / opencv / onnxruntime 依赖）</li>
<li>下载源实测：ModelScope 3.4MB/s &gt; gh-proxy.com 5MB/s（GitHub）&gt; HF经Clash 1.4MB/s &gt;&gt; hf-mirror 0.06MB/s（不可用）</li>
<li><b>工具脚本</b>：local_cn_ipa.py（v2 批量出图，支持 denoise/深度强度/姿态强度/IPA权重/目录/结束百分比参数）</li>
</ul>
</section>

<section class="sec">
<h2>下一步建议（按优先级）</h2>
<ul>
<li><b>提速（本地量产的前提）</b>：DWPose 换 torchscript 版走 GPU、或直接用 <b>SD1.5 + Turbo/LCM 蒸馏模型</b>（4-8 步出图，速度快 3-5 倍）；
   或降分辨率到 512×384。目标：单张 ≤30 秒，657 张 ≈ 5.5 小时。</li>
<li><b>连贯性</b>：服装颜色/场景语义靠「固定种子 + 更强的 IPAdapter 权重 + 给提示词加场景锁定句」继续调参；
   也可训练 LoRA（用 20-30 张定妆图）获得真正稳定的角色。</li>
<li><b>务实路线</b>：本地继续当<b>免费试构图/调参台</b>（现在能出的图已经很接近目标风格），正式量产仍走即梦云端（¥530-2000）。</li>
</ul>
</section>
</div></body></html>'''

with open(OUT, "w", encoding="utf-8") as f:
    f.write(HTML)
print("✓ 报告:", OUT, f"({os.path.getsize(OUT)//1024} KB)")
