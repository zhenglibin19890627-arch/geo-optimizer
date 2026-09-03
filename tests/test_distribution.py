"""内容分发闭环回归测试（临时库隔离，零真实 AI/网络调用）。

覆盖：
- 缺口快照（纯规则法）：信源缺口 / 弱势问题 / 已布点域名；
- 创作简报：无钥匙降级规则简报，有钥匙走 LLM（monkeypatch）；
- 生成稿件（LLM 打桩）→ 审阅编辑 → 官网发布成功/失败（requests 打桩）；
- 报告页信源排行「已布点」标注。
"""

import json
import os
import tempfile
from datetime import datetime

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.models import db as database
from geo.models.migration import MIGRATION_VERSION
from geo.analyzers import content_advice, llm_client
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
    database.set_setting("site_base_url", "https://www.example-brand.com")
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


# ---------------- 站点发布配置（通用，无预置站点） ----------------

def test_站点配置保存与回显(tmpdb, client):
    r = client.post("/api/distribution/config", json={
        "brand_id": 21, "base_url": "https://cfg.example.com",
        "publish_path": "api/posts/publish",  # 故意不带前导斜杠
        "category": "技术动态", "author": "内容组", "token": "cfg-tok"})
    body = r.get_json()
    assert body["code"] == 0, body
    assert body["data"]["publish_path"] == "/api/posts/publish"  # 自动补斜杠
    assert body["data"]["configured"] is True
    assert body["data"]["token_masked"]  # 掩码非空
    # 回读：官网配置按品牌独立存储，用 GET 接口带上同一品牌
    cfg = client.get("/api/distribution/config?brand_id=21").get_json()["data"]
    assert cfg["base_url"] == "https://cfg.example.com"
    assert cfg["category"] == "技术动态"
    assert cfg["author"] == "内容组"
    assert cfg["token_masked"]

    # 分类超长 → 大白话报错
    r = client.post("/api/distribution/config", json={
        "brand_id": 21, "category": "超" * 51})
    assert r.get_json()["code"] == 1


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

    # 什么都没配 → 先提示接口地址（通用配置：无预置站点）
    monkeypatch.setattr(database, "get_setting", lambda key, default=None: default)
    with database.session_scope() as s:
        s.add(database.DistributionDraft(id=610, brand_id=21, title="t", body_md="b"))
    r = client.post("/api/distribution/drafts/610/publish", json={"brand_id": 21})
    assert r.get_json()["code"] == 1
    assert "地址" in r.get_json()["message"]

    # 配好站点 + 打桩 requests.post：成功路径
    def _fake_setting(key, default=None):
        if key in ("site_base_url", "official_api_base"):
            return "https://publish.example.com"
        if key in ("site_api_token", "official_api_token"):
            return "tok123"
        return default

    monkeypatch.setattr(database, "get_setting", _fake_setting)
    posted = {}

    def _fake_post(url, json=None, timeout=0, headers=None, **kw):
        posted["url"] = url
        posted["payload"] = json
        return _Resp(payload={"code": 0, "url": "https://www.publish-example.com/art/1"})

    monkeypatch.setattr("geo.core.distribution.requests_lib.post", _fake_post)
    r = client.post("/api/distribution/drafts/610/publish", json={"brand_id": 21})
    body = r.get_json()
    assert body["code"] == 0, body
    assert body["data"]["url"] == "https://www.publish-example.com/art/1"
    assert "publish.example.com" in posted["url"]
    assert posted["payload"]["author"] == "闭环测试牌"  # 未配作者 → 回落品牌名
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, 610)
        assert row.status == "published"
        assert row.published_url == "https://www.publish-example.com/art/1"

    # 失败路径：官网返回业务错误码 → 稿件 failed + error_msg 可重试
    monkeypatch.setattr("geo.core.distribution.requests_lib.post",
                        lambda *a, **kw: _Resp(payload={"code": 1, "message": "token 无效"}))
    r = client.post("/api/distribution/drafts/610/publish", json={"brand_id": 21})
    assert r.get_json()["code"] == 1
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, 610)
        assert row.status == "failed"
        assert "token 无效" in row.error_msg

    # 已布点域名随之更新：官网主域（来自站点配置）天然算已布点；
    # 此刻稿件已被上面的失败子流程标回 failed，其落地页域名不再计入
    assert "publish.example.com" in distribution.deployed_domains(21)
    assert "publish-example.com" not in distribution.deployed_domains(21)


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


# ---------------- 一键 GEO 优化（打完分后的针对性重写） ----------------

def test_一键优化返回改写与前后分(tmpdb, client, monkeypatch):
    with database.session_scope() as s:
        row = database.DistributionDraft(brand_id=21, title="待优化标题",
                                         body_md="很短。", status="draft")
        s.add(row)
        s.flush()
        did = row.id

    # 模型返回的改写稿：长度/品牌名/结构/外链/联系方式/FAQ 各评分项全部命中
    new_body = ("# 智能网关选购指南\n\n" + "闭环测试牌 智能网关 表现稳定。\n\n" * 200
                + "常见问题（FAQ）\n\n如何选型？看接口与协议。\n\n结论：按场景选型。\n\n"
                "参考 https://example.com/guide\n\n联系方式：400-000-0000")
    payload = json.dumps({"title": "优化后的标题", "body_md": new_body,
                          "changes": [{"title": "补充 FAQ 与结论",
                                       "detail": "增加结构化小节"}]},
                         ensure_ascii=False)
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k: payload)
    r = client.post(f"/api/distribution/drafts/{did}/optimize", json={"brand_id": 21})
    body = r.get_json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["title"] == "优化后的标题"
    assert "选购指南" in data["body_md"]
    assert len(data["changes"]) == 1
    assert data["score_before"]["score"] == 0          # 原稿啥评分项都不命中
    assert data["score_after"]["score"] >= 70          # 改写稿各维度命中
    assert data["score_after"]["score"] > data["score_before"]["score"]


