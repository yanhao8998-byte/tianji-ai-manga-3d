# -*- coding: utf-8 -*-
"""生成《本地 ComfyUI 试构图对比报告》HTML：原图 vs denoise0.45 vs denoise0.75"""
import base64, io, os, subprocess, sys, glob

FFMPEG = r"<项目根>\_tools\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
STYLED = r"<项目根>\04_关键帧_风格化"
ORIG   = os.path.join(STYLED, "_试跑_待处理")
D45    = os.path.join(STYLED, "_本地试跑")
D75    = os.path.join(STYLED, "_本地试跑_高重绘")
OUT    = r"<项目根>\本地试构图对比报告.html"

def to_jpg_b64(png_path, q=82):
    """ffmpeg 解码 PNG → JPEG(q) → base64（走 subprocess 规避中文路径 argv 坑）"""
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", png_path,
           "-q:v", str(q), "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(p.stderr.decode(errors="replace")[:200])
    return base64.b64encode(p.stdout).decode()

META = [
    ("f000192.png", "全景·板书少", "定基础构图与人物比例"),
    ("f000892.png", "侧身写板书手势", "手部还原考验"),
    ("f002352.png", "板书最多", "板书策略的主考题"),
    ("f017100.png", "正面讲课·板书环绕", "定妆帧最佳候选"),
    ("f018172.png", "学生后脑勺入镜", "弟子处理策略考题"),
]

imgs = {}
for name, _, _ in META:
    imgs[name] = {"orig": to_jpg_b64(os.path.join(ORIG, name))}
    p45 = os.path.join(D45, name)
    if os.path.exists(p45):
        imgs[name]["d45"] = to_jpg_b64(p45)
p75 = os.path.join(D75, "f017100.png")
if os.path.exists(p75):
    imgs["f017100.png"]["d75"] = to_jpg_b64(p75)

def img_tag(b64, cap, cls=""):
    return f'<figure class="{cls}"><img src="data:image/jpeg;base64,{b64}"><figcaption>{cap}</figcaption></figure>'

cards = []
for name, scene, why in META:
    d = imgs[name]
    if "d75" in d:
        body = (img_tag(d["orig"], "① 原图（真人教室）") +
                img_tag(d["d45"], "② denoise 0.45｜几乎没变，提示词无效") +
                img_tag(d["d75"], "③ denoise 0.75｜风格化成功，但出现双影人、构图漂移", "hot"))
        cards.append(f'''<section class="card wide">
  <h3>{name.replace(".png","")} <span class="tag">{scene}</span> <span class="tag gray">{why}</span></h3>
  <div class="tri">{body}</div>
  <p class="note">考题结论：0.45 档完全没风格化；0.75 档人物换成仙风道骨长老+山海秘境成功，但 <b>原图 1 人变 2 人</b>（原图人物残影被"补"成了第二个仙人）、圆框眼镜丢失、站姿讲课时坐姿。</p>
</section>''')
    else:
        body = (img_tag(d["orig"], "① 原图（真人教室）") +
                img_tag(d["d45"], "② denoise 0.45｜仅轻微柔化"))
        cards.append(f'''<section class="card">
  <h3>{name.replace(".png","")} <span class="tag">{scene}</span> <span class="tag gray">{why}</span></h3>
  <div class="duo">{body}</div>
</section>''')

