#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
selfcheck.py — 提交前自检

对应 AGENTS.md 第七节「做完怎么自检」，五项检查：
  1. 语法检查    所有 .py 通过 ast.parse
  2. 硬编码路径  扫描个人绝对路径字面量
  3. 大文件      超过阈值的文件不应入库
  4. 禁止入库    *_state.json / *.log / 大媒体
  5. 画质红线    比对关键参数是否被改动

用法：
  python selfcheck.py            # 检查工作区
  python selfcheck.py --staged   # 只检查 git 已暂存的文件
  python selfcheck.py --quiet    # 只输出结论

退出码：0 = 全部通过；1 = 有 FAIL
"""
import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── 配置 ───────────────────────────────────────────────
MAX_FILE_MB = 1.0                    # 单文件入库上限（MB），超了要问
MAX_TOTAL_MB = 50.0                  # 仓库总体积上限（MB）
GITIGNORE = os.path.join(ROOT, ".gitignore")

# 允许入库的媒体后缀（其余媒体一律不入库）
OK_MEDIA_EXT = {".gif"}               # 小体积动图可入库

# 禁止入库的文件名模式
FORBIDDEN_PATTERNS = [
    (re.compile(r"_state\.json$"), "状态文件"),
    (re.compile(r"_state/"), "状态目录"),
    (re.compile(r"\.log$"), "日志文件"),
    (re.compile(r"\.pid$"), "PID 文件"),
    (re.compile(r"^_supervisor_state\.json$"), "守护状态"),
    (re.compile(r"^_face_refine_done\.json$"), "完成标记"),
]

# 硬编码个人路径检测（AGENTS.md 第五节禁止）
HARDCODED_PATH = [
    (re.compile(r'["\'][A-Za-z]:\\Users\\[^"\']*["\']'), "硬编码用户目录"),
    (re.compile(r'["\'][A-Za-z]:\\(?!AI\\ComfyUI)(?!AI\\facedet)[^"\']*\\[^"\']*["\']'), "硬编码盘符路径"),
    (re.compile(r'["\']/d/[^"\']*["\']'), "硬编码 /d/ 路径"),
]

# 例外：这些是允许的固定路径（标准本地地址 / 外部工具链）
PATH_EXCEPTIONS = {
    "local_im2img.py": ["127.0.0.1:8188"],
    "local_cn_ipa.py": ["127.0.0.1:8189"],
    "local_style2.py": ["127.0.0.1:8189", "D:\\\\AI", "D:\\AI"],
    "local_svd.py": ["127.0.0.1:8189"],
    "probe_speed.py": ["127.0.0.1:8189"],
    "style_all.py": ["127.0.0.1:8189"],
    "style_all_v2.py": ["127.0.0.1:8189"],
    "supervisor.py": ["127.0.0.1", "D:\\\\AI", "D:\\AI"],
    "process_audio.py": [],
    "process_audio2.py": [],
    # 外部工具链目录，非个人路径
    "face_refine_all.py": ["D:\\\\AI", "D:\\AI"],
    "build_episode2.py": [],
    "build_episode.py": [],
    "preprocess.py": [],
}

# 检测器自身必然含有豁免名单字面量，自身不做路径检查
SELF_EXEMPT = {"selfcheck.py"}

# 文档字符串 / 注释里的示例不算硬编码（只在真实赋值处检查）
DOCSTRING_HINT = re.compile(r'^\s*(#|"""|\'\'\'|python\s|\.\.\.|>>>|\*)')
# 正则字面量：r"..." / r'...' 且内部含正则转义序列（\s \d \w \b 等）
#   是正则 → 里面的 I: 是指标名，不是盘符
#   不是正则 → r"D:\B站\..." 是原始路径字符串，必须照常检查
REGEX_LITERAL = re.compile(
    r'''r(["'])(?:\\.|(?!\1).)*\1'''
)
# 注意：\B 不算正则元字符 —— Python 正则里 \B 是"非词边界"，
# 而 Windows 路径里的 "\B站" 是"B站"目录名，不能误判成正则。
REGEX_ESCAPE = re.compile(r"\\[sdbwWDSZAnrtfv]|\[\^?[^]]*\]|\(\?")

# 画质红线（AGENTS.md 第三节）—— 值来自契约，改契约就要改这里
REDLINE = {
    "style_all_v2.py": {
        "w=896": r"w\s*=\s*896",
        "h=672": r"h\s*=\s*672",
        "steps=30": r"steps\s*=\s*30",
        "cfg=5.0": r"cfg\s*=\s*5\.0",
        "denoise=0.78": r"denoise\s*=\s*0\.78",
        "脸部IPA=1.00": r"iw\s*=\s*1\.00",
        "全身IPA=0.90": r"bw\s*=\s*0\.90",
    },
    "face_refine_all.py": {
        "FACE_SIZE=448": r"FACE_SIZE\s*=\s*448",
        "FACE_STEPS=16": r"FACE_STEPS\s*=\s*16",
        "FACE_DENOISE=0.30": r"FACE_DENOISE\s*=\s*0\.30",
        "FACE_IW=0.95": r"FACE_IW\s*=\s*0\.95",
    },
    "build_episode2.py": {
        "1440x1080@12fps": r"W,\s*H,\s*FPS\s*=\s*1440,\s*1080,\s*12",
        "crf 18": r"[\"']-crf[\"']\s*,\s*[\"']18[\"']",
        "preset medium": r"[\"']-preset[\"']\s*,\s*[\"']medium[\"']",
        "轻锐化 unsharp": r"unsharp=5:5:0\.45",
    },
}