def test_一键优化_模型坏输出报大白话错误(tmpdb, client, monkeypatch):
    with database.session_scope() as s:
        row = database.DistributionDraft(brand_id=21, title="t", body_md="b",
                                         status="draft")
        s.add(row)
        s.flush()
        did = row.id
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k: "模型抽风输出没有JSON")
    r = client.post(f"/api/distribution/drafts/{did}/optimize", json={"brand_id": 21})
    body = r.get_json()
    assert body["code"] == 1
    assert "优化失败" in body["message"]


def test_一键优化_空正文拦截(tmpdb, client, monkeypatch):
    with database.session_scope() as s:
        row = database.DistributionDraft(brand_id=21, title="只有标题", body_md="",
                                         status="draft")
        s.add(row)
        s.flush()
        did = row.id
    monkeypatch.setattr(llm_client, "chat",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该调用模型")))
    r = client.post(f"/api/distribution/drafts/{did}/optimize", json={"brand_id": 21})
    body = r.get_json()
    assert body["code"] == 1
    assert "正文" in body["message"]


def test_优化函数解析围栏JSON(tmpdb, monkeypatch):
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k:
                        '```json\n{"title":"围栏标题","body_md":"围栏正文",'
                        '"changes":[{"title":"改动","detail":"说明"}]}\n```')
    out = content_advice.apply_geo_optimization("旧标题", "旧正文",
                                                {"brand_name": "闭环测试牌"}, [])
    assert out["title"] == "围栏标题"
    assert out["body_md"] == "围栏正文"
    assert out["changes"][0]["title"] == "改动"


# ---------------- 外链口径随目标平台切换 ----------------

def test_外链政策覆盖全部平台(tmpdb):
    for pid in distribution.SUPPORTED_PLATFORMS:
        assert pid in distribution.PLATFORM_LINK_POLICY


def test_评分外链口径切换(tmpdb):
    brand, kws = "闭环测试牌", []
    with_link = "正文内容参考 https://example.com/a 汇总而成。"
    # 默认口径：有外链加分
    assert content_advice.geo_score(with_link, brand, kws)["score"] == 10
    # 不允许外链：有外链 0 分 + 提示移除
    r = content_advice.geo_score(with_link, brand, kws, allow_links=False)
    item = [p for p in r["breakdown"] if "外链" in p["title"]][0]
    assert item["score"] == 0 and "移除" in item["title"]
    # 不允许外链：纯文字正文得该项 10 分
    r2 = content_advice.geo_score("来源：某某百科，纯文字表述。", brand, kws,
                                  allow_links=False)
    item2 = [p for p in r2["breakdown"] if "无外部链接" in p["title"]]
    assert item2 and item2[0]["score"] == 10


def test_一键优化_外链口径传入提示词与评分(tmpdb, client, monkeypatch):
    with database.session_scope() as s:
        row = database.DistributionDraft(brand_id=21, title="t",
                                         body_md="带链接 https://example.com/x 的正文。",
                                         status="draft")
        s.add(row)
        s.flush()
        did = row.id
    seen = {}

    def fake_chat(prompt, **kw):
        seen["prompt"] = prompt
        return json.dumps({"title": "t2", "body_md": "纯文字正文，无任何链接。",
                           "changes": []}, ensure_ascii=False)

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    r = client.post(f"/api/distribution/drafts/{did}/optimize",
                    json={"brand_id": 21, "allow_links": False})
    body = r.get_json()
    assert body["code"] == 0, body
    # 提示词明确告知平台不允许外链
    assert "不允许外部链接" in seen["prompt"]
    # 前后评分都用无外链口径：改前含链接该项 0 分
    before_items = [p for p in body["data"]["score_before"]["breakdown"]
                    if "外链" in p["title"]]
    assert before_items and before_items[0]["score"] == 0
    after_items = [p for p in body["data"]["score_after"]["breakdown"]
                   if "无外部链接" in p["title"]]
    assert after_items and after_items[0]["score"] == 10


def test_advice接口接受外链口径参数(tmpdb, client, monkeypatch):
    with database.session_scope() as s:
        row = database.DistributionDraft(brand_id=21, title="t",
                                         body_md="正文有 https://example.com 链接。",
                                         status="draft")
        s.add(row)
        s.flush()
        did = row.id
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k: "[]")
    r = client.post(f"/api/distribution/drafts/{did}/advice",
                    json={"brand_id": 21, "allow_links": False})
    body = r.get_json()
    assert body["code"] == 0, body
    items = [p for p in body["data"]["score"]["breakdown"] if "外链" in p["title"]]
    assert items and items[0]["score"] == 0
