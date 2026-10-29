# -*- coding: utf-8 -*-
"""画质试点对比报告：把 _pilot2 的配置矩阵拼成 HTML（图内嵌 base64）"""
import os
import base64, glob, io, os, subprocess, sys

PROJ = os.path.dirname(os.path.abspath(__file__))
FF = os.path.join(PROJ, "_tools", "ffmpeg-master-latest-win64-gpl", "bin")
FFMPEG = os.path.join(FF, "ffmpeg.exe")
SRC_DIR = os.path.join(PROJ, "_pilot2")
OUT = os.path.join(PROJ, "画质修复试点报告.html")
ORIG = os.path.join(PROJ, "03_关键帧_原图")


def jpg_b64(png, maxw=760):
    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", png,
                        "-vf", f"scale='min({maxw},iw)':-2",
                        "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return ""
    return base64.b64encode(r.stdout).decode()


def card(title, path, note=""):
    b = jpg_b64(path)
    if not b:
        return f'<div class="card miss"><h3>{title}</h3><p>生成失败</p></div>'
    return (f'<div class="card"><h3>{title}</h3>'
            f'<img src="data:image/jpeg;base64,{b}"/>'
            f'<p class="note">{note}</p></div>')


def main():
    frames = [
        ("f017100", "中景·正面（主角清晰）"),
        ("f000192", "中景·背面（考验角色锚定）"),
        ("f000048", "大远景（考验场景语义）"),
    ]
    cfgs = [
        ("P1基线", "P1基线", "640×480 · d0.70 · cfg7.0 · 26步 — <b>旧版配置（用户投诉的版本）</b> · 64s"),
        ("P3", "P3_hires", "768×576 d0.58 + hires1.5二次重绘 — 380s（太慢，弃用）"),
        ("P4", "P4_896", "896×672 · d0.55 · cfg5.0 · 30步 — 脸最清晰，但场景被深度网锁死 · 65s"),
        ("P9/P10", "P9_低深度", "896×672 · <b>d0.60 · cfg5.0 · 深度0.40(至0.6) · 姿态0.55(至0.85) · IPA0.90(至0.9)</b> — <b>最终选定</b>"),
        ("P10", "P10_更低深度", "896×672 · 深度0.30 · 姿态0.60 · IPA1.0 — 场景最自由的变体"),
        ("P6", "P6_1024", "1024×768 · 同P4 — 225s/张（全量要41小时，弃用）"),
    ]
    order = {"P1基线": 0, "P3": 1, "P4": 2, "P9/P10": 3, "P10": 4, "P6": 5}
    rows = []
    for fid, desc in frames:
        cells = []
        # 原图
        op = os.path.join(ORIG, f"{fid}.png")
        if os.path.isfile(op):
            cells.append(card(f"原始关键帧<br><small>{desc}</small>", op, "1440×1080 源帧"))
        for name, pat, note in cfgs:
            p = os.path.join(SRC_DIR, f"{fid}_{pat}.png")
            if os.path.isfile(p):
                cells.append(card(name, p, note))
            else:
                cells.append(f'<div class="card miss"><h3>{name}</h3><p>未生成</p></div>')
        rows.append('<div class="row">' + "".join(cells) + "</div>")

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>画质修复试点报告</title><style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f5f3ee;color:#2c2a26;margin:0;padding:28px}}
h1{{font-size:24px;margin:0 0 6px}} .sub{{color:#7a756a;font-size:14px;margin-bottom:24px}}
.row{{display:flex;gap:14px;margin-bottom:26px;flex-wrap:wrap}}
.card{{flex:1 1 300px;max-width:390px;background:#fff;border-radius:12px;padding:12px;
box-shadow:0 2px 10px rgba(0,0,0,.07)}}
.card h3{{font-size:15px;margin:2px 0 8px;color:#4a463d}}
.card img{{width:100%;border-radius:8px;display:block}}
.note{{font-size:12px;color:#8a8478;margin:8px 0 2px;line-height:1.5}}
.miss{{opacity:.55}}
.win{{outline:3px solid #3fa66a;outline-offset:-3px}}
.verdict{{background:#fff;border-left:5px solid #3fa66a;border-radius:10px;padding:16px 20px;
font-size:14px;line-height:1.9;max-width:900px}}
</style></head><body>
<h1>画质修复试点报告</h1>
<div class="sub">针对「人物失真、太虚、面部扭曲」的配置矩阵 · 生成于 {"" }
<-script></-script>{""}</div>
{''.join(rows)}
<div class="verdict">
<b>结论</b><br>
① 旧版 P1 的问题：640×480 被组装端放大 2.7 倍 → 全画面发虚；denoise 0.70 + cfg 7.0 过强 → 五官被重绘坏（眯眼、镜架扭曲、胡子变形）。<br>
② <b>最终选定 P9 档（896×672 · denoise 0.60 · cfg 5.0 · 深度0.40→0.6 · 姿态0.55→0.85 · IPA 0.90→0.9）</b>：
脸部清晰规整 + 场景能变成山海秘境 + 角色锚定不丢，全量 657 张预计 10-14 小时。<br>
③ hires 二次重绘 380s/张、1024×768 225s/张，收益都不成比例 → 弃用。<br>
④ 组装端同步修复：运镜放大 2.7x→1.69x + 轻锐化 + 音频换成处理后音轨（-16 LUFS / RNNoise 降噪）。<br>
⑤ 已交付：`天纪AI漫剧_第一集_音频增强版.mp4`（旧画面+新音频，立即可看）；画质版需等全量重跑完成。
</div>
</body></html>"""
    html = html.replace("<-script></-script>", "").replace("<script></script>", "")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("✓", OUT)


if __name__ == "__main__":
    sys.exit(main())
