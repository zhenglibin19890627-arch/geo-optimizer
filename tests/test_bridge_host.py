"""原生宿主端到端桩测试。

测试进程同时扮演两边的对手方：
- 扩展：读宿主 stdout 的 Native Messaging 帧，按剧本应答 create_post /
  publish_post / get_job_status；
- GEO 后端：threading HTTP 桩服务，供应 /api/agent/* 三端点并记录全部回写。

跑的是真实的 bridge/geo_bridge_host.py 子进程（浏览器拉起方式一致）。
"""

import json
import os
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from geo.core.distribution import PLATFORM_DOMAINS  # noqa: F401  (口径锚定)
from tests.test_agent import _drain_queue  # noqa: F401  (复用排空逻辑)

HOST_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bridge", "geo_bridge_host.py")


# ---------------- 帧读写（测试侧扮演扩展） ----------------

def read_frame(fd):
    head = fd.read(4)
    if not head or len(head) < 4:
        return None
    (length,) = struct.unpack("<I", head)
    return json.loads(fd.read(length).decode("utf-8"))


def write_frame(fd, obj):
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    fd.write(struct.pack("<I", len(payload)))
    fd.write(payload)
    fd.flush()


# ---------------- GEO 桩服务 ----------------

@pytest.fixture()
def geo_stub():
    recorded = {"heartbeats": 0, "claims": 0, "reports": []}

    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/agent/heartbeat":
                recorded["heartbeats"] += 1
                self._json({"code": 0, "data": {}})
            elif self.path == "/api/agent/claim":
                recorded["claims"] += 1
                tasks = [{
                    "task_id": 101,
                    "platform": "zhihu",
                    "platform_name": "知乎",
                    "account_id": "",
                    "draft": {"draft_id": 9, "title": "桩标题", "body_md": "## 桩正文",
                              "summary": "桩摘要", "tags": ["云澜", "中台"]},
                }]
                self._json({"code": 0, "data": tasks if recorded["claims"] == 1 else []})
            elif self.path.startswith("/api/agent/tasks/"):
                recorded["reports"].append(payload)
                self._json({"code": 0, "data": {}})
            else:
                self._json({"code": 1, "message": "unknown"}, 404)

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], recorded
    server.shutdown()


# ---------------- 端到端 ----------------

def test_宿主全链路_心跳领取建稿发布回写(geo_stub):
    port, recorded = geo_stub
    env = dict(os.environ)
    env.update({
        "GEO_BASE_URL": f"http://127.0.0.1:{port}",
        "GEO_HEARTBEAT_SECONDS": "1",
        "GEO_IDLE_SECONDS": "0.2",
        "GEO_POLL_SECONDS": "0.2",
        "GEO_JOB_TIMEOUT_SECONDS": "10",
        "PYTHONIOENCODING": "utf-8",
    })
    proc = subprocess.Popen(
        [sys.executable, HOST_SCRIPT], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)

    try:
        # 请求 1：心跳后立刻领取 → create_post
        req = read_frame(proc.stdout)
        assert req["action"] == "create_post", req
        assert req["payload"]["title"] == "桩标题"
        assert req["payload"]["tags"] == ["云澜", "中台"]
        write_frame(proc.stdin, {"id": req["id"], "ok": True, "data": {"postId": "p-1"}})

        # 请求 2：publish_post
        req = read_frame(proc.stdout)
        assert req["action"] == "publish_post", req
        assert req["payload"]["postId"] == "p-1"
        assert req["payload"]["targets"] == [{"platform": "zhihu"}]
        write_frame(proc.stdin, {"id": req["id"], "ok": True, "data": {"jobId": "j-1"}})

        # 请求 3+：轮询 job → 第一次运行中，第二次成功
        req = read_frame(proc.stdout)
        assert req["action"] == "get_job_status"
        write_frame(proc.stdin, {"id": req["id"], "ok": True,
                                 "data": {"state": "running", "progress": 30}})
        req = read_frame(proc.stdout)
        assert req["action"] == "get_job_status"
        write_frame(proc.stdin, {"id": req["id"], "ok": True, "data": {
            "state": "published", "progress": 100,
            "results": [{"platform": "zhihu", "status": "published",
                         "url": "https://zhuanlan.zhihu.com/p/66"}]}})

        # 等宿主把结果回写 GEO 桩
        deadline = time.time() + 15
        while time.time() < deadline and len(recorded["reports"]) < 2:
            time.sleep(0.2)
        states = [r["state"] for r in recorded["reports"]]
        assert states == ["dispatched", "published"], recorded["reports"]
        assert recorded["reports"][0]["job_id"] == "j-1"
        assert recorded["reports"][1]["platform_url"] == "https://zhuanlan.zhihu.com/p/66"
        assert recorded["heartbeats"] >= 1
    finally:
        # 关闭 stdin 模拟扩展断开 → 宿主应自行退出
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise


def test_宿主对扩展错误的处理(geo_stub):
    """扩展返回 ok=false（如未登录）→ 宿主把错误如实回写 GEO。"""
    port, recorded = geo_stub
    env = dict(os.environ)
    env.update({
        "GEO_BASE_URL": f"http://127.0.0.1:{port}",
        "GEO_IDLE_SECONDS": "0.2",
        "GEO_POLL_SECONDS": "0.2",
        "PYTHONIOENCODING": "utf-8",
    })
    # 桩改成领取即给任务
    proc = subprocess.Popen(
        [sys.executable, HOST_SCRIPT], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        req = read_frame(proc.stdout)
        assert req["action"] == "create_post"
        write_frame(proc.stdin, {"id": req["id"], "ok": False, "error": {
            "code": "validation_error", "message": "标题不能为空"}})
        deadline = time.time() + 15
        while time.time() < deadline and not recorded["reports"]:
            time.sleep(0.2)
        assert recorded["reports"], "宿主应回写失败"
        rep = recorded["reports"][-1]
        assert rep["state"] == "failed"
        assert "validation_error" in rep["error_msg"]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
