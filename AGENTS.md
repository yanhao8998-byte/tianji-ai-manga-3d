# AGENTS.md — 天纪 AI 漫剧 3D · AI 协作契约

> 这份文件是给 AI 看的**项目铁律**。任何 AI（Codex / WorkBuddy / 其他代理）接手本项目时，
> 读到这里就知道：什么能做、什么绝对不能做、做完怎么自检。
>
> 本文件与 `值守手册.md`（人类向运维手册）配套。参数变更必须先改本文件，再改代码。

---

## 一、项目是什么

《天纪》第一集 AI 漫剧（3D 化）。**规模**：74 段 / 666 张关键帧 / 657 张风格化完成。

**技术栈**：ComfyUI + SD1.5 + ControlNet + IPAdapter + DepthAnything + DWPose，本地 GPU 单卡跑。

**产物**：`06_成片/天纪AI漫剧_第一集_画质修复版.mp4`

---

## 二、流水线（改代码前必须理解这条链）

```
preprocess.py        预处理          → 切片、抽帧
shots_and_keys.py    选关键帧        → 666 张
      ↓
style_all_v2.py      A 风格化        → 657 张 (896×672)
face_refine_all.py   B 脸部精修      → 只动脸 (448px)
      ↓
build_episode2.py    C 组装          → 1440×1080 成片
      ↓
process_audio2.py    音频处理        → audio_final.wav
      ↓
supervisor.py        守护进程编排上述全流程
```

**依赖铁律**：
- **阶段 A 与 B 绝不能并发**。同卡并发会各自掉速 2 倍以上，且会污染产出。
  判别当前是否有并发：`tasklist | findstr style_all_v2 face_refine_all`
- 阶段 B 只允许在阶段 A 结束后启动。
- `supervisor.py` 是唯一编排者，不要绕过它直接手改状态文件。

---

## 三、画质红线（任何情况下不许改）

| 环节 | 固定参数 |
|---|---|
| **风格化** | 896×672 / 30 步 / cfg 5.0 / **denoise 0.78** / 脸部 IPAdapter 1.00 / 全身 IPAdapter(bw) 0.90 |
| **脸部精修** | 448px / 16 步 / **denoise 0.30** / FACE-IPAdapter 0.95（只动脸，不动道袍/场景） |
| **音频** | `audio_final.wav`（老者档 P3n），**不得替换、不得重渲** |
| **组装** | 1440×1080 / 12 fps / crf 18 / preset medium / 轻锐化 unsharp |

> **如果为了"跑通"需要动这些参数 —— 不许动。**
> 宁可停下来记录，等人工决定。这一条高于所有"让测试通过"的理由。

---

## 四、速度基线（判断是否异常的依据）

| 阶段 | 耗时 | 含义 |
|---|---|---|
| 风格化 | 32~40 s/帧 | 仅出现在刚启动后约 50 分钟内，**短时峰值，不是稳态** |
| 风格化 | 128~140 s/帧 | 长时运行的**稳态**速度 |
| 风格化 | 150 s/帧以上且持续 | 满载劣化态 |
| 风格化 | 190~290 s/帧 | **冷启动长帧**（模型重载），一次性开销，**须剔除不计** |
| 脸部精修 | 10~12 s/帧 | 独占 GPU 稳态 |
| 脸部精修 | 280~650 s/帧 | 与风格化**并发**时，属抢卡非劣化 |

**已证伪的结论**（不要再犯）：
- ❌「重启 ComfyUI 就能恢复速度」——**已证伪**。真实变快的原因是**机器重启 + GPU 冷却**。
- ✅ 速度劣化**不影响画质**时，只记录，不按阈值盲目重启。
- ✅ 不要重复重启 ComfyUI——已证无效，只增加冷启动损失。

---

## 五、代码约定

### 路径写法（必须遵守）
**禁止硬编码绝对路径**。一律用脚本自身位置推导：

```python
import os
ROOT = os.path.dirname(os.path.abspath(__file__))          # 项目根
PROJ = ROOT                                                  # 兼容旧变量名
FFMPEG = os.path.join(ROOT, "_tools", "ffmpeg-master-latest-win64-gpl", "bin", "ffmpeg.exe")
```

Windows 批处理（`.cmd`）里用 `%USERPROFILE%`，不要写死用户名。

### 硬编码的例外
- `HOST = "http://127.0.0.1:8189"`（ComfyUI 本地端口）——**允许**，这是标准本地地址。
  注意 `local_im2img.py` 用 8188、`local_cn_ipa.py` / `local_style2.py` / `local_svd.py` / `probe_speed.py` / `style_all.py` 用 8189，
  **两个端口对应不同实例，不要擅自统一**。

### 注释与日志
- 中文注释，说明「为什么」不说明「做了什么」。
- 日志用 `print()`，带阶段前缀（例：`[style] 帧 120/657`）。
- 不要在日志里打印完整绝对路径。

