"""托盘单实例锁 + 服务生命周期合并验证（无 GUI 循环、无注册表、跑完自清理）。

锁逻辑（Windows 文件句柄独占锁）：
1) 首次拿锁成功；2) 持锁期间二次拿锁被共享冲突拒绝；3) 释放后可重新拿锁。
服务生命周期：启动 → 探活 → HTTP → 幂等 → 暂停 → 重启 → 清理。
"""

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("GEO_NO_SCHEDULER", "1")

import requests  # noqa: E402

from geo import tray  # noqa: E402


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return (name, ok, detail)


results = []

# ---- 单实例锁 ----
# 1) 活进程 pid 占锁：本机当前 python 进程之一（用当前测试脚本自己不行——acquire 会跳过自身 pid，
#    借一个肯定活着的系统进程：取父进程 cmd 的 pid 不可靠，这里起一个短暂 sleep 子进程占位）
# ---- 单实例锁（Windows 文件句柄独占锁：同进程二次拿锁也会得到共享冲突拒绝） ----
ok_take = tray._acquire_single_instance() is True
results.append(check("首次拿锁成功", ok_take and tray.LOCK_FILE.exists()))
ok_refuse = tray._acquire_single_instance() is False
results.append(check("持锁期间二次拿锁→拒绝第二实例", ok_refuse))

# 释放后可重新获取（崩溃/退出场景由系统自动释放句柄）
tray._release_single_instance()
results.append(check("release 后锁文件已删", not tray.LOCK_FILE.exists()))
ok_take2 = tray._acquire_single_instance() is True
results.append(check("释放后重新拿锁成功", ok_take2))
tray._release_single_instance()

# ---- 服务生命周期 ----
ok = tray.start_server()
results.append(check("启动服务", ok, f"state={tray.read_state()}"))
time.sleep(1.0)
results.append(check("服务探活", tray.is_server_running()))
if tray.is_server_running():
    r = requests.get(tray.server_url() + "/api/overview", timeout=5)
    results.append(check("HTTP /api/overview",
                         r.status_code == 200 and r.json()["code"] == 0,
                         f"url={tray.server_url()}"))

pid_before = tray.read_state().get("pid")
ok = tray.start_server()
results.append(check("重复启动幂等", ok and tray.read_state().get("pid") == pid_before))

ok = tray.stop_server()
time.sleep(0.5)
results.append(check("暂停服务", ok and not tray.is_server_running()))

ok = tray.restart_server()
time.sleep(1.0)
results.append(check("重启服务", ok and tray.is_server_running()))

tray.stop_server()
time.sleep(0.3)
results.append(check("清理后无残留", not tray.is_server_running()))

failed = [n for n, ok, d in results if not ok]
print("=" * 50)
print("COMBINED_RESULT:", "PASS" if not failed else f"FAIL {failed}")
sys.exit(0 if not failed else 1)
