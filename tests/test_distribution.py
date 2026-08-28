"""内容分发闭环回归测试（临时库隔离，零真实 AI/网络调用）。

覆盖：
- 缺口快照（纯规则法）：信源缺口 / 弱势问题 / 已布点域名；
- 创作简报：无钥匙降级规则简报，有钥匙走 LLM（monkeypatch）；
- 生成稿件（LLM 打桩）→ 审阅编辑 → 官网发布成功/失败（requests 打桩）；
- 报告页信源排行「已布点」标注。
"""

import os
import tempfile
from datetime import datetime

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.models import db as database
from geo.models.migration import MIGRATION_VERSION
from geo.analyzers import llm_client
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


def _seed_monitor_data(brand_id=21):
    """造 2 轮正常监测：zhihu.com 高频信源、一个提及率 0% 的短板问题。"""
    with database.session_scope() as s:
        if not s.get(database.BrandProfile, brand_id):
            s.add(database.BrandProfile(id=brand_id, brand_name="闭环测试牌",
                                        product_name="智能网关",
                                        brand_description="工业物联网设备厂商",
                                        auto_monitor=True))
        for rnd in (601, 602):
            s.add(database.MonitorTask(id=rnd, type="manual", status="done",
                                       mode="web", brand_id=brand_id,
                                       done_calls=2, total_calls=2))
            s.flush()
            s.add(database.MonitorRound(id=rnd, task_id=rnd, brand_id=brand_id,
                                        mode="web", mention_rate=0.5,
                                        net_sentiment=0.0, overall_score=50))
            for engine, ans, mentioned in (
                    ("deepseek", "参考知乎 zhiliao 介绍 智能网关", True),
                    ("qwen", "不知道 智能网关 是什么", False)):
                s.add(database.MonitorResult(
                    round_id=rnd, brand_id=brand_id, engine_code=engine,
                    model="m", question_id=1, question_text="工控场景怎么选智能网关",
                    answer_text=ans, is_mentioned=mentioned, mention_count=1,
                    sentiment="neutral",
                    sources=database.jdumps([{"title": "知乎专栏", "domain": "zhihu.com",
                                              "url": "https://zhuanlan.zhihu.com/x"}])))


# ---------------- 缺口快照（纯规则法） ----------------

def test_缺口快照_信源与短板(tmpdb):
    _seed_monitor_data()
    snap = distribution.build_gap_snapshot(21)
    assert snap["rounds_used"] == 2
    assert any(d["domain"] == "zhihu.com" for d in snap["top_domains"])
    # 我方没发布过内容：zhihu.com 就是缺口域名
    assert any(d["domain"] == "zhihu.com" for d in snap["missing_domains"])
    # 官网域名默认已布点
    assert distribution.official_domain() in snap["deployed_domains"]
    assert snap["weak_questions"][0]["mention_rate"] == 0.5


def test_已布点域名含已发布稿件(tmpdb):
    with database.session_scope() as s:
        s.add(database.DistributionDraft(
            brand_id=21, title="t", body_md="b", status="published",
            published_url="https://blog.csdn.net/xxx/article/1"))
        s.add(database.DistributionDraft(
            brand_id=21, title="t2", body_md="b2", status="draft"))
    domains = distribution.deployed_domains(21)
    assert "csdn.net" in domains          # 已发布稿件
    assert "zhihu.com" not in domains     # 未发布的不算


# ---------------- 创作简报 ----------------

def test_无钥匙降级规则简报(tmpdb, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: False)
    brief = distribution.build_brief(21)
    assert brief["mode"] == "rule"
    assert brief["topics"], "规则简报也要有主题"


def test_有钥匙走LLM简报(tmpdb, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k:
                        '{"summary":"补短板","topics":[{"title":"工控网关选型指南",'
                        '"angle":"覆盖知乎类技术社区","distillation_words":["闭环测试牌","智能网关"],'
                        '"target_domains":["zhihu.com"]}]}')
    brief = distribution.build_brief(21)
    assert brief["mode"] == "llm"
    assert brief["topics"][0]["title"] == "工控网关选型指南"


