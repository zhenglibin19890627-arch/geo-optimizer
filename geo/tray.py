"""GEO 优化系统托盘：开机自启 + 服务暂停/启动/重启 + 打开界面 + 退出。

服务进程管理：托盘以子进程方式拉起 app.py（pythonw 静默运行），
- 暂停服务 = 结束子进程（界面与每日自动监测一并停止，托盘保持常驻）
- 启动服务 = 重新拉起子进程（app.py 启动时会自动回收上次中断的僵尸任务）
- 重启服务 = 结束并重新拉起（配置/钥匙改动后兜底刷新）
- 开机自启 = 写入/删除 HKCU Run 注册表键（当前用户级，无需管理员权限）
- 若已有服务在跑（如用户手动执行过 app.py），托盘会"接管"而不是重复启动；
  接管的服务暂停时按 service.json 里的 pid 结束进程。
"""

import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import requests

from geo import config
from geo.server import DEFAULT_HOST

APP_PY = config.PROJECT_ROOT / "app.py"
TRAY_PY = config.PROJECT_ROOT / "tray_app.py"
STATE_FILE = config.DATA_DIR / "service.json"
LOG_FILE = config.DATA_DIR / "service.log"
LOCK_FILE = config.DATA_DIR / "tray.lock"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "GEO优化系统"

# 我们作为父进程拉起的子进程句柄（None=接管模式或未启动）
_server_proc = None
# 当前已知端口（读自状态文件）
_state_port = None


def _pythonw() -> str:
    """当前解释器同目录的 pythonw.exe（无控制台窗口）；没有则回落 python.exe。"""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe)


def read_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def server_url() -> str:
    global _state_port
    port = _state_port or read_state().get("port") or 5080
    return f"http://{DEFAULT_HOST}:{port}"


def is_server_running() -> bool:
    """服务是否在线：优先看子进程存活，其次探活状态文件里的端口。"""
    global _server_proc
    if _server_proc is not None:
        return _server_proc.poll() is None
    port = read_state().get("port")
    if not port:
        return False
    try:
        # /api/overview 是本系统的独有接口，避免误认同端口的其他程序
        r = requests.get(f"http://{DEFAULT_HOST}:{port}/api/overview", timeout=2)
        return r.status_code == 200 and r.json().get("code") == 0
    except Exception:
        return False


def start_server() -> bool:
    """拉起服务子进程，等待状态文件出现（最多约 10 秒）。已在运行则直接返回 True。"""
    global _server_proc, _state_port
    if is_server_running():
        _state_port = read_state().get("port") or _state_port
        return True
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        logf = open(LOG_FILE, "ab", buffering=0)
    except Exception:
        logf = None
    try:
        _server_proc = subprocess.Popen(
            [_pythonw(), str(APP_PY), "--no-browser"],
            cwd=str(config.PROJECT_ROOT),
            stdout=logf or subprocess.DEVNULL,
            stderr=logf or subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return False
    for _ in range(50):
        st = read_state()
        if st.get("port"):
            _state_port = st.get("port")
            return True
        if _server_proc.poll() is not None:
            return False
        time.sleep(0.2)
    return _server_proc.poll() is None


def stop_server() -> bool:
    """结束服务进程：自有子进程用句柄结束；接管的服务按状态文件 pid 结束。"""
    global _server_proc, _state_port
    stopped = False
    if _server_proc is not None:
        try:
            if _server_proc.poll() is None:
                _server_proc.terminate()
                stopped = True
        except Exception:
            pass
        _server_proc = None
    else:
        pid = read_state().get("pid")
        if pid and pid != os.getpid():
            try:
                os.kill(int(pid), signal.SIGTERM)
                stopped = True
            except (OSError, ValueError, TypeError):
                stopped = False
    _state_port = None
    try:
        STATE_FILE.unlink()
    except OSError:
        pass
    return stopped


def restart_server() -> bool:
    stop_server()
    time.sleep(0.4)
    return start_server()


# ---------------- 开机自启（HKCU Run，无需管理员） ----------------

def _run_command() -> str:
    return f'"{_pythonw()}" "{TRAY_PY}"'


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    import winreg
    try:
        if enabled:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, _run_command())
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
        return True
    except OSError:
        return False


