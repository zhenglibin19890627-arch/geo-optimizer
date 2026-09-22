"""API 层回归测试（Flask test_client + 临时数据库）。

保障点：
- 不碰正式 data/geo.db（换临时库，测完还原）；
- 不发真实 AI 调用（无钥匙路径 + monkeypatch 双保险）；
- PRAGMA user_version 标到最新，跳过 run_migrations 的 VACUUM INTO，
  避免向真实 data/ 目录写备份文件。

运行：python -m pytest tests/test_api.py
"""

import os
import tempfile
import time

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.models import db as database
from geo.models.migration import MIGRATION_VERSION


@pytest.fixture(scope="module")
def app():
    """隔离 App：临时库 + 跳过迁移备份，全程不碰正式数据。"""
    tmp = tempfile.mktemp(suffix=".db")
    old_engine, old_session = database.engine, database.SessionLocal
    database.engine = create_engine(
        f"sqlite:///{tmp}", connect_args={"check_same_thread": False})
    database.SessionLocal = sessionmaker(bind=database.engine, expire_on_commit=False)
    database.init_db()
    # 表已按最新模型建好，把 user_version 标到最新，跳过迁移（含 VACUUM INTO）
    conn = database.engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA user_version = {MIGRATION_VERSION}")
        conn.commit()
    finally:
        conn.close()

    from geo.web import create_app
    flask_app = create_app()
    flask_app.testing = True
    yield flask_app

    database.engine, database.SessionLocal = old_engine, old_session
    try:
        os.remove(tmp)
    except OSError:
        pass


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------- 首页与空库引导 ----------------

def test_overview_空库给品牌引导(client):
    r = client.get("/api/overview?brand_id=1")
    body = r.get_json()
    assert body["code"] == 0
    assert body["data"]["score"] is None
    assert body["data"]["round_count"] == 0
    assert any("品牌" in h for h in body["data"]["hints"])


# ---------------- 品牌 CRUD 与校验 ----------------

def test_品牌创建与重名空名校验(client):
    r = client.post("/api/brands", json={
        "brand_name": "云澜", "product_name": "实验室改造",
        "brand_aliases": "云澜科技", "competitors": "好孩子",
        "brand_description": "做理化生实验室改造", "auto_monitor": True,
    })
    body = r.get_json()
    assert body["code"] == 0, body
    assert body["data"]["id"] == 1

    # 重名拦截
    r = client.post("/api/brands", json={"brand_name": "云澜"})
    assert r.get_json()["code"] == 1
    # 空品牌名拦截
    r = client.post("/api/brands", json={"brand_name": "  "})
    assert r.get_json()["code"] == 1


# ---------------- 问题库 CRUD 与跨品牌隔离 ----------------

def test_问题库增改查与跨品牌隔离(client):
    r = client.post("/api/questions", json={"brand_id": 1, "text": "实验室改造找谁靠谱？"})
    assert r.get_json()["code"] == 0
    qid = r.get_json()["data"]["id"]

    # 非法来源拦截
    r = client.post("/api/questions", json={"brand_id": 1, "text": "x", "source": "other"})
    assert r.get_json()["code"] == 1

    # 品牌 2 看不到品牌 1 的问题（隔离）
    client.post("/api/brands", json={"brand_name": "另一个品牌"})
    r = client.get("/api/questions?brand_id=2")
    assert r.get_json()["data"] == []
    r = client.get("/api/questions?brand_id=1")
    assert len(r.get_json()["data"]) == 1

    # 跨品牌删除拦截
    r = client.delete(f"/api/questions/{qid}", json={"brand_id": 2})
    assert r.get_json()["code"] == 1

    # 本品牌改开关
    r = client.put(f"/api/questions/{qid}", json={"brand_id": 1, "enabled": False})
    assert r.get_json()["data"]["enabled"] is False
    # 本品牌删除
    r = client.delete(f"/api/questions/{qid}", json={"brand_id": 1})
    assert r.get_json()["code"] == 0


# ---------------- 手动粘贴（零费用核心链路） ----------------

