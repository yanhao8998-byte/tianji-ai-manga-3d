# -*- coding: utf-8 -*-
"""
夜间自愈守护 supervisor.py  ——  《天纪》第一集 画质修复版

职责：全程独占托管「风格化 → 脸部精修 → 组装」三阶段，任何一环出问题自动修复，
      绝不以降低画质参数的方式"绕过去"（精良视频原则，见 值守手册.md）。

守护项：
  1. ComfyUI 服务健康（HTTP /system_stats）；连续 3 次失败 → 停上下游 → 重启服务
  2. 阶段A 风格化：进程死亡 / 产出停滞(>20min) → 重启（断点续跑，不丢已出帧）
  3. 阶段B 脸部精修：进程死亡 / 精修计数停滞(>25min) → 重启（state 断点续跑）
  4. 阶段C 组装：失败/超时自动重试（build_episode2 分片自带缓存）
  5. 清理半写坏帧（<20KB 且 mtime>10min），交由风格化重跑

进程存活一律以 pid 文件为准（_style.pid / _refine.pid / _comfy.pid / _build.pid），
因此本脚本自身被重启后仍能接管已在跑的进程，不会重复启动。

用法:
  python supervisor.py            # 常驻守护
  python supervisor.py --once     # 只巡检一轮（调试）
  python supervisor.py --dry-run  # 只报告不动作
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import deque
from ctypes import wintypes

PROJ = r"<项目根>"
STY = os.path.join(PROJ, "04_关键帧_风格化")
OUTDIR = os.path.join(STY, "_正式输出_v2")
REFINE_FLAG = os.path.join(STY, "_face_refine_done.json")
REFINE_STATE = os.path.join(STY, "_face_refine_state.json")

PID_STYLE = os.path.join(PROJ, "_style.pid")
PID_REFINE = os.path.join(PROJ, "_refine.pid")
PID_COMFY = os.path.join(PROJ, "_comfy.pid")
PID_BUILD = os.path.join(PROJ, "_build.pid")
PID_SELF = os.path.join(PROJ, "_supervisor.pid")

PY_COMFY = r"D:\AI\ComfyUI_windows_portable\python_embeded\python.exe"
PY_MINI = r"%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
COMFY_CWD = r"D:\AI\ComfyUI_windows_portable"
COMFY_PORT = 8189

LOG = os.path.join(PROJ, "_supervisor.log")
STATE = os.path.join(PROJ, "_supervisor_state.json")

FINAL_MP4 = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集_画质修复版.mp4")
OUT_MP4 = os.path.join(PROJ, "06_成片", "天纪AI漫剧_第一集v2_无声.mp4")
TMP_DIR = os.path.join(PROJ, "06_成片", "_组装临时v2")

EXPECT = 657
TICK = 60
STALL_A = 20 * 60
STALL_B = 25 * 60
COMFY_FAIL_LIMIT = 3
COMFY_RESTART_LIMIT = 6
RESTART_LIMIT = 12
BACKOFF = 30 * 60          # 超过重启上限后，改为每 30 分钟再试一次
BUILD_TIMEOUT = 3 * 3600
MIN_PNG = 20000
BROKEN_AGE = 10 * 60

_K32 = ctypes.windll.kernel32
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259

DRY = False


# ---------------------------------------------------------------- 基础工具
def log(msg, level="INFO"):
    line = f"{time.strftime('%F %T')} [{level}] {msg}"
    # pythonw.exe（无控制台）下 sys.stdout 为 None，print 会抛异常，必须兜住
    try:
        if sys.stdout is not None:
            print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# 让本进程持续请求"系统不得休眠"：14 小时的长任务最怕空闲休眠把 GPU 挂起
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake():
    """阻止系统进入空闲休眠/待机（不影响屏幕熄灭）。

    ES_CONTINUOUS 让该状态一直有效，直到本进程退出或显式清除。
    仅需定期重申一次即可，即使 GUI 侧有电源计划也能压住。
    """
    try:
        _K32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass


def clean_env():
    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    env["no_proxy"] = "127.0.0.1,localhost"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def pid_alive(pid):
    """Windows 下判断进程存活。

    注意：绝不能用 os.kill(pid, 0) —— CPython 在 Windows 上会把它实现成
    TerminateProcess(handle, 0)，等于"检查即杀死"。这里只做 OpenProcess +
    GetExitCodeProcess 的只读查询。
    """
    if not pid:
        return False
    try:
        h = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if _K32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == _STILL_ACTIVE
            return False
        finally:
            _K32.CloseHandle(h)
    except Exception:
        return False


def read_pid(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def write_pid(path, pid):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(pid))
    except OSError:
        pass


def kill_pid(pid, why=""):
    if not pid_alive(pid):
        return False
    if DRY:
        log(f"[dry-run] 本应终止 pid {pid}（{why}）", "WARN")
        return False
    ok = False
    try:
        r = subprocess.run(["taskkill", "/PID", str(int(pid)), "/F", "/T"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        ok = (r.returncode == 0)
        if not ok:
            log(f"taskkill 返回 {r.returncode}: {((r.stdout or '') + (r.stderr or ''))[:200]}", "WARN")
    except Exception as e:
        log(f"taskkill 调用失败: {e}", "WARN")
    if not ok:
        try:
            os.kill(int(pid), 9)
            ok = True
        except Exception as e:
            log(f"os.kill 兜底也失败: {e}", "ERROR")
    if ok:
        log(f"已终止 pid {pid}（{why}）", "WARN")
        time.sleep(2)
    else:
        log(f"无法终止 pid {pid}", "ERROR")
    return ok


def count_frames():
    try:
        return sum(1 for f in os.listdir(OUTDIR) if f.endswith(".png"))
    except OSError:
        return 0


def newest_frame_ts():
    try:
        ts = [os.path.getmtime(os.path.join(OUTDIR, f))
              for f in os.listdir(OUTDIR) if f.endswith(".png")]
        return max(ts) if ts else time.time()
    except OSError:
        return time.time()


def refined_count():
    try:
        with open(REFINE_STATE, encoding="utf-8") as f:
            return len(json.load(f).get("done", []))
    except Exception:
        return 0


def comfy_ok():
    """不经系统/环境代理，直连本机 ComfyUI"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(f"http://127.0.0.1:{COMFY_PORT}/system_stats", timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def find_pid_by_port(port):
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        for line in r.stdout.splitlines():
            if f":{port}" in line and ("LISTENING" in line or "侦听" in line):
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
    except Exception as e:
        log(f"netstat 查询失败: {e}", "WARN")
    return None


