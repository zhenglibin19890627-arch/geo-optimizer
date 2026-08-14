"""GEO 优化系统：服务启动辅助（端口查找，供 app.py 前台入口与托盘共用）。"""

import socket

DEFAULT_HOST = "127.0.0.1"
PORT_RANGE = range(5080, 5091)


def find_free_port() -> int:
    """5080 被占用时自动顺延 5081-5090；全部占用返回 None。"""
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((DEFAULT_HOST, port))
                return port
            except OSError:
                continue
    return None
