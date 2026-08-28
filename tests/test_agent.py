"""二期多平台分发回归测试：agent 端点（宿主视角）+ 分发页平台任务编排。

临时库隔离，零真实网络/扩展调用。协议依据发布扩展逆向结论：
create_post {title, body_md, summary?, tags?} → publish_post {targets} → 轮询。
"""

import os
import tempfile

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.models import db as database
from geo.models.migration import MIGRATION_VERSION
from geo.core import distribution


@pytest.fixture(scope="module")
def tmpdb():
    tmp = tempfile.mktemp(suffix=".db")
    old_engine, old_session = database.engine, database.SessionLocal
    database.engine = create_engine(
        f"sqlite:///{tmp}", connect_args={"check_same_thread": False})
    database.SessionLocal = sessionmaker(bind=database.engine, expire_on_commit=False)
    database.init_db()
    conn = database.engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA user_version = {MIGRATION_VERSION}")
        conn.commit()
    finally:
        conn.close()
    yield database
    database.engine, database.SessionLocal = old_engine, old_session
    try:
        os.remove(tmp)
    except OSError:
        pass


@pytest.fixture(scope="module")
def app(tmpdb):
    from geo.web import create_app
    flask_app = create_app()
    flask_app.testing = True
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed_draft(brand_id=31):
    with database.session_scope() as s:
        if not s.get(database.BrandProfile, brand_id):
            s.add(database.BrandProfile(id=brand_id, brand_name="多平台测试牌",
                                        product_name="内容中台", auto_monitor=True))
        row = database.DistributionDraft(
            brand_id=brand_id, title="多平台标题", body_md="## 正文",
            summary="摘要", tags="a,b", status="draft")
        s.add(row)
        s.flush()
        return row.id


# ---------------- 勾选平台建任务 ----------------

def test_勾选平台建任务与去重(tmpdb, client):
    draft_id = _seed_draft()
    r = client.post(f"/api/distribution/drafts/{draft_id}/channels", json={
        "brand_id": 31, "platforms": ["zhihu", "sohu", "toutiao", "zhihu"]})
    body = r.get_json()
    assert body["code"] == 0, body
    assert len(body["data"]) == 3  # 重复勾选去重
    assert {t["platform"] for t in body["data"]} == {"zhihu", "sohu", "toutiao"}
    assert all(t["status"] == "pending" for t in body["data"])

    # 同平台已有 pending 不重复建
    r = client.post(f"/api/distribution/drafts/{draft_id}/channels", json={
        "brand_id": 31, "platforms": ["zhihu"]})
    assert r.get_json()["data"] == []

    # 不支持的平台 → 大白话报错
    r = client.post(f"/api/distribution/drafts/{draft_id}/channels", json={
        "brand_id": 31, "platforms": ["xiaohongshu"]})
    assert r.get_json()["code"] == 1


# ---------------- 宿主领取与回写 ----------------

def _drain_queue(client):
    """排空残留 pending（模块级共享库，保证用例间队列隔离）。"""
    while True:
        items = client.post("/api/agent/claim", json={"limit": 10}).get_json()["data"]
        if not items:
            return
        for i in items:
            client.post(f"/api/agent/tasks/{i['task_id']}/status",
                        json={"state": "failed", "error_msg": "test-drain"})


def test_宿主领取与状态回写(tmpdb, client):
    _drain_queue(client)
    draft_id = _seed_draft()
    client.post(f"/api/distribution/drafts/{draft_id}/channels", json={
        "brand_id": 31, "platforms": ["zhihu", "sohu"]})

    # 领取：pending → dispatching，带稿件全文
    r = client.post("/api/agent/claim", json={"limit": 5})
    items = r.get_json()["data"]
    assert len(items) == 2
    assert {i["platform"] for i in items} == {"zhihu", "sohu"}
    assert items[0]["draft"]["title"] == "多平台标题"
    assert items[0]["draft"]["tags"] == ["a", "b"]
    r = client.get(f"/api/distribution/drafts/{draft_id}/channels?brand_id=31")
    assert {t["status"] for t in r.get_json()["data"]} == {"dispatching"}

    # 回写 dispatched（拿到 jobId）
    tid = items[0]["task_id"]
    r = client.post(f"/api/agent/tasks/{tid}/status", json={
        "state": "dispatched", "job_id": "job-1"})
    assert r.get_json()["code"] == 0

    # 回写 published（必须带 platform_url）
    r = client.post(f"/api/agent/tasks/{tid}/status", json={"state": "published"})
    assert r.get_json()["code"] == 1
    r = client.post(f"/api/agent/tasks/{tid}/status", json={
        "state": "published", "platform_url": "https://zhuanlan.zhihu.com/p/99"})
    assert r.get_json()["code"] == 0

    # 回写 failed
    tid2 = items[1]["task_id"]
    client.post(f"/api/agent/tasks/{tid2}/status", json={
        "state": "failed", "error_msg": "未登录知乎"})

    # published 平台主域并入已布点
    assert "zhihu.com" in distribution.deployed_domains(31)
    # 失败的不算
    assert "sohu.com" not in distribution.deployed_domains(31)

    # 失败任务单独重试 → pending，可再被领取
    r = client.post(f"/api/distribution/channels/{tid2}/retry", json={"brand_id": 31})
    assert r.get_json()["code"] == 0
    r = client.post("/api/agent/claim", json={"platforms": ["sohu"]})
    assert len(r.get_json()["data"]) == 1


def test_心跳与队列概览(tmpdb, client):
    client.post("/api/agent/heartbeat", json={"version": "0.1.0"})
    assert distribution.agent_online() is True
    r = client.get("/api/agent/queue")
    data = r.get_json()["data"]
    assert data["agent_online"] is True
    assert [p["id"] for p in data["platforms"]] == ["zhihu", "sohu", "toutiao"]


def test_桥令牌鉴权(tmpdb, client, monkeypatch):
    monkeypatch.setattr(database, "get_setting",
                        lambda key, default=None:
                        "secret123" if key == "agent_bridge_token" else default)
    r = client.post("/api/agent/heartbeat", json={})
    assert r.get_json()["code"] == 401
    r = client.post("/api/agent/heartbeat", json={},
                    headers={"X-Agent-Token": "wrong"})
    assert r.get_json()["code"] == 401
    r = client.post("/api/agent/heartbeat", json={},
                    headers={"X-Agent-Token": "secret123"})
    assert r.get_json()["code"] == 0


def test_删除稿件级联清理平台任务(tmpdb, client):
    draft_id = _seed_draft(brand_id=32)
    client.post(f"/api/distribution/drafts/{draft_id}/channels", json={
        "brand_id": 32, "platforms": ["zhihu"]})
    r = client.delete(f"/api/distribution/drafts/{draft_id}", json={"brand_id": 32})
    assert r.get_json()["code"] == 0
    with database.session_scope() as s:
        left = (s.query(database.DistributionChannelTask)
                .filter(database.DistributionChannelTask.draft_id == draft_id).count())
    assert left == 0
