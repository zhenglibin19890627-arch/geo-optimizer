"""竞品自动提取 + 深度分析回归测试（临时库隔离，零真实 AI 调用）。

覆盖：
- 名称清洗（LLM 输出的如“XX公司”包装杂质）；
- trigger_if_due 规则法现算提及（不依赖落库 competitor_mentions，旧轮次也能触发）；
- 报告页分析状态接口按需触发 / 无提及返回 none；
- 深度分析聚焦被提到最多的前 8 家（truncated 标记）。
"""

import os
import tempfile
from urllib.parse import quote

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.models import db as database
from geo.models.migration import MIGRATION_VERSION
from geo.analyzers import competitor_analysis, llm_client


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


def _seed(tmpdb, auto_names, answers, comps_by_answer=None, brand_id=1,
          brand_name="威启"):
    """建品牌 + 轮次 + 回答（competitor_mentions 默认空，模拟旧轮次未回算）；返回轮次 id。"""
    with database.session_scope() as s:
        if not s.get(database.BrandProfile, brand_id):
            s.add(database.BrandProfile(
                id=brand_id, brand_name=brand_name, product_name="实验室改造",
                brand_aliases=database.jdumps(["威启科技"]),
                brand_description="", competitors="[]", auto_monitor=True))
        rnd = database.MonitorRound(
            brand_id=brand_id, mode="normal", mention_rate=0.5,
            net_sentiment=0.0, overall_score=70, summary="{}",
            auto_competitors=database.jdumps(auto_names))
        s.add(rnd)
        s.flush()
        rid = rnd.id
        for i, a in enumerate(answers, 1):
            comps = (comps_by_answer or {}).get(i - 1) or []
            s.add(database.MonitorResult(
                round_id=rid, brand_id=brand_id, engine_code="deepseek",
                model="m", question_id=1, question_text="监测用问题",
                answer_text=a, is_mentioned=False, mention_count=0,
                sentiment="neutral", sources="[]",
                competitor_mentions=database.jdumps(comps), input_mode="auto"))
    return rid


# ---------------- 名称清洗 ----------------

def test_strip_name_wraps():
    f = competitor_analysis._strip_name_wraps
    assert f("如“龙泉市XX网络科技有限公司”") == "龙泉市XX网络科技有限公司"
    assert f("例如「浙江威启」") == "浙江威启"
    assert f("比如《某某品牌》）") == "某某品牌"
    # 不带引号的品牌名不受影响（如家酒店不能丢掉“如”）
    assert f("如家酒店") == "如家酒店"
    assert f("  好孩子  ") == "好孩子"
    assert f("") == ""


def test_clean_brands_dedup_and_wrap():
    out = competitor_analysis._clean_brands(
        ["如“XX网络科技有限公司”", "XX网络科技有限公司", "好孩子", "威启科技"], ["威启"])
    assert out == ["XX网络科技有限公司", "好孩子"]


# ---------------- 触发条件：规则法现算提及 ----------------

def test_trigger_rule_based_mention(tmpdb, monkeypatch):
    """旧轮次 competitor_mentions 为空，但回答文本提到竞品 → 仍能触发。"""
    monkeypatch.setattr(llm_client, "is_configured", lambda: False)
    rid = _seed(tmpdb, ["好孩子"], ["好孩子很好，值得推荐"])
    competitor_analysis.trigger_if_due(rid, 1)
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == rid).first())
        assert row is not None
        assert row.status == competitor_analysis.STATUS_UNAVAILABLE


def test_trigger_no_mention_no_row(tmpdb, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: False)
    rid = _seed(tmpdb, ["好孩子"], ["这轮回答没有提到任何品牌"])
    competitor_analysis.trigger_if_due(rid, 1)
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == rid).first())
        assert row is None


def test_trigger_no_competitors_no_row(tmpdb, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: False)
    rid = _seed(tmpdb, [], ["好孩子很好，值得推荐"])
    competitor_analysis.trigger_if_due(rid, 1)
    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == rid).first())
        assert row is None


# ---------------- 报告页分析状态接口：按需触发 ----------------

def test_analysis_status_on_demand(client, tmpdb, monkeypatch):
    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    monkeypatch.setattr(competitor_analysis, "generate",
                        lambda *a, **k: None)  # 线程只跑空实现，零真实调用
    rid1 = _seed(tmpdb, ["好孩子"], ["好孩子很好，值得推荐"])
    rid2 = _seed(tmpdb, [], ["这轮没有提到其他品牌"])

    r = client.get(f"/api/report/competitor/analysis?brand_id=1&round_id={rid1}")
    d = r.get_json()["data"]
    assert d["status"] == competitor_analysis.STATUS_PENDING

    # 无竞品提及的轮次：none，而不是误导的“没填钥匙”
    r = client.get(f"/api/report/competitor/analysis?brand_id=1&round_id={rid2}")
    d = r.get_json()["data"]
    assert d["status"] == "none"


