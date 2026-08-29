"""分发桥联调自检：一条命令定位链路断在哪一环。

用法（任意目录）：python bridge/preflight.py

检查项：
  1. 注册表（Chrome/Edge HKCU）指向的 manifest 是否存在且格式合法
  2. manifest 的 allowed_origins / 启动 bat / bat 里的 python 是否有效
  3. GEO 服务是否可达（自动发现地址）、/api/agent/queue 是否应答
  4. 分发桥心跳是否新鲜（扩展拉起宿主后才会出现）
  5. 提示预测扩展 ID 与加载方法
"""

import json
import os
import sys
import winreg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge.geo_bridge_host import resolve_geo_base  # noqa: E402

MANIFEST_DIR = os.path.join(os.environ.get("LOCALAPPDATA",
                                           os.path.expanduser("~")), "GEO", "bridge")
HOST_NAME = "org.synccaster.bridge"
REG_ROOTS = {
    "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
}

OK, BAD, WARN = "[OK]  ", "[FAIL]", "[WAIT]"


def check(name: str, passed, detail: str = "") -> bool:
    mark = OK if passed is True else BAD if passed is False else WARN
    print(f"{mark} {name}" + (f" —— {detail}" if detail else ""))
    return passed is True


def main():
    results = []
    print("=" * 60)
    print("GEO 分发桥自检")
    print("=" * 60)

    # 1. 注册表与 manifest
    manifest = None
    for browser, root in REG_ROOTS.items():
        path = ""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{root}\{HOST_NAME}") as k:
                path, _ = winreg.QueryValueEx(k, "")
        except OSError:
            pass
        if not check(f"{browser} 注册表键", bool(path),
                     path or "未注册，先运行 bridge/register_host.py"):
            continue
        exists = os.path.exists(path)
        check(f"{browser} manifest 文件存在", exists, path)
        if not exists:
            continue
        try:
            manifest = json.load(open(path, encoding="utf-8"))
            check(f"{browser} manifest 格式", manifest.get("name") == HOST_NAME
                  and manifest.get("type") == "stdio",
                  f"name={manifest.get('name')}")
        except Exception as e:
            check(f"{browser} manifest 格式", False, str(e))

    # 2. allowed_origins / bat / python
    if manifest:
        origins = manifest.get("allowed_origins") or []
        check("allowed_origins", bool(origins),
              "；".join(origins) + "（未打包扩展按加载路径预测；若与实际 ID 不符，"
              "把实际 ID 发给开发者重跑 register_host.py --ext-id <实际ID>）")
        bat = manifest.get("path", "")
        check("启动 bat 存在", os.path.exists(bat), bat)
        if os.path.exists(bat):
            content = open(bat, encoding="mbcs", errors="replace").read()
            py = content.split('"')[1] if '"' in content else ""
            check("bat 里的 python 解释器存在", os.path.exists(py), py)
            script = content.split('"')[3] if content.count('"') >= 4 else ""
            check("宿主脚本存在", os.path.exists(script), script)

    # 3. GEO 服务
    base = resolve_geo_base()
    print(f"\nGEO 地址（自动发现）：{base}")
    try:
        from urllib import request as urlreq
        with urlreq.urlopen(base + "/api/agent/queue", timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        check("GEO 服务可达且 agent 端点应答", body.get("code") == 0,
              f"队列 {body.get('data', {}).get('stats')}")
        online = (body.get("data") or {}).get("agent_online")
        check("分发桥心跳新鲜（扩展已拉起宿主）", online is True,
              "在线" if online else "尚无心跳：请先在浏览器加载扩展并重启浏览器")
        # 各平台发布就绪度（来自宿主心跳捎带的账号快照）
        try:
            with urlreq.urlopen(base + "/api/distribution/overview?brand_id=1",
                                timeout=5) as resp:
                ov = json.loads(resp.read().decode("utf-8")).get("data") or {}
            accounts = ((ov.get("agent") or {}).get("accounts")) or []
            if accounts:
                for a in accounts:
                    if a.get("is_default") and a.get("enabled", True):
                        mark, note = OK, "就绪（默认发布）"
                    elif a.get("enabled", True):
                        mark, note = WARN, "已登录但未设默认发布——去扩展设置里补设"
                    else:
                        mark, note = BAD, "已停用"
                    print(f"{mark} 平台 {a.get('platform')} "
                          f"{a.get('nickname') or ''} —— {note}")
            else:
                print(f"{WARN} 平台账号快照为空（宿主换代后心跳会带来；"
                      f"需重启浏览器更新宿主）")
        except Exception:
            pass
    except Exception as e:
        check("GEO 服务可达", False, f"{e}（GEO 系统没启动？python app.py）")

    # 4. 操作指引（扩展已安装并在线时的常态指引）
    print("\n下一步：")
    print("  1. 若上方账号快照为空：完全重启浏览器（宿主换代后开始上报）")
    print("  2. 扩展设置页为知乎/搜狐/头条设置「默认发布账号」（就绪的关键）")
    print("  3. GEO 分发页 → 审阅稿件 → 勾选平台 → 分发")
    print("  日志：data/bridge.log")
    print("=" * 60)


if __name__ == "__main__":
    main()