def test_手动粘贴分析提及情感信源(client):
    r = client.post("/api/monitor/paste", json={
        "brand_id": 1, "engine_code": "manual",
        "question_text": "实验室改造找谁？",
        "answer_text": "云澜很好，值得推荐，云澜科技也不错。参考 https://example.com/a",
    })
    body = r.get_json()
    assert body["code"] == 0, body
    res = body["data"]["result"]
    assert res["is_mentioned"] is True
    assert res["mention_count"] == 2
    assert res["sentiment"] == "positive"
    assert res["input_mode"] == "paste"
    assert res["round_id"] is None
    assert res["sources"][0]["domain"] == "example.com"

    # 空问题/空回答拦截
    r = client.post("/api/monitor/paste", json={"brand_id": 1, "answer_text": "x"})
    assert r.get_json()["code"] == 1
    r = client.post("/api/monitor/paste", json={"brand_id": 1, "question_text": "x"})
    assert r.get_json()["code"] == 1


def test_手动粘贴未提及好评计中性(client):
    # 防回归：夸竞品/夸行业的好评不算我方正面（情感口径）
    r = client.post("/api/monitor/paste", json={
        "brand_id": 1, "engine_code": "manual",
        "question_text": "实验室改造找谁？",
        "answer_text": "好孩子很好，值得推荐，参考 https://example.com/b"})
    body = r.get_json()
    assert body["code"] == 0, body
    res = body["data"]["result"]
    assert res["is_mentioned"] is False
    assert res["sentiment"] == "neutral"


# ---------------- 发起监测：无钥匙拦截（monkeypatch 保证零真实调用） ----------------

def test_发起监测无钥匙拦截(client, monkeypatch):
    from geo.engines.base import EngineAdapter
    monkeypatch.setattr(EngineAdapter, "is_configured", lambda self: False)

    r = client.post("/api/questions", json={"brand_id": 1, "text": "监测用问题"})
    qid = r.get_json()["data"]["id"]

    r = client.post("/api/monitor/start", json={
        "brand_id": 1, "question_ids": [qid], "engine_codes": ["deepseek"]})
    body = r.get_json()
    assert body["code"] == 1
    assert "钥匙" in body["message"]

    # 非法模式拦截
    r = client.post("/api/monitor/start", json={
        "brand_id": 1, "question_ids": [qid], "engine_codes": ["deepseek"],
        "mode": "other"})
    assert r.get_json()["code"] == 1


def test_发起监测手填型号自由填写(client, monkeypatch):
    """2026-09-04 起模型自由填写：配置里没有的型号也能创建任务
    （存在性由执行时引擎 API 判定；此处打桩，零真实调用）。"""
    from geo.core import monitor_task as mt

    class _SyncThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(mt.threading, "Thread", _SyncThread)
    monkeypatch.setattr(mt, "run_monitor_task", lambda tid: None)

    r = client.post("/api/monitor/start", json={
        "brand_id": 1, "question_ids": [1],
        "modes": ["normal", "web"],
        "models": {
            "normal": {"opencode": ["not-a-real-model"]},
            "web": {"qwen": ["not-a-real-model"]},
        }})
    body = r.get_json()
    assert body["code"] == 0, body
    assert len(body["data"]["task_ids"]) == 2

    # 清理：run_monitor_task 被打桩，任务停在 running，会卡住后续用例的发起
    from geo.models import db as database
    with database.session_scope() as s:
        s.query(database.MonitorTask).update({"status": "failed"})


