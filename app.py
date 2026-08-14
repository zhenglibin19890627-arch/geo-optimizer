"""GEO 优化系统：唯一入口。

零代码用户只需要两行命令：
    pip install -r requirements.txt
    python app.py
然后浏览器打开程序提示的地址即可。

托盘程序（tray_app.py）会以 `pythonw app.py --no-browser` 方式在后台拉起本服务，
启动成功后把端口/进程号写入 data/service.json，供托盘读取并打开界面。
"""

import json
import os
import sys
import threading
import webbrowser

from geo import config
from geo.server import find_free_port
from geo.web import create_app

app = create_app()


def main():
    args = sys.argv[1:]
    no_browser = "--no-browser" in args
    port = None
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            port = None

    if port is None:
        port = find_free_port()
        if port is None:
            print("【提示】5080-5090 端口都被占用了，请关掉一些正在运行的程序后再试。")
            return
        if port != 5080:
            print(f"【提示】端口 5080 已被其他程序占用，本程序改用端口 {port}。")

    # 服务状态文件（托盘据此拿到端口打开界面）：写失败不影响启动
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / "service.json").write_text(json.dumps({
            "host": "127.0.0.1",
            "port": port,
            "pid": os.getpid(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    print("=" * 50)
    print("GEO 优化系统已启动！")
    print(f"请在浏览器打开：http://127.0.0.1:{port}")
    print("（如果浏览器没有自动打开，请复制上面的地址到浏览器访问）")
    print("=" * 50)
    if not no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