def test_analysis_status_unavailable_retry(client, tmpdb, monkeypatch):
    """当时没钥匙记了 unavailable；补填钥匙后再看报告 → 删行重新触发。"""
    monkeypatch.setattr(competitor_analysis, "generate",
                        lambda *a, **k: None)
    rid = _seed(tmpdb, ["好孩子"], ["好孩子很好，值得推荐"])
    with database.session_scope() as s:
        s.add(database.CompetitorAnalysis(
            round_id=rid, brand_id=1,
            status=competitor_analysis.STATUS_UNAVAILABLE))

    monkeypatch.setattr(llm_client, "is_configured", lambda: False)
    r = client.get(f"/api/report/competitor/analysis?brand_id=1&round_id={rid}")
    assert r.get_json()["data"]["status"] == competitor_analysis.STATUS_UNAVAILABLE

    monkeypatch.setattr(llm_client, "is_configured", lambda: True)
    r = client.get(f"/api/report/competitor/analysis?brand_id=1&round_id={rid}")
    assert r.get_json()["data"]["status"] == competitor_analysis.STATUS_PENDING


# ---------------- 近 30 轮提及趋势：自动提取并集上百家 → 只画前 10 ----------------

def test_trend_capped_top10(client, tmpdb):
    names = [f"竞品{n}有限公司" for n in range(15)]
    # 独立品牌 2，避免同库其他用例的轮次混入聚合
    _seed(tmpdb, names, ["竞品0有限公司 竞品1有限公司 竞品2有限公司 都在"],
          comps_by_answer={0: [
              {"name": "竞品0有限公司", "count": 3, "position": 1},
              {"name": "竞品1有限公司", "count": 2, "position": 2},
              {"name": "竞品2有限公司", "count": 1, "position": 3}]},
          brand_id=2, brand_name="测试牌")

    r = client.get("/api/report/competitor/trend?brand_id=2&rounds=30")
    d = r.get_json()["data"]
    assert d["total"] == 15
    assert d["truncated"] is True
    assert len(d["series"]) == 4  # 自己 + 前 3 家竞品
    assert d["series"][0]["name"] == "测试牌"
    comp = [s["name"] for s in d["series"][1:]]
    assert len(comp) == 3
    # 被提到次数最多的排最前
    assert comp == ["竞品0有限公司", "竞品1有限公司", "竞品2有限公司"]
    # 值口径：竞品0 累计 3 次
    assert d["series"][1]["values"] == [3]

    # wanted（图例取消选择）过滤不受上限影响
    q = quote("竞品0有限公司,竞品1有限公司")
    r2 = client.get(f"/api/report/competitor/trend?brand_id=2&rounds=30&competitors={q}")
    d2 = r2.get_json()["data"]
    assert [s["name"] for s in d2["series"]] == ["测试牌", "竞品0有限公司", "竞品1有限公司"]
    assert d2["truncated"] is False


# ---------------- 深度分析聚焦前 8 家 ----------------

def test_generate_inner_cap(tmpdb, monkeypatch):
    names = [f"竞品{n}有限公司" for n in range(12)]
    # 前 4 家在两轮回答里被提到（存明细），其余无明细
    rid = _seed(tmpdb, names,
                ["竞品0有限公司和竞品1有限公司不错", "竞品2有限公司、竞品3有限公司也不错"],
                comps_by_answer={
                    0: [{"name": "竞品0有限公司", "count": 1, "position": 1},
                        {"name": "竞品1有限公司", "count": 1, "position": 2}],
                    1: [{"name": "竞品2有限公司", "count": 1, "position": 1},
                        {"name": "竞品3有限公司", "count": 1, "position": 2}],
                })
    with database.session_scope() as s:
        s.add(database.CompetitorAnalysis(
            round_id=rid, brand_id=1, status=competitor_analysis.STATUS_PENDING))

    monkeypatch.setattr(competitor_analysis, "_summarize_competitor",
                        lambda brand, name, evs: (f"总结：{name}",
                                                  ["行业媒体报道类"], ["特点"]))
    monkeypatch.setattr(competitor_analysis, "_generate_advice",
                        lambda brand, cd, note: [{"gap": "g", "where": "w", "what": "t"}])

    competitor_analysis._generate_inner(rid, 1, names)

    with database.session_scope() as s:
        row = (s.query(database.CompetitorAnalysis)
               .filter(database.CompetitorAnalysis.round_id == rid).first())
        assert row.status == competitor_analysis.STATUS_DONE
        data = database.jloads(row.data, None)
        assert data["total"] == 12
        assert data["truncated"] is True
        comps = data["competitors"]
        assert len(comps) == competitor_analysis.ANALYSIS_MAX_COMPETITORS
        mentioned = [c["name"] for c in comps if c["mentioned"]]
        assert mentioned == [f"竞品{n}有限公司" for n in range(4)]