def test_发起监测一轮多模式串行(client, monkeypatch):
    """2026-08-19：modes 数组 + 嵌套模型选择 → 一轮预创建常规+联网两个任务，
    单线程按序执行（不真正跑 AI：线程改同步 + run_monitor_task 打桩）。"""
    from geo.core import monitor_task as mt

    r = client.post("/api/questions", json={"brand_id": 1, "text": "多模式问题"})
    qid = r.get_json()["data"]["id"]

    executed = []

    class _SyncThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(mt.threading, "Thread", _SyncThread)
    monkeypatch.setattr(mt, "run_monitor_task", lambda tid: executed.append(tid))

    r = client.post("/api/monitor/start", json={
        "brand_id": 1, "question_ids": [qid],
        "modes": ["normal", "web"],
        "models": {
            "normal": {"deepseek": ["deepseek-v4-flash"]},
            "web": {"qwen": ["qwen3.7-max-2026-05-20"]},
        }})
    body = r.get_json()
    assert body["code"] == 0, body
    d = body["data"]
    assert d["modes"] == ["normal", "web"]
    assert len(d["task_ids"]) == 2
    assert d["total_calls"] == 2  # 1 问题 × (1 常规模型 + 1 联网模型)
    assert executed == d["task_ids"]  # 串行按序执行

    from geo.models import db as database
    with database.session_scope() as s:
        t1 = s.get(database.MonitorTask, d["task_ids"][0])
        t2 = s.get(database.MonitorTask, d["task_ids"][1])
        assert (t1.mode, t1.status) == ("normal", "pending")
        assert (t2.mode, t2.status) == ("web", "pending")
        assert database.jloads(t1.models) == {"deepseek": ["deepseek-v4-flash"]}
        assert database.jloads(t2.models) == {"qwen": ["qwen3.7-max-2026-05-20"]}

    # 联网模式一个模型都没勾 → 大白话拦截（不能静默回落全队默认档）
    r = client.post("/api/monitor/start", json={
        "brand_id": 1, "question_ids": [qid],
        "modes": ["normal", "web"],
        "models": {"normal": {"deepseek": ["deepseek-v4-flash"]}, "web": {}}})
    body = r.get_json()
    assert body["code"] == 1
    assert "联网提问" in body["message"] and "模型" in body["message"]
    # modes 空数组 / 全非法 → 拦截
    r = client.post("/api/monitor/start", json={
        "brand_id": 1, "question_ids": [qid], "modes": []})
    assert r.get_json()["code"] == 1
    r = client.post("/api/monitor/start", json={
        "brand_id": 1, "question_ids": [qid], "modes": ["nope"]})
    assert r.get_json()["code"] == 1


# ---------------- 轮次列表/详情 ----------------

def test_轮次列表为空与详情不存在(client):
    r = client.get("/api/monitor/rounds?brand_id=1")
    assert r.get_json()["code"] == 0
    assert r.get_json()["data"]["items"] == []
    r = client.get("/api/monitor/rounds/999?brand_id=1")
    assert r.get_json()["code"] == 1


# ---------------- HTTP 语义（404/405） ----------------

def test_404与405状态码(client):
    r = client.get("/api/no-such-endpoint")
    assert r.status_code == 404
    assert r.get_json()["code"] == 1
    r = client.put("/api/brands", json={"brand_name": "x"})
    assert r.status_code == 405


# ---------------- 定时设置读写与校验 ----------------

def test_定时设置读写与非法时间拦截(client):
    r = client.get("/api/schedule")
    body = r.get_json()
    assert body["code"] == 0
    assert body["data"]["enabled"] is True
    assert body["data"]["time"] == "08:30"
    assert body["data"]["plain_hint"]
    assert body["data"]["modes"] in (["normal"], ["web"])  # 旧设置兼容

    # 旧字段 web_mode 仍兼容 → modes=['web']
    r = client.put("/api/schedule", json={"time": "07:15", "enabled": False, "web_mode": True})
    assert r.get_json()["code"] == 0
    r = client.get("/api/schedule")
    d = r.get_json()["data"]
    assert d["time"] == "07:15"
    assert d["enabled"] is False
    assert d["web_mode"] is True
    assert d["modes"] == ["web"]

    # 2026-08-16：多选模式（常规+联网）；空数组/非法值拦截
    r = client.put("/api/schedule", json={"modes": ["normal", "web"]})
    assert r.get_json()["code"] == 0
    d = client.get("/api/schedule").get_json()["data"]
    assert d["modes"] == ["normal", "web"]
    assert d["web_mode"] is True
    r = client.put("/api/schedule", json={"modes": ["normal"]})
    assert r.get_json()["code"] == 0
    d = client.get("/api/schedule").get_json()["data"]
    assert d["modes"] == ["normal"]
    assert d["web_mode"] is False
    r = client.put("/api/schedule", json={"modes": []})
    assert r.get_json()["code"] == 1
    r = client.put("/api/schedule", json={"modes": ["nope"]})
    assert r.get_json()["code"] == 1

    # 2026-08-16：监测周期可调（1~30 天）
    r = client.put("/api/schedule", json={"interval_days": 7})
    assert r.get_json()["code"] == 0
    d = client.get("/api/schedule").get_json()["data"]
    assert d["interval_days"] == 7
    r = client.put("/api/schedule", json={"interval_days": 0})
    assert r.get_json()["code"] == 1
    r = client.put("/api/schedule", json={"interval_days": 99})
    assert r.get_json()["code"] == 1
    r = client.put("/api/schedule", json={"interval_days": "abc"})
    assert r.get_json()["code"] == 1
    r = client.put("/api/schedule", json={"interval_days": 1})
    assert r.get_json()["code"] == 0

    r = client.put("/api/schedule", json={"time": "25:99"})
    assert r.get_json()["code"] == 1
    r = client.put("/api/schedule", json={"time": "abc"})
    assert r.get_json()["code"] == 1


