# -*- coding: utf-8 -*-
"""
paths.py — 项目路径与外部依赖的集中配置

设计原则（AGENTS.md 第五节）：
- 一切路径都从脚本自身位置推导，不写死盘符
- 外部依赖（ComfyUI、ffmpeg、源视频）用环境变量覆盖，未设置时用合理默认
- 源视频这类体积大、位置随机的资源，通过 TIANJI_SRC_VIDEO 指定

环境变量一览：
  TIANJI_SRC_VIDEO   源视频完整路径（默认：项目下 00_素材/源视频.mp4）
  TIANJI_COMFY_DIR   ComfyUI 目录（默认：D:\\AI\\ComfyUI_windows_portable）
  TIANJI_FACEDET     人脸检测模型目录（默认：D:\\AI\\facedet）
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── 源视频 ───────────────────────────────────────────
# 源视频体积大、不入库。位置由环境变量指定；
# 未指定时指向项目内的 00_素材/源视频.mp4（需自备）。
SRC_VIDEO = os.environ.get(
    "TIANJI_SRC_VIDEO",
    os.path.join(ROOT, "00_素材", "源视频.mp4"),
)

# ── 外部工具链（不在项目内，需单独安装）────────────────
COMFY_DIR = os.environ.get(
    "TIANJI_COMFY_DIR",
    r"D:\AI\ComfyUI_windows_portable",
)
COMFY_PYTHON = os.path.join(COMFY_DIR, "python_embeded", "python.exe")
COMFY_PORT = int(os.environ.get("TIANJI_COMFY_PORT", "8189"))

FACEDET_DIR = os.environ.get("TIANJI_FACEDET", r"D:\AI\facedet")
FACEDET_MODEL = os.path.join(FACEDET_DIR, "yunet2.onnx")

# ── 项目内目录 ───────────────────────────────────────
MATERIAL_DIR = os.path.join(ROOT, "00_素材")
GUIDE_DIR = os.path.join(ROOT, "01_引导视频")
SHOT_DIR = os.path.join(ROOT, "02_镜头分段")
KEYFRAME_DIR = os.path.join(ROOT, "03_关键帧_原图")
STYLED_DIR = os.path.join(ROOT, "04_关键帧_风格化")
AUDIO_DIR = os.path.join(ROOT, "_音频处理")
FILM_DIR = os.path.join(ROOT, "06_成片")
LOG_DIR = os.path.join(ROOT, "logs")
TOOLS_DIR = os.path.join(ROOT, "_tools")

# ffmpeg / ffprobe（项目内自带）
FFMPEG_DIR = os.path.join(TOOLS_DIR, "ffmpeg-master-latest-win64-gpl", "bin")
FFMPEG = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
FFPROBE = os.path.join(FFMPEG_DIR, "ffprobe.exe")

# rnnoise（cwd 用，避开盘符冒号）
RN_DIR = os.path.join(TOOLS_DIR, "rnnoise")
