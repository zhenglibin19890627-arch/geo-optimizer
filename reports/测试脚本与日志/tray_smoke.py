"""托盘核心功能冒烟（不进入托盘图标消息循环）。

验证：服务启动（子进程拉起）→ 探活 → 状态文件 → 暂停 → 重启 → 开机自启开关 → 清理。
运行环境要求：本机 python + pythonw.exe 可用；会短暂在后台启动/停止 GEO 服务。
"""

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("GEO_NO_SCHEDULER", "1")  # 子进程继承，测试期间不跑定时器

import requests  # noqa: E402

from geo import tray  # noqa: E402


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return (name, ok, detail)


results = []

# 1. 图标生成
img = tray.make_icon_image()
results.append(check("图标生成 64x64 RGBA", img.size == (64, 64) and img.mode == "RGBA"))

# 2. 启动服务
ok = tray.start_server()
results.append(check("启动服务", ok, f"state={tray.read_state()}"))
time.sleep(1.0)
running = tray.is_server_running()
results.append(check("服务探活", running))
if running:
    url = tray.server_url()
    r = requests.get(url + "/api/overview", timeout=5)
    results.append(check("HTTP /api/overview", r.status_code == 200 and r.json()["code"] == 0,
                         f"url={url}"))

# 3. 重复启动不重复拉起（幂等）
pid_before = tray.read_state().get("pid")
ok = tray.start_server()
pid_after = tray.read_state().get("pid")
results.append(check("重复启动幂等", ok and pid_before == pid_after,
                     f"pid {pid_before} == {pid_after}"))

# 4. 暂停服务
ok = tray.stop_server()
time.sleep(0.5)
results.append(check("暂停服务", ok and not tray.is_server_running()))

# 5. 重启服务
ok = tray.restart_server()
time.sleep(1.0)
results.append(check("重启服务", ok and tray.is_server_running(),
                     f"state={tray.read_state()}"))

# 6. 开机自启开关（HKCU Run）
before = tray.autostart_enabled()
ok_set = tray.set_autostart(True)
time.sleep(0.2)
on = tray.autostart_enabled()
ok_unset = tray.set_autostart(False)
time.sleep(0.2)
off = not tray.autostart_enabled()
results.append(check("开机自启开启/查询/关闭", ok_set and on and ok_unset and off,
                     f"before={before}"))

# 7. 清理：停止服务，不留后台进程
tray.stop_server()
time.sleep(0.3)
results.append(check("清理后无残留服务", not tray.is_server_running()))

failed = [n for n, ok, d in results if not ok]
print("=" * 50)
print("TRAY_SMOKE_RESULT:", "PASS" if not failed else f"FAIL {failed}")
sys.exit(0 if not failed else 1)