# ---------------- 单实例锁（防止开机自启 + 手动启动出现两个托盘互相接管） ----------------
# 用 Windows 文件句柄独占锁：持锁进程以"无共享"方式持有 tray.lock 的句柄，
# 第二实例打开同一文件会得到共享冲突（ERROR_SHARING_VIOLATION）→ 拒绝启动。
# 进程退出/崩溃时系统自动释放句柄，不存在陈旧 pid 误判问题。

_lock_handle = None


def _acquire_single_instance() -> bool:
    """拿单实例锁：成功（或无法判断）返回 True；已有实例持锁返回 False。"""
    global _lock_handle
    try:
        import ctypes
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        GENERIC_READ = 0x80000000
        OPEN_ALWAYS = 4
        FILE_ATTRIBUTE_NORMAL = 0x80
        ERROR_SHARING_VIOLATION = 32
        INVALID_HANDLE = ctypes.c_void_p(-1).value

        handle = kernel32.CreateFileW(
            str(LOCK_FILE), GENERIC_READ, 0, None, OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL, None)
        if not handle or handle == INVALID_HANDLE:
            if ctypes.get_last_error() == ERROR_SHARING_VIOLATION:
                return False  # 已有托盘实例持锁
            return True  # 其他原因打不开：宁可照常启动
        _lock_handle = handle
        return True
    except Exception:
        return True  # 拿锁异常时照常启动（极端情况下可能出现双托盘，属可接受兜底）


def _release_single_instance():
    global _lock_handle
    if _lock_handle is not None:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_lock_handle)
        except Exception:
            pass
        _lock_handle = None
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


# ---------------- 托盘图标与菜单 ----------------

def make_icon_image():
    """运行时用 Pillow 画一个 64x64 圆角方形 + G 字图标（零图片资产）。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=16, fill=(37, 116, 169, 255))
    try:
        font = ImageFont.load_default(size=34)
    except TypeError:
        font = ImageFont.load_default()
    d.text((32, 31), "G", anchor="mm", fill=(255, 255, 255, 255), font=font)
    return img


def _notify(icon, message: str):
    try:
        icon.notify(message, "GEO 优化系统")
    except Exception:
        pass


def build_menu():
    import pystray

    def on_open(icon, item):
        if not is_server_running():
            start_server()
        webbrowser.open(server_url())

    def on_toggle_pause(icon, item):
        if is_server_running():
            stop_server()
            _notify(icon, "服务已暂停（界面与自动监测已停止），点「启动服务」可恢复")
        else:
            ok = start_server()
            _notify(icon, "服务已启动，点「打开界面」进入" if ok
                    else "服务启动失败，请打开 data/service.log 查看原因")

    def on_restart(icon, item):
        ok = restart_server()
        _notify(icon, "服务已重启，配置已重新加载" if ok
                else "服务重启失败，请打开 data/service.log 查看原因")

    def on_toggle_autostart(icon, item):
        target = not autostart_enabled()
        ok = set_autostart(target)
        if ok:
            _notify(icon, "已开启开机自启（下次开机自动在托盘运行）" if target
                    else "已关闭开机自启")
        else:
            _notify(icon, "开机自启设置失败，请以当前用户重试或检查注册表权限")

    def on_quit(icon, item):
        stop_server()
        icon.stop()

    return pystray.Menu(
        pystray.MenuItem("打开界面", on_open, default=True),
        pystray.MenuItem(
            text=lambda item: "暂停服务" if is_server_running() else "启动服务",
            action=on_toggle_pause),
        pystray.MenuItem("重启服务", on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "开机自启",
            on_toggle_autostart,
            checked=lambda item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )


def main():
    """托盘主循环：单实例锁 → 拉起服务 → 托盘图标消息循环（阻塞）。"""
    import pystray
    if not _acquire_single_instance():
        print("GEO 优化系统托盘已在运行，本实例退出。")
        return
    start_server()
    try:
        icon = pystray.Icon("geo_optimizer", make_icon_image(), "GEO 优化系统", build_menu())
        icon.run()
    finally:
        _release_single_instance()
