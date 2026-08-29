"""org.synccaster.bridge —— GEO 多平台分发原生宿主（Native Messaging Host）。

角色与数据流（协议依据对发布扩展 background.js 的逆向结论）：

    GEO 后端(/api/agent/*) ←HTTP─ 本宿主 ─Native Messaging→ 发布扩展 ─→ 知乎/搜狐/头条

浏览器经 connectNative 拉起本进程（Chrome/Edge 注册表指向它），扩展是服务端：
宿主发 {"id","action","payload"}，扩展回 {"id","ok","data"|"error"}。
宿主主循环：心跳 → 领取待发任务 → create_post → publish_post → 轮询
get_job_status → 把结果回写 GEO。发布动作全部由扩展在浏览器登录态里执行。

设计约束：
- 纯标准库（浏览器拉起时不依赖 venv / 第三方包）；
- stdout 专属 Native Messaging 帧，任何日志只进 stderr 与 data/bridge.log；
- GEO 地址发现顺序：环境变量 GEO_BASE_URL > data/service.json > 默认 5080；
- 扩展断开（stdin EOF）即退出，浏览器负责回收进程。

可选环境变量：GEO_BASE_URL、GEO_AGENT_TOKEN（对应 settings 的
agent_bridge_token）、GEO_HEARTBEAT_SECONDS(30)、GEO_IDLE_SECONDS(5)、
GEO_POLL_SECONDS(4)、GEO_JOB_TIMEOUT_SECONDS(150)。
"""

import json
import os
import struct
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq

HOST_NAME = "org.synccaster.bridge"
HTTP_BRIDGE_PORT = 39123  # 扩展内置的 agent-bridge 探活端口（/v1/health）
JOB_STATE_PUBLISHED = "published"
JOB_STATE_FAILED = "failed"
JOB_STATE_MANUAL = "pending_manual_confirm"
JOB_TERMINAL = (JOB_STATE_PUBLISHED, JOB_STATE_FAILED, JOB_STATE_MANUAL)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_dir() -> str:
    return os.path.join(_repo_root(), "data")