def test_定时模型档位读写与校验(client):
    # 2026-08-18：定时监测可选模型档位；初始为空 = 跟随各引擎当前档
    d = client.get("/api/schedule").get_json()["data"]
    assert d["models"] == {"normal": {}, "web": {}}

    # 从钥匙列表拿真实档位名，避免硬编码型号
    keys = client.get("/api/settings/keys").get_json()["data"]
    ds = [k for k in keys if k["engine"] == "deepseek"][0]
    names = list(dict.fromkeys(o["name"] for o in ds["model_options"]))
    picked = names[:2]

    r = client.put("/api/schedule", json={
        "models": {"normal": {"deepseek": picked}}})
    assert r.get_json()["code"] == 0
    d = client.get("/api/schedule").get_json()["data"]
    assert d["models"]["normal"]["deepseek"] == picked
    assert d["models"]["web"] == {}

    # 自由填写：配置里没有的型号也接受（2026-09-04 起不再校验档位）
    r = client.put("/api/schedule", json={
        "models": {"normal": {"deepseek": ["not-a-model"]}}})
    assert r.get_json()["code"] == 0
    d = client.get("/api/schedule").get_json()["data"]
    assert d["models"]["normal"]["deepseek"] == ["not-a-model"]

    # 勾选参加但型号留空 → 存 []（定时执行时回落该引擎当前档）
    r = client.put("/api/schedule", json={
        "models": {"normal": {"deepseek": []}}})
    assert r.get_json()["code"] == 0
    d = client.get("/api/schedule").get_json()["data"]
    assert d["models"]["normal"]["deepseek"] == []

    # 不支持联网的引擎（opencode 订阅 API 无联网工具）选联网模型拦截
    r = client.put("/api/schedule", json={
        "models": {"web": {"opencode": ["grok-4.5"]}}})
    assert r.get_json()["code"] == 1

    # 未知引擎拦截
    r = client.put("/api/schedule", json={
        "models": {"normal": {"no-such-engine": ["x"]}}})
    assert r.get_json()["code"] == 1

    # 清空恢复默认（跟随当前档）
    r = client.put("/api/schedule", json={"models": None})
    assert r.get_json()["code"] == 0
    d = client.get("/api/schedule").get_json()["data"]
    assert d["models"] == {"normal": {}, "web": {}}


# ---------------- 钥匙脱敏与费用接口 ----------------

def test_钥匙列表永不含明文(client):
    r = client.get("/api/settings/keys")
    body = r.get_json()
    assert body["code"] == 0
    assert len(body["data"]) == 7  # 5 家引擎 + analysis + create
    for item in body["data"]:
        assert "api_key" not in item  # 只有脱敏字段
        assert "api_key_masked" in item


def test_分析模型自由填写与厂商切换(client):
    """2026-09 用户需求：分析用模型自由填写（不再档位白名单拦截），
    只清洗；显式留空=清空（执行时回落厂商当前档）。"""
    # 切到 opencode 厂商
    r = client.post("/api/settings", json={"analysis_vendor": "opencode"})
    assert r.get_json()["code"] == 0
    r = client.get("/api/settings/keys")
    item = [x for x in r.get_json()["data"] if x["engine"] == "analysis"][0]
    assert item["vendor"] == "opencode"
    assert len(item["vendors"]) == 5
    assert item["model_options"]

    # 非法厂商拦截
    r = client.post("/api/settings", json={"analysis_vendor": "no-such"})
    assert r.get_json()["code"] == 1

    # 自由填写：配置里没有的型号也接受（型号存在性由执行时引擎 API 报错）
    r = client.post("/api/settings", json={"analysis_model": "not-a-real-model"})
    assert r.get_json()["code"] == 0
    item = [x for x in client.get("/api/settings/keys").get_json()["data"]
            if x["engine"] == "analysis"][0]
    assert item["model"] == "not-a-real-model"

    # 空白清洗：首尾空格与空项去掉后保存
    r = client.post("/api/settings", json={"analysis_model": "  a-model ， ，b-model ，"})
    assert r.get_json()["code"] == 0
    item = [x for x in client.get("/api/settings/keys").get_json()["data"]
            if x["engine"] == "analysis"][0]
    assert item["model"] == "a-model,b-model"

    # 显式留空 = 清空（回显空串；current_model 仍给出回落当前档）
    r = client.post("/api/settings", json={"analysis_model": "   "})
    assert r.get_json()["code"] == 0
    item = [x for x in client.get("/api/settings/keys").get_json()["data"]
            if x["engine"] == "analysis"][0]
    assert item["model"] == ""
    assert item["current_model"]

    # 不带 analysis_model 键的保存（只切厂商）不动型号
    r = client.post("/api/settings", json={"analysis_vendor": "deepseek"})
    assert r.get_json()["code"] == 0
    item = [x for x in client.get("/api/settings/keys").get_json()["data"]
            if x["engine"] == "analysis"][0]
    assert item["model"] == ""