def test_LLM返回坏JSON时降级规则简报(tmpdb, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k: "模型抽风输出")
    brief = distribution.build_brief(21)
    assert brief["mode"] == "rule"


# ---------------- 生成 / 审阅 / 发布 ----------------

def test_生成稿件并审阅编辑(tmpdb, client, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k:
                        '{"title":"闭环测试牌智能网关应用指南","body":"## 背景\\n正文用了“中文引号”。",'
                        '"summary":"一篇客观介绍","tags":"物联网,智能网关"}')
    r = client.post("/api/distribution/generate", json={"brand_id": 21,
                                                        "user_instruction": "口吻客观"})
    body = r.get_json()
    assert body["code"] == 0, body
    draft = body["data"]
    assert draft["status"] == "draft"
    assert "中文引号" in draft["body_md"]

    # 人工审阅编辑
    r = client.put(f"/api/distribution/drafts/{draft['id']}", json={
        "brand_id": 21, "title": "改后的标题", "body_md": "## 改后正文",
        "summary": "改后摘要", "tags": "改"})
    assert r.get_json()["code"] == 0
    r = client.get(f"/api/distribution/drafts/{draft['id']}", query_string={"brand_id": 21})
    assert r.get_json()["data"]["title"] == "改后的标题"


def test_发布官网成功与失败路径(tmpdb, client, monkeypatch):
    class _Resp:
        def __init__(self, code=200, payload=None, text=""):
            self.status_code = code
            self._payload = payload if payload is not None else {"code": 0, "url": "https://x/a"}
            self.text = text

        def json(self):
            return self._payload

    # 未配 Token → 大白话报错
    monkeypatch.setattr(database, "get_setting",
                        lambda key, default=None: "" if key == "official_api_token" else default)
    with database.session_scope() as s:
        s.add(database.DistributionDraft(id=610, brand_id=21, title="t", body_md="b"))
    r = client.post("/api/distribution/drafts/610/publish", json={"brand_id": 21})
    assert r.get_json()["code"] == 1
    assert "Token" in r.get_json()["message"]

    # 配好 Token + 打桩 requests.post：成功路径
    monkeypatch.setattr(database, "get_setting",
                        lambda key, default=None:
                        "tok123" if key == "official_api_token" else default)
    posted = {}

    def _fake_post(url, json=None, timeout=0, headers=None, **kw):
        posted["url"] = url
        posted["payload"] = json
        return _Resp(payload={"code": 0, "url": "https://www.celadonhorizon.com/art/1"})

    monkeypatch.setattr("geo.core.distribution.requests_lib.post", _fake_post)
    r = client.post("/api/distribution/drafts/610/publish", json={"brand_id": 21})
    body = r.get_json()
    assert body["code"] == 0, body
    assert body["data"]["url"] == "https://www.celadonhorizon.com/art/1"
    assert "celadonhorizon.com" in posted["url"]
    assert posted["payload"]["author"] == "闭环测试牌"
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, 610)
        assert row.status == "published"
        assert row.published_url == "https://www.celadonhorizon.com/art/1"

    # 失败路径：官网返回业务错误码 → 稿件 failed + error_msg 可重试
    monkeypatch.setattr("geo.core.distribution.requests_lib.post",
                        lambda *a, **kw: _Resp(payload={"code": 1, "message": "token 无效"}))
    r = client.post("/api/distribution/drafts/610/publish", json={"brand_id": 21})
    assert r.get_json()["code"] == 1
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, 610)
        assert row.status == "failed"
        assert "token 无效" in row.error_msg

    # 已布点域名随之更新
    assert "celadonhorizon.com" in distribution.deployed_domains(21)


def test_信源排行已布点标注(tmpdb, client):
    # 发布一篇落在知乎域名下的稿件 → 信源排行的 zhihu.com 应标为已布点
    with database.session_scope() as s:
        s.add(database.DistributionDraft(
            brand_id=21, title="布点稿", body_md="b", status="published",
            published_url="https://zhuanlan.zhihu.com/p/1"))
    r = client.get("/api/report/sources?brand_id=21")
    items = r.get_json()["data"]
    by_domain = {i["domain"]: i for i in items}
    assert "zhihu.com" in by_domain
    assert by_domain["zhihu.com"]["deployed"] is True