### 状态文件
- `*_state.json` 由对应脚本自己写，**不要手工编辑**。
- 判活优先用 `Get-CimInstance Win32_Process` 取 CommandLine 辨身份，
  不要只用 `os.kill(pid, 0)`（Windows 下等价于 TerminateProcess，会误杀）。

---

## 六、仓库与提交

- 分支：`main`。**改动走分支 + PR**，不要直接推 main。
- `.gitignore` 已排除全部媒体大文件（项目原始体积约 19GB，仓库只存约 2.1MB 文本）。
  **新增文件前先想清楚它是否该进版本库**；单个文件上限 100MB。
- 提交信息格式：`[模块] 干了什么`，例如 `风格化：v2 批次重试逻辑，失败帧自动降级`。

### 提交前自检（必做）

```bash
python -c "import ast; [ast.parse(open(f,encoding='utf-8').read()) for f in __import__('glob').glob('*.py')]"
git status --short
git diff --cached --stat
```

确认：**没有大文件、没有 `*_state.json`、没有 `.log`、没有个人路径**。

---

## 七、做完怎么自检（AI 必做）

**直接跑**：

```bash
cd /d/天纪AI漫剧3D
python selfcheck.py            # 完整报告
python selfcheck.py --quiet    # 只看结论
python selfcheck.py --staged   # 只查已暂存的文件
```

**退出码**：0 = 通过，1 = 有 FAIL（不要提交）。

**六项检查**：

| # | 检查项 | 抓什么 |
|---|---|---|
| 1 | 语法检查 | 所有 `.py` 通过 `ast.parse` |
| 2 | 硬编码路径 | 个人绝对路径（豁免 `127.0.0.1` 与 `D:\AI` 工具链） |
| 3 | 大文件 | 超过 1MB 的待入库文件 |
| 4 | 禁止入库 | `*_state.json` / `*.log` / `*.pid` / 未豁免的媒体 |
| 5 | 画质红线 | 15 项关键参数比对 + 注释与实现一致性 |
| 6 | 导入测试 | 核心模块实际 import（抓语法检查抓不到的运行时错误） |

**已装 git hook**：`core.hooksPath = .githooks`，`pre-commit` 会自动跑自检，
**不通过直接阻止提交**（实测拦截成功）。AI 提交时不必手动跑，但**要如实报告自检输出**。

> 临时需要跳过：`git commit --no-verify` —— **不建议**，红线违规会静默进入历史。

**环境变量**（paths.py 读取）：

| 变量 | 用途 | 默认值 |
|---|---|---|
| `TIANJI_SRC_VIDEO` | 源视频完整路径 | `00_素材/源视频.mp4` |
| `TIANJI_COMFY_DIR` | ComfyUI 目录 | `D:\AI\ComfyUI_windows_portable` |
| `TIANJI_FACEDET` | 人脸检测模型目录 | `D:\AI\facedet` |
| `TIANJI_COMFY_PORT` | ComfyUI 端口 | `8189` |

**其他必须遵守**：

- 画质红线一个都不许改。
- 路径一律走 `paths.py`，不要在业务脚本里再写绝对路径。
- 拿不准就停下来问，不要为了「让流程跑通」而放宽任何红线。

---

## 七之二、环境变量设置（首次使用）

源视频不在项目里（19GB 目录不进 Git），第一次跑之前设一次：

```bash
# Windows 用户级永久设置
setx TIANJI_SRC_VIDEO "D:\B站视频下载\倪海厦-天纪\【天纪】...\_P1_第一集(01)_B站_BV1S94y127Sv.mp4"

# 或者只在当前终端临时设
export TIANJI_SRC_VIDEO="D:\B站视频下载\...\源视频.mp4"
```

设完直接跑脚本即可，不用改任何代码。

---

## 八、当前状态（2026-10-29）

| 阶段 | 状态 |
|---|---|
| 预处理 / 选关键帧 | ✅ 完成，666 张关键帧 |
| A 风格化 | ✅ 完成 657 张 |
| B 脸部精修 | ✅ 完成 |
| C 组装 | ✅ 已生成成片 |
| 音频 | ✅ 定稿 `audio_final.wav` |

**仓库**：https://github.com/yanhao8998-byte/tianji-ai-manga-3d
**在线档案**：https://yanhao8998-byte.github.io/tianji-ai-manga-3d/

---

## 九、给 AI 的分工建议（多代理协作时）

| 角色 | 职责 | 红线 |
|---|---|---|
| **实现者** | 写/改流水线脚本 | 不得改第三节参数 |
| **审查者** | 检查参数漂移、并发冲突、路径硬编码 | 发现红线问题必须打回 |
| **验证者** | 跑语法检查、路径实测、检查大文件 | 不得跳过自检 |
| **值守者** | 监控进程存活与阶段进度 | 只记录，不擅自重启 |

**协作原则**：审查者独立于实现者。发现问题时**记录并上报**，不自己动手改参数。