def test_创作模型自由填写与清空(client):
    """内容创作模型自由填写：填了生效（清洗后保存）、留空清空回落分析模型。"""
    r = client.post("/api/settings", json={"create_model": "  create-x "})
    assert r.get_json()["code"] == 0
    item = [x for x in client.get("/api/settings/keys").get_json()["data"]
            if x["engine"] == "create"][0]
    assert item["model"] == "create-x"
    assert item["current_model"] == "create-x"

    # 留空清空：current_model 回落到分析模型链（deepseek 默认档）
    r = client.post("/api/settings", json={"create_model": ""})
    assert r.get_json()["code"] == 0
    item = [x for x in client.get("/api/settings/keys").get_json()["data"]
            if x["engine"] == "create"][0]
    assert item["model"] == ""
    assert item["current_model"] == "deepseek-v4-flash"


def test_费用接口空库为0(client):
    r = client.get("/api/settings/cost")
    body = r.get_json()
    assert body["code"] == 0
    assert body["data"]["month_cost_yuan"] == 0


def test_各引擎厂商对比接口(client):
    """2026-08-22：/api/report/engines 聚合近30轮每引擎/每模型提及数据。"""
    from geo.models import db as database

    # 独立品牌（id=7），避免污染同库其他用例对品牌 1 的统计
    with database.session_scope() as s:
        if not s.get(database.BrandProfile, 7):
            s.add(database.BrandProfile(id=7, brand_name="对比测试牌", auto_monitor=True))
    # 造一轮正常完成的数据：豆包提及、千问未提及、opencode 提及（订阅网关不进厂商对比）
    with database.session_scope() as s:
        s.add(database.MonitorTask(id=501, type="manual", status="done",
                                   mode="normal", brand_id=7, done_calls=3, total_calls=3))
        s.flush()
        s.add(database.MonitorRound(id=501, task_id=501, brand_id=7, mode="normal",
                                    mention_rate=0.5, net_sentiment=0.0, overall_score=50))
        s.add(database.MonitorResult(round_id=501, brand_id=7, engine_code="doubao",
                                     model="doubao-mini", question_id=1, question_text="q",
                                     answer_text="云澜不错", is_mentioned=True, mention_count=1,
                                     mention_position=1, sentiment="positive", sources="[]"))
        s.add(database.MonitorResult(round_id=501, brand_id=7, engine_code="qwen",
                                     model="qwen-max", question_id=1, question_text="q",
                                     answer_text="没有品牌", is_mentioned=False, mention_count=0,
                                     sentiment="neutral", sources="[]"))
        s.add(database.MonitorResult(round_id=501, brand_id=7, engine_code="opencode",
                                     model="qwen3.7-max", question_id=1, question_text="q",
                                     answer_text="云澜很好", is_mentioned=True, mention_count=1,
                                     mention_position=1, sentiment="positive", sources="[]"))

    r = client.get("/api/report/engines?rounds=30&brand_id=7")
    body = r.get_json()
    assert body["code"] == 0
    d = body["data"]
    assert d["rounds_used"] == 1
    by_engine = {e["engine"]: e for e in d["engines"]}
    # opencode 是订阅网关不是模型厂家：数据照常落库，但不进厂商对比
    assert set(by_engine) == {"doubao", "qwen"}
    ds = by_engine["doubao"]
    assert ds["answered"] == 1 and ds["mentioned"] == 1
    assert ds["mention_rate"] == 1.0
    assert ds["net_sentiment"] == 1.0
    assert ds["avg_position"] == 1
    assert ds["modes"]["normal"]["answered"] == 1
    assert ds["models"][0]["model"] == "doubao-mini"
    # 提及率降序：豆包在前
    assert d["engines"][0]["engine"] == "doubao"

    # 空库（其他品牌）→ 空列表不报错
    r = client.get("/api/report/engines?rounds=30&brand_id=9")
    d = r.get_json()["data"]
    assert d["engines"] == [] and d["rounds_used"] == 0