MEDIA_EXT = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".psd", ".exr", ".hdr",
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",
    ".safetensors", ".ckpt", ".pt", ".pth", ".onnx", ".engine", ".bin", ".gguf",
}


class Report:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.rows = []
        self.fail_count = 0
        self.warn_count = 0

    def add(self, level, check, msg):
        self.rows.append((level, check, msg))
        if level == "FAIL":
            self.fail_count += 1
        elif level == "WARN":
            self.warn_count += 1

    def ok(self, check, msg):
        self.add("PASS", check, msg)

    def fail(self, check, msg):
        self.add("FAIL", check, msg)

    def add_warn(self, check, msg):
        self.add("WARN", check, msg)

    def show(self):
        if not self.quiet:
            width = max(len(r[2]) for r in self.rows) if self.rows else 40
            width = min(width, 76)
            print("=" * 78)
            print("  提交前自检 · 天纪 AI 漫剧 3D")
            print("=" * 78)
            last = None
            for level, check, msg in self.rows:
                if check != last:
                    print(f"\n[{check}]")
                    last = check
                mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn "}[level]
                print(f"  {mark} {msg}")
            print("\n" + "=" * 78)
            total = len(self.rows)
            passed = total - self.fail_count - self.warn_count
            print(f"  通过 {passed} · 警告 {self.warn_count} · 失败 {self.fail_count}")
            print("=" * 78)
        if self.fail_count:
            print(f"\n结论：FAIL —— 有 {self.fail_count} 项未通过，不要提交。")
            return 1
        if self.warn_count:
            print(f"\n结论：PASS（{self.warn_count} 项警告，请人工确认）。")
            return 0
        print("\n结论：PASS —— 全部检查通过。")
        return 0


def run_git(args):
    try:
        r = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except Exception:
        return ""


def get_files(staged=False):
    """返回待检查的文件列表（相对 ROOT）"""
    if staged:
        out = run_git(["diff", "--cached", "--name-only", "--diff-filter=ACM"])
    else:
        out = run_git(["ls-files"])
    return [f for f in out.splitlines() if f.strip()]


def read_ignored():
    """读取 .gitignore 里的排除规则，用于判断某文件是否本就不该入库"""
    rules = []
    if os.path.exists(GITIGNORE):
        with open(GITIGNORE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                rules.append(line)
    return rules


def is_ignored(relpath, rules):
    """粗略判断：路径是否被 .gitignore 覆盖（够用即可）"""
    import fnmatch
    parts = relpath.replace("\\", "/").split("/")
    for rule in rules:
        rule = rule.rstrip("/")
        neg = rule.startswith("!")
        if neg:
            rule = rule[1:]
        if fnmatch.fnmatch(relpath, rule) or fnmatch.fnmatch(relpath, rule + "/*"):
            return not neg
        # 目录前缀匹配
        for i in range(1, len(parts) + 1):
            if fnmatch.fnmatch("/".join(parts[:i]), rule):
                return not neg
    return False


def check_syntax(files, rep):
    bad = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                ast.parse(f.read())
        except SyntaxError as e:
            bad.append(f"{rel} 第 {e.lineno} 行: {e.msg}")
        except Exception as e:
            bad.append(f"{rel} 读取失败: {e}")
    if bad:
        for b in bad:
            rep.fail("1. 语法检查", b)
    else:
        n = sum(1 for f in files if f.endswith(".py"))
        rep.ok("1. 语法检查", f"{n} 个 .py 文件全部通过 ast.parse")


def docstring_line_ranges(src):
    """用 AST 找出所有 docstring 所在的行区间（模块/类/函数）"""
    ranges = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ranges
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr):
                val = body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    end = getattr(body[0], "end_lineno", body[0].lineno)
                    ranges.append((body[0].lineno, end))
    return ranges


def check_hardcoded_path(files, rep):
    hits = []
    for rel in files:
        if not rel.endswith((".py", ".cmd", ".bat")):
            continue
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception:
            continue
        lines = src.splitlines()
        doc_ranges = docstring_line_ranges(src)
        base = os.path.basename(rel)
        if base in SELF_EXEMPT:
            continue
        allowed = PATH_EXCEPTIONS.get(base, [])
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 正则字面量跳过（仅当确实含正则元字符时；r"D:\路径" 仍要检查）
            m = REGEX_LITERAL.search(line)
            if m and REGEX_ESCAPE.search(m.group(0)):
                continue
            # docstring 内的示例不算硬编码
            if any(a <= i <= b for a, b in doc_ranges):
                continue
            for rx, label in HARDCODED_PATH:
                if not rx.search(line):
                    continue
                if any(a in line for a in allowed):
                    continue
                hits.append(f"{rel}:{i}  {label}  →  {stripped[:64]}")
    if hits:
        for h in hits:
            rep.fail("2. 硬编码路径", h)
    else:
        rep.ok("2. 硬编码路径", "未发现硬编码个人路径（127.0.0.1 本地端口已豁免）")


