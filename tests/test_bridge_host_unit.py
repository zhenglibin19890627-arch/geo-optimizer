"""分发桥宿主纯函数单测（帧协议 / 落地页提取 / 地址发现 / 心跳线程 / 幂等透传）。"""

import io
import json
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge import geo_bridge_host as host


# ---------------- 帧协议 ----------------

def test_帧编解码往返():
    obj = {"id": "abc", "action": "create_post",
           "payload": {"title": "中文标题", "body_md": "## 正文\n换行"}}
    buf = io.BytesIO(host.encode_frame(obj))
    assert host.decode_frame(buf) == obj


def test_帧长度前缀为小端四字节():
    frame = host.encode_frame({"a": 1})
    (n,) = struct.unpack("<I", frame[:4])
    assert n == len(frame) - 4
    assert json.loads(frame[4:].decode("utf-8")) == {"a": 1}


def test_流结束返回None_超长帧拒绝():
    assert host.decode_frame(io.BytesIO(b"")) is None
    bad = io.BytesIO(struct.pack("<I", 128 * 1024 * 1024) + b"x")
    assert host.decode_frame(bad) is None


# ---------------- 落地页提取 ----------------

def test_落地页提取_优先命中平台():
    job = {"results": [{"platform": "sohu", "url": "https://mp.sohu.com/x"},
                       {"platform": "zhihu", "url": "https://zhuanlan.zhihu.com/p/1"}]}
    assert host.extract_platform_url(job, "zhihu") == "https://zhuanlan.zhihu.com/p/1"


def test_落地页提取_无命中返回空():
    assert host.extract_platform_url({"results": []}, "zhihu") == ""
    assert host.extract_platform_url({}, "zhihu") == ""


# ---------------- GEO 地址发现 ----------------

def test_环境变量优先(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_BASE_URL", "http://127.0.0.1:9999/")
    assert host.resolve_geo_base() == "http://127.0.0.1:9999"


def test_兜底默认地址(tmp_path, monkeypatch):
    monkeypatch.delenv("GEO_BASE_URL", raising=False)
    monkeypatch.setattr(host, "_data_dir", lambda: str(tmp_path))  # 无 service.json
    assert host.resolve_geo_base() == "http://127.0.0.1:5080"


def test_service_json发现端口(tmp_path, monkeypatch):
    monkeypatch.delenv("GEO_BASE_URL", raising=False)
    (tmp_path / "service.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 5088}), encoding="utf-8")
    monkeypatch.setattr(host, "_data_dir", lambda: str(tmp_path))
    assert host.resolve_geo_base() == "http://127.0.0.1:5088"


# ---------------- 心跳线程 ----------------

class _FakeExt:
    alive = True

    def request(self, action, payload=None, timeout=30.0):
        return []  # list_accounts → 空账号清单


class _FakeGeo:
    def __init__(self, fail_first=False):
        self.beats = []
        self.fail_first = fail_first

    def heartbeat(self, accounts):
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("GEO 未就绪")
        self.beats.append(accounts)


def test_心跳循环_周期触发且异常不带崩线程():
    geo = _FakeGeo(fail_first=True)  # 首拍失败：循环必须存活并继续下一拍
    stop = threading.Event()
    th = threading.Thread(target=host.heartbeat_loop, daemon=True,
                          args=(_FakeExt(), geo, {"heartbeat": 0.05,
                                                  "rpc_timeout": 1}, stop))
    th.start()
    deadline = time.time() + 5
    while len(geo.beats) < 2 and time.time() < deadline:
        time.sleep(0.02)
    stop.set()
    th.join(timeout=2)
    assert len(geo.beats) >= 2, "异常后心跳循环应继续触发"
    assert not th.is_alive(), "stop 置位后心跳线程应退出"


def test_启动心跳_返回停止开关且线程为守护():
    stop = host.start_heartbeat(_FakeExt(), _FakeGeo(), {"heartbeat": 30,
                                                         "rpc_timeout": 1})
    assert isinstance(stop, threading.Event)
    time.sleep(0.2)  # 首拍立即发出（不等首个间隔）
    for th in threading.enumerate():
        if th.name == "heartbeat":
            assert th.daemon is True
    stop.set()


# ---------------- 幂等键透传（宿主 → 扩展） ----------------

class _ScriptExt:
    """按动作剧本应答的扩展桩，记录全部请求 payload。"""

    def __init__(self):
        self.calls = []

    def request(self, action, payload=None, timeout=30.0):
        self.calls.append((action, payload))
        if action == "create_post":
            return {"postId": "p-1"}
        if action == "publish_post":
            return {"jobId": "j-9"}
        if action == "get_job_status":
            return {"state": "published",
                    "results": [{"platform": "zhihu",
                                 "url": "https://zhuanlan.zhihu.com/p/5"}]}
        return {}


class _ReportGeo:
    def __init__(self):
        self.reports = []

    def report(self, task_id, state, **kw):
        self.reports.append((task_id, state, kw))
        return {}


_CFG = {"rpc_timeout": 1, "poll": 0.01, "job_timeout": 1}


def test_幂等键透传到扩展建稿与发布():
    ext, geo = _ScriptExt(), _ReportGeo()
    task = {"task_id": 7, "platform": "zhihu", "idempotency_key": "7",
            "draft": {"title": "标题", "body_md": "正文"}}
    host.dispatch_task(ext, geo, task, _CFG)
    assert ext.calls[0][0] == "create_post" and ext.calls[0][1]["externalId"] == "7"
    assert ext.calls[1][0] == "publish_post" and ext.calls[1][1]["externalId"] == "7"
    assert ext.calls[1][1]["postId"] == "p-1"
    assert [s for _, s, _ in geo.reports] == ["dispatched", "published"]


def test_无幂等键_向后兼容不带字段():
    ext, geo = _ScriptExt(), _ReportGeo()
    task = {"task_id": 8, "platform": "zhihu",
            "draft": {"title": "标题", "body_md": "正文"}}
    host.dispatch_task(ext, geo, task, _CFG)  # 旧版 GEO 不下发幂等键
    assert "externalId" not in ext.calls[0][1]
    assert "externalId" not in ext.calls[1][1]
    assert [s for _, s, _ in geo.reports] == ["dispatched", "published"]