# ---------------- 内容优化入口校验 + 无钥匙失败落库（异步轮询） ----------------

def test_优化入口校验与无钥匙落库(client, monkeypatch):
    monkeypatch.setattr("geo.analyzers.llm_client.is_configured", lambda: False)

    r = client.post("/api/optimize", json={"brand_id": 1, "type": "text", "content": "太短"})
    assert r.get_json()["code"] == 1

    r = client.post("/api/optimize", json={
        "brand_id": 1, "type": "text", "content": "这是一段足够长的内容，用于测试优化分析的入口。"})
    body = r.get_json()
    assert body["code"] == 0, body
    rid = body["data"]["record_id"]

    # 后台线程无钥匙 → 落库 failed + 大白话原因
    status = None
    for _ in range(50):
        r = client.get(f"/api/optimize/{rid}?brand_id=1")
        status = r.get_json()["data"]["status"]
        if status in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status == "failed"
    assert "钥匙" in r.get_json()["data"]["error_msg"]


# ---------------- 报告接口（趋势/信源/竞品对比） ----------------

def test_报告接口趋势信源竞品(client):
    from datetime import datetime
    with database.session_scope() as s:
        # 用例自包含：不依赖前序用例创建品牌（单独运行 -k 也能过）
        if not s.get(database.BrandProfile, 1):
            s.add(database.BrandProfile(
                id=1, brand_name="云澜", product_name="实验室改造",
                brand_aliases="[]", brand_description="",
                competitors=database.jdumps(["好孩子"])))
        task = database.MonitorTask(
            type="manual", status="done", brand_id=1, mode="normal",
            progress=100, total_calls=2, done_calls=2, finished_at=datetime.now())
        s.add(task)
        s.flush()
        round_row = database.MonitorRound(
            task_id=task.id, brand_id=1, mode="normal", mention_rate=0.5,
            net_sentiment=0.3, overall_score=70,
            summary=database.jdumps({"total_answers": 2, "mentioned_answers": 1}),
            auto_competitors=database.jdumps(["好孩子"]),
            finished_at=datetime.now())
        s.add(round_row)
        s.flush()
        rid = round_row.id
        s.add(database.MonitorResult(
            round_id=rid, brand_id=1, engine_code="deepseek",
            model="deepseek-v4-flash", question_id=1,
            question_text="监测用问题",
            answer_text="好孩子很好，云澜也不错，参考 https://example.com/a",
            is_mentioned=True, mention_count=1, mention_position=2,
            sentiment="positive",
            sources=database.jdumps(
                [{"title": "a", "url": "https://example.com/a",
                  "domain": "example.com"}]),
            competitor_mentions=database.jdumps(
                [{"name": "好孩子", "count": 1, "position": 1}]),
            input_mode="auto"))
        s.add(database.MonitorResult(
            round_id=rid, brand_id=1, engine_code="qwen", model="qwen3.7-max-2026-05-20",
            question_id=1,
            question_text="监测用问题", answer_text="这个问题没有提到品牌",
            is_mentioned=False, mention_count=0, sentiment="neutral",
            sources="[]", competitor_mentions="[]", input_mode="auto"))
        # 评分快照（首页 score / score_trend 只读快照，轮次上的 overall_score 不参与）
        s.add(database.ScoreSnapshot(
            round_id=rid, brand_id=1, score=70,
            breakdown=database.jdumps({"total": 70})))

    # 首页：最新评分与最近一轮摘要
    r = client.get("/api/overview?brand_id=1")
    d = r.get_json()["data"]
    assert d["score"] == 70
    assert d["round_count"] == 1
    assert d["last_round"]["total_answers"] == 2
    assert d["last_round"]["mentioned_answers"] == 1

    # 趋势
    r = client.get("/api/report/trend?brand_id=1&metric=score")
    d = r.get_json()["data"]
    assert d["labels"] == ["第1轮"]
    assert d["values"] == [70]

    # 信源排行
    r = client.get("/api/report/sources?brand_id=1")
    items = r.get_json()["data"]
    assert items[0]["domain"] == "example.com"
    assert items[0]["count"] == 1
    assert items[0]["engines"][0]["engine_code"] == "deepseek"

    # 竞品对比（自己 + 本轮自动提取竞品）
    r = client.get(f"/api/report/competitor?brand_id=1&round_id={rid}")
    items = r.get_json()["data"]["items"]
    by_name = {it["name"]: it for it in items}
    assert by_name["云澜"]["is_self"] is True
    assert by_name["云澜"]["mention_count"] == 1
    assert by_name["好孩子"]["is_self"] is False
    assert by_name["好孩子"]["mention_count"] == 1

    # 轮次详情：结果带实际调用模型名
    r = client.get(f"/api/monitor/rounds/{rid}?brand_id=1")
    results = r.get_json()["data"]["results"]
    models = sorted(set(x["model"] for x in results))
    assert models == ["deepseek-v4-flash", "qwen3.7-max-2026-05-20"]