HTML = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地 ComfyUI 试构图对比报告</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#f6f4ef;color:#2c2a26;padding:32px 16px}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:26px;margin-bottom:6px}}
.sub{{color:#8a8378;margin-bottom:24px;font-size:14px}}
.verdict{{background:#fff;border:1px solid #e5ddce;border-left:5px solid #c0392b;border-radius:10px;padding:20px 24px;margin-bottom:24px}}
.verdict h2{{font-size:17px;margin-bottom:10px;color:#c0392b}}
.verdict p{{font-size:14.5px;line-height:1.9}}
.verdict b{{color:#1a1a1a}}
.card{{background:#fff;border:1px solid #e5ddce;border-radius:10px;padding:18px 20px;margin-bottom:22px}}
.card h3{{font-size:16px;margin-bottom:12px}}
.tri{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}
.duo{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}}
figure img{{width:100%;border-radius:6px;border:1px solid #ddd4c2;display:block}}
figcaption{{font-size:12.5px;color:#6f6a5f;margin-top:6px;line-height:1.5}}
figure.hot img{{border:2px solid #c0392b}}
figure.hot figcaption{{color:#c0392b;font-weight:bold}}
.tag{{display:inline-block;background:#eef3ea;color:#4a7c3f;font-size:12px;padding:2px 10px;border-radius:20px;margin-left:6px;vertical-align:2px}}
.tag.gray{{background:#f0ede6;color:#8a8378}}
.note{{font-size:13.5px;color:#5c574d;line-height:1.8;margin-top:12px;background:#faf8f2;border-radius:6px;padding:10px 14px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}}
th,td{{border:1px solid #e5ddce;padding:8px 12px;text-align:left}}
th{{background:#f4f1e8}}
.next{{background:#fff;border:1px solid #e5ddce;border-radius:10px;padding:20px 24px;margin-top:4px}}
.next h2{{font-size:17px;margin-bottom:12px}}
.next ol{{margin-left:20px;font-size:14.5px;line-height:2.1}}
.ok{{color:#2e7d32;font-weight:bold}} .bad{{color:#c0392b;font-weight:bold}}
@media(max-width:760px){{.tri,.duo{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>本地 ComfyUI 试构图对比报告</h1>
<div class="sub">2026-10-13 15:26 ｜ 本地 GPU ｜ SD1.5 (v1-5-pruned-emaonly) 768×576 ｜ 22步 dpmpp_2m/karras ｜ 每张约 12 秒</div>

<div class="verdict">
<h2>一句话结论</h2>
<p>本机 ComfyUI <b>能跑、速度合格</b>（12 秒/张，657 张全跑约 2.2 小时、零成本），但 <b>纯 img2img 存在两难</b>：
重绘幅度低（0.45）时画面几乎不动、提示词不起作用——还是真人教室；
重绘幅度高（0.75）时风格化成功——真人确实变成了仙风道骨的长老+山海秘境，
但<b>构图漂移、单人变双人、眼镜丢失</b>。要本地走通正式流程，必须补 <b>ControlNet（锁姿态构图）+ IPAdapter（锁角色长相）</b>，模型约 2–3 GB，本机显存可承受。</p>
</div>

{''.join(cards)}

<section class="card">
<h3>实测数据</h3>
<table>
<tr><th>档位</th><th>风格化</th><th>构图/姿态保持</th><th>人物身份</th><th>单张耗时</th><th>657 张成本</th></tr>
<tr><td>denoise 0.45</td><td class="bad">几乎为零</td><td class="ok">完全保持</td><td>原样（真人）</td><td>12 秒</td><td>¥0 / 2.2 小时</td></tr>
<tr><td>denoise 0.75</td><td class="ok">成功</td><td class="bad">漂移（站→坐、板书消失）</td><td class="bad">走样（双影、丢眼镜）</td><td>12 秒</td><td>¥0 / 2.2 小时</td></tr>
</table>
</section>

<div class="next">
<h2>下一步三条路（按推荐排序）</h2>
<ol>
<li><b>补齐本地管线（推荐先做）</b>：下载 ControlNet OpenPose+Depth（约 1.4GB）+ IPAdapter（约 1GB），
用「高重绘 0.7 + ControlNet 锁姿态 + IPAdapter 锁玄鹤先生脸」重跑这 5 张考题——这是本地能不能当正式生产线的决定性测试。</li>
<li><b>本地只当免费试构图台</b>：正式 657 张仍走即梦云端（¥530–2000），本地用来快速验证提示词与策略，省钱但不省事。</li>
<li><b>混合</b>：静止镜头段（占比大）用本地批量，动作段与特写走云端。</li>
</ol>
</div>
</div></body></html>'''

with open(OUT, "w", encoding="utf-8") as f:
    f.write(HTML)
print("✓ 报告已生成:", OUT, f"({os.path.getsize(OUT)//1024} KB)")
