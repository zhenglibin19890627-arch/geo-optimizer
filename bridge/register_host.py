"""注册/注销 org.synccaster.bridge 原生宿主（Chrome + Edge，当前用户 HKCU）。

用法（在仓库根目录）：
    python bridge/register_host.py --ext-id <32位扩展ID>                # 双浏览器注册
    python bridge/register_host.py --ext-id <ID> --browser chrome      # 仅 Chrome
    python bridge/register_host.py --print                             # 只看将写入的内容
    python bridge/register_host.py --unregister                        # 清理注册

原理：Chrome/Edge 启动扩展的 connectNative 时，查注册表
HKCU\\Software\\(Google\\Chrome|Microsoft\\Edge)\\NativeMessagingHosts\\org.synccaster.bridge
拿到 manifest JSON 路径，manifest 里 path 指向启动脚本（.bat 包一层 python），
allowed_origins 必须与扩展 ID 完全一致——这就是为什么需要你提供扩展 ID。
"""

import argparse
import json
import os
import subprocess
import sys

HOST_NAME = "org.synccaster.bridge"
MANIFEST_DIR = os.path.join(os.environ.get("LOCALAPPDATA",
                                           os.path.expanduser("~")), "GEO", "bridge")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_SCRIPT = os.path.join(REPO_ROOT, "bridge", "geo_bridge_host.py")
REG_ROOTS = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
}


def validate_ext_id(ext_id: str) -> str:
    ext_id = (ext_id or "").strip().lower()
    if len(ext_id) != 32 or any(c not in "abcdefghijklmnop" for c in ext_id):
        raise SystemExit("扩展 ID 必须是 32 位小写字母（a-p）。"
                         "在 chrome://extensions 开发者模式里能看到。")
    return ext_id


def build_manifest(ext_id: str) -> dict:
    return {
        "name": HOST_NAME,
        "description": "GEO 多平台分发桥：把审阅后的稿件经发布扩展发到知乎/搜狐/头条",
        "path": os.path.join(MANIFEST_DIR, "geo_bridge_host.bat"),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }


def build_bat() -> str:
    """Windows 下 manifest 的 path 必须是可执行文件，用 .bat 包一层 python。

    pythonw 无控制台窗口，但 Native Messaging 需要 stdio 管道，用 python；
    加 /min 尽量减少闪窗（Chrome 拉起时仍可能闪一下，属正常）。
    """
    return ("@echo off\r\n"
            f'"{sys.executable}" "{HOST_SCRIPT}"\r\n')


def _reg_op(browser: str, exe_args: list) -> bool:
    """用 reg.exe 写/查/删注册表（不引第三方依赖）。"""
    key = rf"HKCU\{REG_ROOTS[browser]}\{HOST_NAME}"
    try:
        r = subprocess.run(["reg", exe_args[0], key] + exe_args[1:],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception as e:
        print(f"  {browser} 注册表操作失败：{e}")
        return False


def register(ext_id: str, browsers: list, dry_run: bool = False) -> bool:
    validate_ext_id(ext_id)
    manifest = build_manifest(ext_id)
    bat = build_bat()
    print("将写入：")
    print(f"  manifest → {os.path.join(MANIFEST_DIR, HOST_NAME + '.json')}")
    print(f"  启动脚本 → {manifest['path']}（python = {sys.executable}）")
    for b in browsers:
        print(f"  HKCU\\{REG_ROOTS[b]}\\{HOST_NAME}")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if dry_run:
        return True

    os.makedirs(MANIFEST_DIR, exist_ok=True)
    manifest_path = os.path.join(MANIFEST_DIR, HOST_NAME + ".json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(manifest["path"], "w", encoding="ascii") as f:
        f.write(bat)
    ok_all = True
    for b in browsers:
        written = _reg_op(b, ["add", "/ve", "/t", "REG_SZ", "/d", manifest_path, "/f"])
        print(f"  {'✓' if written else '✗'} {b} 注册" + ("完成" if written else "失败"))
        ok_all = ok_all and written
    print("注册完成。重启浏览器后，扩展启动时会自动连上本桥。")
    return ok_all


def unregister(browsers: list) -> bool:
    ok_all = True
    for b in browsers:
        deleted = _reg_op(b, ["delete", "/f"])
        print(f"  {'✓' if deleted else '（本无注册）'} {b} 已清理")
    try:
        for p in (os.path.join(MANIFEST_DIR, HOST_NAME + ".json"),
                  os.path.join(MANIFEST_DIR, "geo_bridge_host.bat")):
            if os.path.exists(p):
                os.remove(p)
    except OSError:
        pass
    return ok_all


def main():
    ap = argparse.ArgumentParser(description="注册 GEO 分发桥原生宿主")
    ap.add_argument("--ext-id", help="发布扩展的 32 位 ID（chrome://extensions 开发者模式）")
    ap.add_argument("--browser", choices=["chrome", "edge", "both"], default="both")
    ap.add_argument("--print", dest="dry_run", action="store_true", help="只打印不写入")
    ap.add_argument("--unregister", action="store_true", help="清理注册与文件")
    args = ap.parse_args()
    browsers = list(REG_ROOTS) if args.browser == "both" else [args.browser]
    if args.unregister:
        unregister(browsers)
        return
    if not args.ext_id:
        ap.error("缺少 --ext-id（或用 --print 预览、--unregister 清理）")
    sys.exit(0 if register(args.ext_id, browsers, args.dry_run) else 1)


if __name__ == "__main__":
    main()