# ---------------- 预警列表 ----------------

def test_预警列表为空(client):
    r = client.get("/api/alerts?brand_id=1")
    body = r.get_json()
    assert body["code"] == 0
    assert body["data"]["unread_count"] == 0
    assert body["data"]["items"] == []


# ---------------- 写操作品牌归属强制（2026-09 评审 R8） ----------------

def test_写操作缺brand_id被拦截不再静默写进品牌1(client):
    """R8 回归：写方法（POST/PUT/DELETE）缺 brand_id 时大白话报错，
    不能像从前那样悄悄把数据写进品牌 1。"""
    r = client.post("/api/questions", json={"text": "没带品牌的问题"})
    body = r.get_json()
    assert body["code"] == 1
    assert "品牌" in body["message"]
    # 确认没有静默写进品牌 1
    items = client.get("/api/questions?brand_id=1").get_json()["data"]
    assert "没带品牌的问题" not in [x["text"] for x in items]

    # brand_id 格式非法同样拦截（不再回落品牌 1）
    r = client.post("/api/questions", json={"brand_id": "abc", "text": "坏编号的问题"})
    assert r.get_json()["code"] == 1
    assert "品牌" in r.get_json()["message"]


def test_读操作缺brand_id仍按缺省品牌1(client):
    """读路径保留缺省 1 的旧口径（有归属校验兜底，不会写坏数据）。"""
    r = client.get("/api/questions")
    assert r.get_json()["code"] == 0


def test_知识库上传缺brand_id被拦截(client):
    """R8 口径统一：前端 distribution.js 上传已给 FormData 补 brand_id
    （与 geoApi 同一品牌上下文），后端收回缺 brand_id 落品牌 1 的存量例外——
    缺 brand_id 大白话报错，不写任何数据。"""
    import io
    data = {"file": (io.BytesIO("没带品牌的上传内容".encode("utf-8")),
                     "无品牌上传.md")}
    r = client.post("/api/knowledge/docs/upload", data=data,
                    content_type="multipart/form-data")
    body = r.get_json()
    assert body["code"] == 1
    assert "品牌" in body["message"]
    # 确认没有静默写进品牌 1
    docs = client.get("/api/knowledge/docs?brand_id=1").get_json()["data"]["docs"]
    assert all(d["title"] != "无品牌上传" for d in docs)


def test_知识库上传带brand_id落对应品牌(client):
    """FormData 带 brand_id 时落到对应品牌，不串到别的品牌。"""
    import io
    r = client.post("/api/brands", json={"brand_name": "知识库上传第二品牌"})
    assert r.get_json()["code"] == 0, r.get_json()
    bid = r.get_json()["data"]["id"]
    data = {"file": (io.BytesIO("第二品牌的知识库上传内容".encode("utf-8")),
                     "归属测试.md"),
            "brand_id": str(bid)}
    r = client.post("/api/knowledge/docs/upload", data=data,
                    content_type="multipart/form-data")
    body = r.get_json()
    assert body["code"] == 0, body
    doc_id = body["data"]["doc_id"]
    docs_b = client.get(f"/api/knowledge/docs?brand_id={bid}").get_json()["data"]["docs"]
    assert any(d["id"] == doc_id for d in docs_b)
    docs_1 = client.get("/api/knowledge/docs?brand_id=1").get_json()["data"]["docs"]
    assert all(d["id"] != doc_id for d in docs_1)
