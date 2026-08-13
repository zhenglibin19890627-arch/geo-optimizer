"""GEO 优化系统：唯一入口。

零代码用户只需要两行命令：
    pip install -r requirements.txt
    python app.py
然后浏览器打开程序提示的地址即可。
"""

import socket
import threading
import webbrowser

from geo.web import create_app

app = create_app()

PORT_RANGE = range(5080, 5091)


def find_free_port():
    """5080 被占用时自动顺延 5081-5090，并打印大白话提示。"""
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def main():
    port = find_free_port()
    if port is None:
        print("【提示】5080-5090 端口都被占用了，请关掉一些正在运行的程序后再试。")
        return
    if port != 5080:
        print(f"【提示】端口 5080 已被其他程序占用，本程序改用端口 {port}。")
    print("=" * 50)
    print("GEO 优化系统已启动！")
    print(f"请在浏览器打开：http://127.0.0.1:{port}")
    print("（如果浏览器没有自动打开，请复制上面的地址到浏览器访问）")
    print("=" * 50)
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