def resolve_geo_base() -> str:
    env = os.environ.get("GEO_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    try:
        with open(os.path.join(_data_dir(), "service.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        port = int(cfg.get("port") or 0)
        if port > 0:
            return f"http://{cfg.get('host') or '127.0.0.1'}:{port}"
    except Exception:
        pass
    return "http://127.0.0.1:5080"


def log(message: str):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + message
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        path = os.path.join(_data_dir(), "bridge.log")
        if os.path.exists(path) and os.path.getsize(path) > 1024 * 1024:
            os.remove(path)  # 简单封顶，日志无需轮转
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- Native Messaging 帧协议 ----------------

def encode_frame(obj) -> bytes:
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def _read_exact(fd, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = fd.read(n - len(buf))
        if not chunk:
            return b""
        buf += chunk
    return buf


def decode_frame(fd):
    """读一帧；流结束返回 None。"""
    head = _read_exact(fd, 4)
    if not head or len(head) < 4:
        return None
    (length,) = struct.unpack("<I", head)
    if length <= 0 or length > 64 * 1024 * 1024:
        return None
    body = _read_exact(fd, length)
    if not body or len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


# ---------------- 扩展侧 RPC 客户端 ----------------

class ExtClient:
    """扩展是服务端：请求发 stdout（二进制帧），响应从 stdin 收（读线程分发）。

    stdout 必须是二进制写入器（main() 传 sys.stdout.buffer；测试传管道）。
    """

    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or getattr(sys.stdout, "buffer", sys.stdout)
        self._lock = threading.Lock()
        self._pending = {}
        self._alive = True
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    @property
    def alive(self) -> bool:
        return self._alive

    def _read_loop(self):
        while self._alive:
            try:
                msg = decode_frame(self.stdin)
            except Exception as e:  # 坏帧：记日志继续，连续坏帧由上层超时兜底
                log(f"坏帧：{e}")
                continue
            if msg is None:
                log("扩展侧输入流已关闭")
                self.close()  # 置断开标记并唤醒所有等待中的请求
                break
            rid = msg.get("id")
            event = self._pending.pop(rid, None)
            if event:
                event["msg"] = msg
                event["ready"].set()

    def request(self, action: str, payload=None, timeout: float = 30.0):
        """发一个 action 并等响应；超时/断开/ok=false 都抛 RuntimeError。"""
        if not self._alive:
            raise RuntimeError("扩展连接已断开")
        rid = uuid.uuid4().hex
        event = {"ready": threading.Event(), "msg": None}
        self._pending[rid] = event
        frame = encode_frame({"id": rid, "action": action, "payload": payload or {}})
        with self._lock:
            try:
                self.stdout.write(frame)
                self.stdout.flush()
            except Exception as e:
                self._pending.pop(rid, None)
                self._alive = False
                raise RuntimeError(f"写入扩展流失败：{e}")
        if not event["ready"].wait(timeout):
            self._pending.pop(rid, None)
            raise RuntimeError(f"扩展响应超时（{action}，{timeout:.0f}s）")
        msg = event["msg"]
        if msg is None:  # 被断开唤醒：等待期间连接已关
            raise RuntimeError("扩展连接已断开")
        if not msg.get("ok"):
            err = msg.get("error") or {}
            raise RuntimeError(f"扩展返回错误（{action}）："
                               f"{err.get('code', 'unknown')} {err.get('message', '')}")
        return msg.get("data")

    def close(self):
        self._alive = False
        for event in list(self._pending.values()):
            event["ready"].set()


# ---------------- GEO 后端 HTTP 客户端 ----------------

class GeoClient:
    def __init__(self, base_url: str, token: str = ""):
        self.base = base_url.rstrip("/")
        self.token = token

    def _call(self, method: str, path: str, payload=None, timeout: float = 20.0):
        data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
        req = urlreq.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Agent-Token", self.token)
        with urlreq.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def heartbeat(self, accounts=None):
        payload = {"version": HOST_NAME + "/1.0"}
        if accounts is not None:
            payload["accounts"] = accounts
        return self._call("POST", "/api/agent/heartbeat", payload)

    def claim(self, platforms=None, limit: int = 3):
        body = self._call("POST", "/api/agent/claim",
                          {"limit": limit, "platforms": platforms or []})
        return body.get("data") or []

    def report(self, task_id: int, state: str, job_id: str = "",
               platform_url: str = "", error_msg: str = ""):
        return self._call("POST", f"/api/agent/tasks/{task_id}/status", {
            "state": state, "job_id": job_id,
            "platform_url": platform_url, "error_msg": error_msg,
        })


# ---------------- 任务编排 ----------------

def extract_platform_url(job_data: dict, platform: str) -> str:
    """从扩展 job 的 results 里取目标平台的落地页 URL。"""
    for r in (job_data.get("results") or []):
        if r.get("platform") == platform and r.get("url"):
            return str(r["url"])
    for r in (job_data.get("results") or []):  # 单平台任务兜底取第一个
        if r.get("url"):
            return str(r["url"])
    return ""


def dispatch_task(ext: ExtClient, geo: GeoClient, task: dict, cfg: dict) -> None:
    """把一个分发任务经扩展走完：建稿 → 发布 → 轮询 → 回写 GEO。"""
    task_id = task["task_id"]
    platform = task["platform"]
    draft = task["draft"]
    platform_home = {"zhihu": "https://zhuanlan.zhihu.com",
                     "sohu": "https://mp.sohu.com",
                     "toutiao": "https://mp.toutiao.com"}.get(platform, "")
    try:
        created = ext.request("create_post", {
            "title": draft["title"], "body_md": draft["body_md"],
            "summary": draft.get("summary") or "",
            "tags": draft.get("tags") or [],
        }, timeout=cfg["rpc_timeout"])
        post_id = (created or {}).get("postId")
        if not post_id:
            raise RuntimeError("扩展未返回 postId")
        target = {"platform": platform}
        if task.get("account_id"):
            target["accountId"] = task["account_id"]
        published = ext.request("publish_post", {
            "postId": post_id, "targets": [target]},
            timeout=cfg["rpc_timeout"] * 2)
        job_id = (published or {}).get("jobId")
        if not job_id:
            raise RuntimeError("扩展未返回 jobId")
        geo.report(task_id, "dispatched", job_id=job_id)
        log(f"任务#{task_id} {platform}：已受理 jobId={job_id}")

        deadline = time.time() + cfg["job_timeout"]
        state, job = "", {}
        while time.time() < deadline:
            time.sleep(cfg["poll"])
            job = ext.request("get_job_status", {"jobId": job_id},
                              timeout=cfg["rpc_timeout"]) or {}
            state = str(job.get("state") or "")
            if state in JOB_TERMINAL:
                break
        if state == JOB_STATE_PUBLISHED:
            url = extract_platform_url(job, platform) or platform_home
            geo.report(task_id, "published", job_id=job_id, platform_url=url)
            log(f"任务#{task_id} {platform}：发布成功 {url}")
        elif state == JOB_STATE_MANUAL:
            geo.report(task_id, "failed", job_id=job_id,
                       error_msg="平台要求人工确认后文章才会上线（pending_manual_confirm），"
                                 "请到平台后台确认，或重试本任务")
            log(f"任务#{task_id} {platform}：需人工确认")
        else:
            detail = str(job.get("error") or "")
            for r in (job.get("results") or []):
                if r.get("error"):
                    detail = str(r["error"])
                    break
            geo.report(task_id, "failed", job_id=job_id,
                       error_msg=detail or f"扩展侧发布未完成（state={state or '超时'}）")
            log(f"任务#{task_id} {platform}：失败 {detail}")
    except Exception as e:
        try:
            geo.report(task_id, "failed", error_msg=str(e)[:500])
        except Exception as e2:
            log(f"任务#{task_id} 失败回写也失败：{e2}")
        log(f"任务#{task_id} {platform}：异常 {e}")


# ---------------- /v1/health 探活桥（扩展 UI 会探测此端口） ----------------

def start_http_bridge():
    """扩展的 CHECK_BRIDGE_AVAILABLE 会 GET 127.0.0.1:39123/v1/health。

    只实现探活；/v1/browser/publish（方案 B 兜底）明确回未实现，
    方案 A（native messaging）正常时不会走到。
    """
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/v1/health"):
                self._json(200, {"status": "ok", "host": HOST_NAME, "version": "1.0"})
            else:
                self._json(404, {"ok": False,
                                 "error": {"code": "not_found", "message": "no such path"}})

        def do_POST(self):
            self._json(501, {"ok": False, "error": {
                "code": "not_implemented",
                "message": "本桥走 native messaging 通道发布；/v1/browser/publish 暂未实现"}})

        def log_message(self, fmt, *args):  # 静默，避免刷 bridge.log
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", HTTP_BRIDGE_PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log(f"HTTP 探活桥已启动 127.0.0.1:{HTTP_BRIDGE_PORT}")
        return server
    except OSError as e:
        log(f"HTTP 探活桥启动失败（不影响 native 通道）：{e}")
        return None


# ---------------- 主循环 ----------------

def collect_accounts(ext: ExtClient, cfg: dict):
    """向扩展要各平台账号登录状态（list_accounts），供分发页显示。

    返回 [{platform, status}]；扩展不可达时返回 None（心跳不带该字段，
    GEO 保留上次快照）。
    """
    try:
        data = ext.request("list_accounts", {}, timeout=cfg["rpc_timeout"]) or []
        return [{"platform": str(a.get("platform") or ""),
                 "status": str(a.get("status") or "")}
                for a in data if isinstance(a, dict)]
    except Exception as e:
        log(f"list_accounts 失败（扩展未连接？）：{e}")
        return None


def main_loop(ext: ExtClient, geo: GeoClient, cfg: dict, max_tasks: int = 0):
    """max_tasks>0 时跑够就返回（测试用）；0 = 常驻直到扩展断开。"""
    server = start_http_bridge()
    last_beat = 0.0
    done = 0
    try:
        while ext.alive:
            now = time.time()
            if now - last_beat >= cfg["heartbeat"]:
                accounts = collect_accounts(ext, cfg)
                try:
                    geo.heartbeat(accounts)
                    last_beat = now
                except Exception as e:
                    log(f"GEO 心跳失败（{e}）；{cfg['idle']}s 后重试")
                    last_beat = now - cfg["heartbeat"] / 2
            try:
                tasks = geo.claim(limit=3)
            except Exception as e:
                log(f"领取任务失败（GEO 未就绪？）：{e}")
                tasks = []
            for task in tasks:
                dispatch_task(ext, geo, task, cfg)
                done += 1
                if max_tasks and done >= max_tasks:
                    return
            if not tasks:
                time.sleep(cfg["idle"])
    finally:
        ext.close()
        if server:
            server.shutdown()


def load_cfg() -> dict:
    def _f(name, default):
        try:
            return float(os.environ.get(name, "") or default)
        except ValueError:
            return float(default)
    return {
        "heartbeat": _f("GEO_HEARTBEAT_SECONDS", 30),
        "idle": _f("GEO_IDLE_SECONDS", 5),
        "poll": _f("GEO_POLL_SECONDS", 4),
        "job_timeout": _f("GEO_JOB_TIMEOUT_SECONDS", 150),
        "rpc_timeout": max(30.0, _f("GEO_POLL_SECONDS", 4) * 5),
    }


def main():
    try:
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    except Exception:
        pass
    log(f"{HOST_NAME} 宿主启动 pid={os.getpid()} geo={resolve_geo_base()}")
    ext = ExtClient(stdin=getattr(sys.stdin, "buffer", sys.stdin),
                    stdout=getattr(sys.stdout, "buffer", sys.stdout))
    geo = GeoClient(resolve_geo_base(), os.environ.get("GEO_AGENT_TOKEN", "").strip())
    try:
        main_loop(ext, geo, load_cfg())
    except KeyboardInterrupt:
        pass
    log("宿主退出")


if __name__ == "__main__":
    main()
