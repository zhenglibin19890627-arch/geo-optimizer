"""分发桥宿主纯函数单测（帧协议 / 落地页提取 / 地址发现）。"""

import io
import json
import os
import struct
import sys

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