def cleanup_broken():
    n = 0
    now = time.time()
    try:
        names = os.listdir(OUTDIR)
    except OSError:
        return 0
    for f in names:
        if not f.endswith(".png"):
            continue
        p = os.path.join(OUTDIR, f)
        try:
            if os.path.getsize(p) < MIN_PNG and now - os.path.getmtime(p) > BROKEN_AGE:
                if not DRY:
                    os.remove(p)
                log(f"清理坏帧 {f}", "WARN")
                n += 1
        except OSError:
            pass
    return n


# ---------------------------------------------------------------- 守护主体
class Supervisor:
    def __init__(self):
        self.comfy_pid = read_pid(PID_COMFY) or find_pid_by_port(COMFY_PORT)
        if self.comfy_pid:
            write_pid(PID_COMFY, self.comfy_pid)
        self.style_pid = read_pid(PID_STYLE)
        self.refine_pid = read_pid(PID_REFINE)
        self.build_pid = read_pid(PID_BUILD)
        self.build_ts = 0.0
        self.comfy_fail = 0
        self.comfy_restarts = 0
        self.style_restarts = 0
        self.refine_restarts = 0
        self.build_tries = 0
        self.retry_after = {"style": 0.0, "refine": 0.0, "build": 0.0}
        self.last_n = count_frames()
        self.last_n_ts = newest_frame_ts()
        self.last_ref = refined_count()
        self.last_ref_ts = time.time()
        self.samples = deque(maxlen=90)
        self.samples.append((time.time(), self.last_n))
        self.note = ""
        self._last_phase = None
        keep_awake()

    # ---------- 启动器 ----------
    def _spawn(self, cmd, cwd, logfile, banner):
        lf = open(logfile, "a", encoding="utf-8")
        lf.write("\n" + "=" * 60 + "\n" + time.strftime("%F %T") + " " + banner + "\n")
        lf.flush()
        return subprocess.Popen(cmd, cwd=cwd, env=clean_env(),
                                stdout=lf, stderr=subprocess.STDOUT)

    def start_comfy(self):
        if DRY:
            log("[dry-run] 本应启动 ComfyUI", "WARN")
            return
        p = self._spawn(
            [PY_COMFY, "-s", "ComfyUI/main.py", "--windows-standalone-build",
             "--port", str(COMFY_PORT)],
            COMFY_CWD, os.path.join(PROJ, "_comfyui.log"), "supervisor 重启 ComfyUI")
        self.comfy_pid = p.pid
        write_pid(PID_COMFY, p.pid)
        log(f"ComfyUI 已启动 pid {p.pid}，等待就绪 ...", "WARN")

    def start_style(self):
        if DRY:
            log("[dry-run] 本应启动风格化", "WARN")
            return
        p = self._spawn([PY_COMFY, "style_all_v2.py"], PROJ,
                        os.path.join(PROJ, "_style_all_v2.log"),
                        "supervisor 启动/重启风格化")
        self.style_pid = p.pid
        write_pid(PID_STYLE, p.pid)
        self.style_restarts += 1
        self.last_n_ts = time.time()
        log(f"风格化已启动 pid {p.pid}（第 {self.style_restarts} 次）", "WARN")

    def start_refine(self):
        if DRY:
            log("[dry-run] 本应启动脸部精修", "WARN")
            return
        p = self._spawn([PY_COMFY, "face_refine_all.py"], PROJ,
                        os.path.join(PROJ, "_face_refine.log"),
                        "supervisor 启动/重启脸部精修")
        self.refine_pid = p.pid
        write_pid(PID_REFINE, p.pid)
        self.refine_restarts += 1
        self.last_ref_ts = time.time()
        log(f"脸部精修已启动 pid {p.pid}（第 {self.refine_restarts} 次）", "WARN")

    def start_build(self):
        if DRY:
            log("[dry-run] 本应启动组装", "WARN")
            return
        p = self._spawn([PY_MINI, "build_episode2.py", OUTDIR, OUT_MP4, TMP_DIR, FINAL_MP4],
                        PROJ, os.path.join(PROJ, "_build_serial.log"),
                        "supervisor 启动组装")
        self.build_pid = p.pid
        write_pid(PID_BUILD, p.pid)
        self.build_ts = time.time()
        self.build_tries += 1
        log(f"组装已启动 pid {p.pid}（第 {self.build_tries} 次）", "WARN")

    # ---------- 配额控制 ----------
    def need_backoff(self, kind, count):
        """重启次数超上限后改为冷却重试；返回 True 表示本次跳过"""
        if count < RESTART_LIMIT:
            return False
        now = time.time()
        if now < self.retry_after[kind]:
            return True
        self.retry_after[kind] = now + BACKOFF
        log(f"{kind} 重启已 {count} 次，进入冷却重试（每 30 分钟一次）", "CRITICAL")
        self.note = f"{kind} 反复重启 {count} 次"
        return False

    # ---------- ComfyUI ----------
    def check_comfy(self):
        if comfy_ok():
            if self.comfy_fail:
                log("ComfyUI 已恢复正常", "INFO")
            self.comfy_fail = 0
            return True
        self.comfy_fail += 1
        log(f"ComfyUI 健康检查失败 {self.comfy_fail}/{COMFY_FAIL_LIMIT}", "WARN")
        if self.comfy_fail < COMFY_FAIL_LIMIT:
            return False
        if self.comfy_restarts >= COMFY_RESTART_LIMIT:
            self.note = "ComfyUI 反复启动失败，已停止自动重启，需人工介入"
            log(f"ComfyUI 重启已达上限 {COMFY_RESTART_LIMIT} 次，停止重启", "CRITICAL")
            return False
        log("准备重启 ComfyUI（先停上下游以免空转）", "WARN")
        kill_pid(self.style_pid, "ComfyUI 重启前停风格化")
        kill_pid(self.refine_pid, "ComfyUI 重启前停精修")
        self.style_pid = self.refine_pid = None
        kill_pid(self.comfy_pid, "无响应")
        self.comfy_pid = None
        self.start_comfy()
        self.comfy_restarts += 1
        t0 = time.time()
        while time.time() - t0 < 300:
            time.sleep(10)
            if comfy_ok():
                self.comfy_fail = 0
                log(f"ComfyUI 重启成功（{time.time()-t0:.0f}s 就绪）", "WARN")
                return True
        self.note = "ComfyUI 重启后 300s 未就绪"
        log("ComfyUI 重启后仍未就绪", "CRITICAL")
        return False

    # ---------- 阶段判定 ----------
    def phase(self):
        n = count_frames()
        if n >= EXPECT and os.path.isfile(REFINE_FLAG):
            return "DONE" if os.path.isfile(FINAL_MP4) else "C"
        if n >= EXPECT:
            return "B"
        return "A"

    # ---------- 阶段A ----------
    def run_a(self, comfy):
        n = count_frames()
        now = time.time()
        if n != self.last_n:
            self.last_n = n
            self.last_n_ts = now
        alive = pid_alive(self.style_pid)

        if alive and (now - self.last_n_ts) > STALL_A:
            log(f"风格化停滞 {int((now-self.last_n_ts)/60)} 分钟（{n}/{EXPECT}），重启", "WARN")
            kill_pid(self.style_pid, "产出停滞")
            self.style_pid = None
            alive = False

        if alive:
            return
        if not comfy:
            return
        if self.need_backoff("style", self.style_restarts):
            return
        log(f"风格化进程不在（{n}/{EXPECT}），启动", "WARN")
        self.start_style()

    # ---------- 阶段B ----------
    def run_b(self, comfy):
        ref = refined_count()
        now = time.time()
        if ref != self.last_ref:
            self.last_ref = ref
            self.last_ref_ts = now
        alive = pid_alive(self.refine_pid)

        if alive and (now - self.last_ref_ts) > STALL_B:
            log(f"精修停滞 {int((now-self.last_ref_ts)/60)} 分钟（{ref}/{EXPECT}），重启", "WARN")
            kill_pid(self.refine_pid, "精修停滞")
            self.refine_pid = None
            alive = False

        if alive:
            return
        if not comfy:
            return
        if self.need_backoff("refine", self.refine_restarts):
            return
        log(f"精修进程不在（{ref}/{EXPECT}），启动", "WARN")
        self.start_refine()

    # ---------- 阶段C ----------
    def run_c(self):
        if os.path.isfile(FINAL_MP4):
            return
        if pid_alive(self.build_pid):
            if self.build_ts and time.time() - self.build_ts > BUILD_TIMEOUT:
                log("组装超时 3 小时，重启", "WARN")
                kill_pid(self.build_pid, "组装超时")
                self.build_pid = None
            else:
                return
        if self.need_backoff("build", self.build_tries):
            return
        log("进入/重启组装阶段", "WARN")
        self.start_build()

    # ---------- 状态落盘 ----------
    def dump_state(self, phase, comfy):
        n = count_frames()
        ref = refined_count()
        now = time.time()
        self.samples.append((now, n))
        rate = 0.0
        if len(self.samples) >= 2:
            t0, n0 = self.samples[0]
            t1, n1 = self.samples[-1]
            if t1 - t0 > 60:
                rate = (n1 - n0) / ((t1 - t0) / 60.0)
        st = {
            "ts": time.strftime("%F %T"),
            "phase": phase,
            "frames": n,
            "expect": EXPECT,
            "refined": ref,
            "rate_fpm": round(rate, 2),
            "eta_hours": round((EXPECT - n) / rate / 60.0, 1) if rate > 0.01 else None,
            "refine_eta_hours": round((EXPECT - ref) / rate / 60.0, 1) if rate > 0.01 else None,
            "last_frame_age_min": round((now - self.last_n_ts) / 60.0, 1),
            "last_refine_age_min": round((now - self.last_ref_ts) / 60.0, 1),
            "comfy_ok": comfy,
            "comfy_pid": self.comfy_pid,
            "style_alive": pid_alive(self.style_pid),
            "refine_alive": pid_alive(self.refine_pid),
            "build_alive": pid_alive(self.build_pid),
            "comfy_restarts": self.comfy_restarts,
            "style_restarts": self.style_restarts,
            "refine_restarts": self.refine_restarts,
            "build_tries": self.build_tries,
            "final_exists": os.path.isfile(FINAL_MP4),
            "note": self.note,
        }
        try:
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STATE)
        except OSError:
            pass
        return st

    # ---------- 主循环 ----------
    def tick(self):
        comfy = self.check_comfy()
        cleanup_broken()
        ph = self.phase()
        if ph != self._last_phase:
            log(f"阶段切换 → {ph}（帧数 {count_frames()}/{EXPECT}，精修 {refined_count()}）", "INFO")
            self._last_phase = ph
        if ph == "A":
            self.run_a(comfy)
        elif ph == "B":
            self.run_b(comfy)
        elif ph == "C":
            self.run_c()
        return self.dump_state(ph, comfy)

    def run(self):
        log("=" * 60)
        log(f"夜间自愈守护启动  comfy_pid={self.comfy_pid} style_pid={self.style_pid} "
            f"refine_pid={self.refine_pid} 初始帧数={self.last_n} 已精修={self.last_ref}")
        while True:
            try:
                st = self.tick()
                if st["phase"] == "DONE" and st["final_exists"]:
                    log(f"✓ 成片已完成: {FINAL_MP4}", "INFO")
                    log("守护任务结束", "INFO")
                    return 0
            except Exception as e:
                log(f"巡检异常: {type(e).__name__}: {e}", "ERROR")
            time.sleep(TICK)


def acquire_singleton():
    """单实例锁：已有存活实例则本次退出，避免两个守护互相抢启动

    注意：外部守卫（tianji_guard.py）会先把自己的子进程 pid 写进 _supervisor.pid
    再等它启动，所以这里读到的 pid 可能正是"自己"。必须先排除自身 pid，
    否则会误判成"已有实例"而自杀退出。
    """
    me = os.getpid()
    old = read_pid(PID_SELF)
    if old and int(old) != me and pid_alive(old):
        log(f"已存在守护实例 pid {old}，本次退出", "WARN")
        return False
    write_pid(PID_SELF, me)
    return True


def main():
    global DRY
    if "--dry-run" in sys.argv:
        DRY = True
        log("以 dry-run 模式启动（只报告不动作）", "WARN")
    if "--once" not in sys.argv:
        if not acquire_singleton():
            return 0
    sup = Supervisor()
    if "--once" in sys.argv:
        st = sup.tick()
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0
    return sup.run()


if __name__ == "__main__":
    sys.exit(main())
