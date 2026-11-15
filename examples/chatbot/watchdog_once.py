# -*- coding: utf-8 -*-
"""One-shot watchdog, meant to be run by Task Scheduler once a minute.

Starts bot.py (minimized console window) when no bot process is alive, but
only while the WeChat main window exists: with WeChat closed or logged out
the bot would crash-loop, so we wait instead.

Pause: create data/pause_watchdog.flag -> no auto start until removed.
"""
import ctypes
import json
import os
import subprocess
import sys
import time

import psutil

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.environ.get("CHATBOT_PYTHON") or sys.executable
LOG_PATH = os.path.join(BASE, "logs", "watchdog.log")
STATE_PATH = os.path.join(BASE, "data", "watchdog_state.json")
PAUSE_PATH = os.path.join(BASE, "data", "pause_watchdog.flag")

MAX_STARTS = 5
START_WINDOW = 600
WECHAT_TITLES = ("\u5fae\u4fe1", "wechat", "weixin")


def log(msg):
    line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def bot_pid():
    """只认本项目脚本，其他项目的 bot.py 不影响本项目自启动。"""
    target = os.path.normcase(os.path.abspath(os.path.join(BASE, "bot.py")))
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (proc.info.get("name") or "").lower() not in ("python.exe", "pythonw.exe"):
                continue
            for part in (proc.info.get("cmdline") or [])[1:]:
                token = str(part).strip('"')
                if os.path.basename(token).lower() != "bot.py":
                    continue
                if not os.path.isabs(token):
                    token = os.path.join(proc.cwd(), token)
                if os.path.normcase(os.path.abspath(token)) == target:
                    return int(proc.info["pid"])
        except (psutil.Error, OSError, ValueError):
            continue
    return 0


def wechat_windows():
    """Visible top-level windows whose title is WeChat's main window."""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lparam):
        try:
            user32 = ctypes.windll.user32
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(128)
            user32.GetWindowTextW(hwnd, buf, 128)
            if buf.value.strip().lower() in WECHAT_TITLES:
                found.append(hwnd)
        except Exception:
            pass
        return True

    try:
        ctypes.windll.user32.EnumWindows(_cb, 0)
    except Exception:
        return []
    return found


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    now = time.time()
    starts = []
    raw = data.get("starts")
    for x in raw if isinstance(raw, list) else []:
        try:
            stamp = float(x)
            if 0 <= now - stamp < START_WINDOW:
                starts.append(stamp)
        except (TypeError, ValueError, OverflowError):
            continue
    data["starts"] = starts
    return data


def save_state(data):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def start_bot():
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 7
    subprocess.Popen(
        [PYTHON, os.path.join(BASE, "bot.py")],
        cwd=BASE,
        close_fds=True,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startupinfo,
    )


def main():
    if not os.path.isfile(os.path.join(BASE, "config.json")):
        return 0
    if os.path.exists(PAUSE_PATH):
        return 0
    if bot_pid():
        return 0
    data = load_state()
    if not wechat_windows():
        if not data.get("wechat_missing"):
            log("wechat window not found, waiting instead of restarting bot")
            data["wechat_missing"] = True
            save_state(data)
        return 0
    if data.get("wechat_missing"):
        log("wechat window is back")
        data["wechat_missing"] = False
        save_state(data)
    starts = data.get("starts") or []
    if len(starts) >= MAX_STARTS:
        log("too many restarts in 10 min, waiting one more minute")
        return 0
    log("starting bot.py (pid was gone)")
    try:
        start_bot()
    except Exception as exc:
        log("start failed: %r" % (exc,))
        return 0
    starts.append(time.time())
    data["starts"] = starts
    save_state(data)
    return 0


if __name__ == "__main__":
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None, False, "Local\\chatbot-bot-watchdog")
    already_running = ctypes.get_last_error() == 183
    if not mutex:
        sys.exit(1)
    try:
        sys.exit(0 if already_running else main())
    finally:
        kernel.CloseHandle(mutex)