def check_large_files(files, rep):
    big = []
    for rel in files:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        ext = os.path.splitext(rel)[1].lower()
        if ext in OK_MEDIA_EXT:
            continue
        if size > MAX_FILE_MB * 1024 * 1024:
            big.append(f"{rel}  {size/1024/1024:.2f} MB")
    if big:
        for b in big:
            rep.fail("3. 大文件", b)
    else:
        rep.ok("3. 大文件", f"无超过 {MAX_FILE_MB} MB 的待入库文件")


def check_forbidden(files, rep):
    hits = []
    for rel in files:
        norm = rel.replace("\\", "/")
        for rx, label in FORBIDDEN_PATTERNS:
            if rx.search(norm):
                hits.append(f"{rel}  （{label}）")
    if hits:
        for h in hits:
            rep.fail("4. 禁止入库", h)
    else:
        rep.ok("4. 禁止入库", "无 *_state.json / *.log / *.pid 等状态与日志文件")


def check_media(files, rules, rep):
    """已入库文件里不应有媒体大文件（除非在 .gitignore 白名单）"""
    hits = []
    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in MEDIA_EXT:
            continue
        if ext in OK_MEDIA_EXT:
            continue
        if is_ignored(rel, rules):
            continue
        p = os.path.join(ROOT, rel)
        size = os.path.getsize(p) if os.path.exists(p) else 0
        hits.append(f"{rel}  {size/1024/1024:.2f} MB  ({ext})")
    if hits:
        for h in hits:
            rep.fail("4. 禁止入库", f"媒体文件已进入版本库  {h}")
    else:
        rep.ok("4. 禁止入库", "版本库中无未豁免的媒体文件")


def check_redline(rep):
    problems = []
    checked = 0
    for fname, params in REDLINE.items():
        p = os.path.join(ROOT, fname)
        if not os.path.exists(p):
            problems.append(f"{fname}  文件不存在，无法核对红线")
            continue
        with open(p, "r", encoding="utf-8") as f:
            src = f.read()
        for label, pattern in params.items():
            checked += 1
            if not re.search(pattern, src):
                problems.append(f"{fname}  红线参数 {label} 未找到（可能被改动或删除）")
    if problems:
        for pr in problems:
            rep.fail("5. 画质红线", pr)
    else:
        rep.ok("5. 画质红线", f"{checked} 项关键参数与 AGENTS.md 契约一致")


def check_redline_conflict(rep):
    """额外检查：文档字符串/注释里的 denoise 描述与代码实际值是否矛盾"""
    issues = []
    p = os.path.join(ROOT, "style_all_v2.py")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"denoise\s*=\s*([0-9.]+)", src)
        code_val = m.group(1) if m else None
        # 注释行里出现的 denoise 数值
        for line in src.splitlines()[:12]:
            if line.strip().startswith("#") or line.strip().startswith("配置"):
                for cm in re.finditer(r"denoise\D{0,3}([0-9.]+)", line, re.I):
                    if code_val and cm.group(1) != code_val:
                        issues.append(
                            f"style_all_v2.py  注释写 denoise {cm.group(1)}，"
                            f"代码实际 {code_val}  →  {line.strip()[:56]}"
                        )
    if issues:
        for i in issues:
            rep.add_warn("5. 画质红线", f"注释与实现不一致  {i}")
    else:
        rep.ok("5. 画质红线", "注释中的参数描述与代码实现一致")


def check_imports(rep):
    """导入测试：抓语法检查抓不到的运行时错误（如缺 import、模块路径错）"""
    import importlib
    mods = ["paths", "preprocess", "process_audio", "process_audio2",
            "build_episode", "selfcheck"]
    sys.path.insert(0, ROOT)
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}  →  {type(e).__name__}: {e}")
    if bad:
        for b in bad:
            rep.fail("6. 导入测试", b)
    else:
        rep.ok("6. 导入测试", f"{len(mods)} 个核心模块可正常导入")


def main():
    staged = "--staged" in sys.argv
    quiet = "--quiet" in sys.argv

    rep = Report(quiet=quiet)
    files = get_files(staged=staged)
    if not files:
        print("没有需要检查的文件。")
        return 0

    rules = read_ignored()

    check_syntax(files, rep)
    check_hardcoded_path(files, rep)
    check_large_files(files, rep)
    check_forbidden(files, rep)
    check_media(files, rules, rep)
    check_redline(rep)
    check_redline_conflict(rep)
    check_imports(rep)

    return rep.show()


if __name__ == "__main__":
    sys.exit(main())
