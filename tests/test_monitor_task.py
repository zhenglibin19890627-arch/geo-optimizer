"""监测提问构造与回答分析口径单测。"""

import threading

import pytest

from geo.core.monitor_task import SYSTEM_PROMPT, _analysis_for, build_messages
from geo.engines.base import ChatResult, EngineError


class _Question:
    id = 1
    text = "实验室改造找哪家公司靠谱？"


class _Adapter:
    code = "deepseek"


def test_只含中性系统提示词与问题本身():
    msgs = build_messages("实验室改造找哪家公司靠谱？")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == SYSTEM_PROMPT
    assert msgs[1] == {"role": "user", "content": "实验室改造找哪家公司靠谱？"}


def test_不注入品牌档案信息():
    # 防回归：此前把品牌名/简介作为"背景参考"注入系统提示词，导致提及率虚高
    # （连续 9 轮 100%）。提问里绝不允许出现品牌线索。
    msgs = build_messages("实验室改造找哪家公司靠谱？")
    joined = msgs[0]["content"] + "\n" + msgs[1]["content"]
    assert "云澜" not in joined
    assert "背景参考" not in joined
    assert "提及该品牌" not in joined
    assert "简介" not in joined


def test_中性提示词不含任何品牌占位():
    assert "{" not in SYSTEM_PROMPT
    assert "品牌" not in SYSTEM_PROMPT


def test_未提及品牌的回答情感计中性():
    # 防回归：夸竞品/夸行业的好评不得算作我方正面（会虚高净情感率）
    brand = {"brand_name": "云澜"}
    analysis = _analysis_for(
        _Adapter(), _Question(),
        ChatResult(text="好孩子很好，值得推荐，参考 https://example.com/b", model="m"),
        brand, competitors=["好孩子"], brand_names=["云澜"])
    assert analysis["is_mentioned"] is False
    assert analysis["sentiment"] == "neutral"


def test_提及品牌的好评计正面():
    brand = {"brand_name": "云澜"}
    analysis = _analysis_for(
        _Adapter(), _Question(),
        ChatResult(text="云澜很好，值得推荐", model="m"),
        brand, competitors=["好孩子"], brand_names=["云澜"])
    assert analysis["is_mentioned"] is True
    assert analysis["sentiment"] == "positive"


# ---------------- 同 key 多模型归一化 ----------------

def test_normalize_models_缺省与空选均用当前档():
    from geo.core.monitor_task import normalize_models
    from geo.engines import get_adapter
    cur = get_adapter("opencode").get_model()
    assert cur  # 配置模板有默认档
    assert normalize_models(["opencode"], None)["opencode"] == [cur]
    assert normalize_models(["opencode"], {"opencode": []})["opencode"] == [cur]


def test_normalize_models_多模型保序去重():
    from geo.core.monitor_task import normalize_models
    m = normalize_models(
        ["opencode"], {"opencode": ["kimi-k3", "grok-4.5", "kimi-k3"]})
    assert m["opencode"] == ["kimi-k3", "grok-4.5"]


def test_normalize_models_自由填写不再校验档位():
    # 2026-09-04 起模型改为手填：任意型号原样放行（执行时由引擎 API 判定存在性）
    from geo.core.monitor_task import normalize_models
    m = normalize_models(["opencode"], {"opencode": ["not-a-real-model"]})
    assert m["opencode"] == ["not-a-real-model"]


def test_normalize_models_联网档默认用联网模型():
    from geo.core.monitor_task import normalize_models
    from geo.engines import get_web_adapter
    m = normalize_models(["qwen"], None, web=True)
    assert m["qwen"] == [get_web_adapter("qwen").get_web_model()]


def test_normalize_models_联网档可显式选模型():
    from geo.core.monitor_task import normalize_models
    from geo.engines import get_web_adapter
    wm = get_web_adapter("qwen").get_web_model()
    m = normalize_models(["qwen"], {"qwen": [wm]}, web=True)
    assert m["qwen"] == [wm]


def test_normalize_models_联网档自由填写任意型号():
    # 2026-09-04 起联网档同样自由填写：不再有联网白名单拦截
    from geo.core.monitor_task import normalize_models
    m = normalize_models(
        ["qwen"], {"qwen": ["qwen3.5-livetranslate-flash-realtime"]}, web=True)
    assert m["qwen"] == ["qwen3.5-livetranslate-flash-realtime"]
    m2 = normalize_models(["qwen"], {"qwen": ["qwen3.7-max-2026-05-20"]}, web=True)
    assert m2["qwen"] == ["qwen3.7-max-2026-05-20"]


# ---------------- 发起互斥（2026-09 评审 R1） ----------------

@pytest.fixture(scope="module")
def tmpdb():
    """临时库：互斥/执行链路用例要真实建任务行，不碰正式 data/geo.db。"""
    import os
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from geo.models import db as database

    tmp = tempfile.mktemp(suffix=".db")
    old_engine, old_session = database.engine, database.SessionLocal
    database.engine = create_engine(
        f"sqlite:///{tmp}", connect_args={"check_same_thread": False})
    database.SessionLocal = sessionmaker(bind=database.engine, expire_on_commit=False)
    database.init_db()
    yield database
    database.engine, database.SessionLocal = old_engine, old_session
    try:
        os.remove(tmp)
    except OSError:
        pass


def _seed_mutex_brand(db) -> tuple:
    """建测试品牌（id=30）+ 一个启用问题，返回 (brand_id, question_id)。幂等。"""
    with db.session_scope() as s:
        if not s.get(db.BrandProfile, 30):
            s.add(db.BrandProfile(id=30, brand_name="互斥测试牌"))
            s.flush()
        q = s.query(db.QuestionBank).filter(
            db.QuestionBank.brand_id == 30).first()
        if not q:
            q = db.QuestionBank(brand_id=30, text="互斥用问题", enabled=True)
            s.add(q)
            s.flush()
        return 30, q.id


def _allow_all_engines(monkeypatch):
    from geo.engines.base import EngineAdapter
    monkeypatch.setattr(EngineAdapter, "is_configured", lambda self: True)


def _noop_run(monkeypatch):
    """发起后的执行线程不打桩会真跑 AI：替换为空操作，任务停在 pending。"""
    from geo.core import monitor_task as mt
    monkeypatch.setattr(mt, "run_monitor_task", lambda tid: None)


def _cleanup_tasks(db):
    """清空任务表：模块级临时库串用例，任务行不残留（含轮次/结果一并清）。"""
    with db.session_scope() as s:
        s.query(db.MonitorResult).delete()
        s.query(db.MonitorRound).delete()
        s.query(db.MonitorTask).delete()


def test_已有任务进行中时再次发起被拒(tmpdb, monkeypatch):
    """R1 回归：有一轮监测在跑时第二次 start 必须被大白话拒绝。"""
    from geo.core import monitor_task as mt
    brand_id, qid = _seed_mutex_brand(tmpdb)
    _allow_all_engines(monkeypatch)
    _noop_run(monkeypatch)

    ids = mt.start_serial_monitor_task(
        [qid], [("normal", ["opencode"], None)], brand_id=brand_id)
    assert len(ids) == 1

    with pytest.raises(EngineError) as ei:
        mt.start_serial_monitor_task(
            [qid], [("normal", ["opencode"], None)], brand_id=brand_id)
    assert "已经有一轮监测在跑了" in str(ei.value)
    _cleanup_tasks(tmpdb)


def test_并发双击只放行一个发起(tmpdb, monkeypatch):
    """R1 回归：两个线程同时 start（模拟双击/双标签页），锁保证「检查+创建」
    原子——只有一个成功建任务，另一个被拒绝；绝不出现两轮并发监测。"""
    from geo.core import monitor_task as mt
    brand_id, qid = _seed_mutex_brand(tmpdb)
    _allow_all_engines(monkeypatch)
    _noop_run(monkeypatch)

    barrier = threading.Barrier(2)
    outcomes = []

    def _caller():
        barrier.wait()
        try:
            outcomes.append(mt.start_serial_monitor_task(
                [qid], [("normal", ["opencode"], None)], brand_id=brand_id))
        except EngineError as e:
            outcomes.append(str(e))

    threads = [threading.Thread(target=_caller) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    succeeded = [o for o in outcomes if not isinstance(o, str)]
    rejected = [o for o in outcomes if isinstance(o, str)]
    assert len(outcomes) == 2
    assert len(succeeded) == 1
    assert len(rejected) == 1 and "已经有一轮监测在跑了" in rejected[0]
    with tmpdb.session_scope() as s:
        assert s.query(tmpdb.MonitorTask).count() == 1  # 只创建了一个任务
    _cleanup_tasks(tmpdb)


# ---------------- 执行链路（2026-09 评审 R5 短事务重构回归） ----------------

class _FakeAdapter:
    """可编排行为的假引擎：ok=正常回答 / error=抛大白话引擎错误。"""
    code = "opencode"
    display_name = "OpenCode"

    def __init__(self, behavior: str = "ok"):
        self.behavior = behavior

    def chat(self, messages, **kwargs):
        if self.behavior == "error":
            raise EngineError("钥匙不对，请检查")
        return ChatResult(text="互斥测试牌很好，值得推荐", model="test-model")


def _seed_run_task(db, brand_id: int, qid: int) -> int:
    """建一个 pending 监测任务行（1 引擎 × 1 模型 × 1 问题），返回 task_id。"""
    from geo.models import db as database
    with db.session_scope() as s:
        task = database.MonitorTask(
            type="manual", status="pending", progress=0,
            brand_id=brand_id, mode="normal",
            total_calls=1, done_calls=0,
            question_ids=database.jdumps([qid]),
            engine_codes=database.jdumps(["opencode"]),
            models=database.jdumps({"opencode": ["test-model"]}),
        )
        s.add(task)
        s.flush()
        return task.id


def _patch_runner(monkeypatch, behavior: str = "ok"):
    """执行链路打桩：假引擎 + 零限速等待 + 竞品收尾空转（绝不真实调 AI）。"""
    from geo.analyzers import competitor_analysis
    from geo.core import monitor_runner, monitor_task as mt
    monkeypatch.setattr(monitor_runner, "get_adapter", lambda code: _FakeAdapter(behavior))
    monkeypatch.setattr(mt, "_monitor_section", lambda: {"min_interval": 0, "max_interval": 0})
    monkeypatch.setattr(competitor_analysis, "finalize_competitors",
                        lambda *a, **k: None)


def _get_round_id(db, task_id: int):
    with db.session_scope() as s:
        round_row = (s.query(db.MonitorRound)
                     .filter(db.MonitorRound.task_id == task_id).first())
        return round_row.id if round_row else None


def test_执行链路_单条成功落库分析与评分快照(tmpdb, monkeypatch):
    from geo.core import monitor_runner
    brand_id, qid = _seed_mutex_brand(tmpdb)
    _patch_runner(monkeypatch)
    task_id = _seed_run_task(tmpdb, brand_id, qid)

    monitor_runner.run_monitor_task(task_id)

    round_id = _get_round_id(tmpdb, task_id)
    with tmpdb.session_scope() as s:
        task = s.get(tmpdb.MonitorTask, task_id)
        assert task.status == "done"
        assert (task.done_calls, task.progress) == (1, 100)
        results = (s.query(tmpdb.MonitorResult)
                   .filter(tmpdb.MonitorResult.round_id == round_id).all())
        assert len(results) == 1
        r = results[0]
        assert r.is_mentioned is True
        assert r.sentiment == "positive"
        assert r.answer_text and "互斥测试牌" in r.answer_text
        assert r.model == "test-model"
        round_row = s.get(tmpdb.MonitorRound, round_id)
        assert round_row.mention_rate == 1.0
        assert round_row.overall_score and round_row.overall_score > 0
        assert round_row.finished_at is not None
        snap = (s.query(tmpdb.ScoreSnapshot)
                .filter(tmpdb.ScoreSnapshot.round_id == round_id).first())
        assert snap is not None and snap.score == round_row.overall_score


def test_执行链路_断点续跑按问题引擎模型去重(tmpdb, monkeypatch):
    """R5 行为不变：任务重跑（断点续跑）时已答的 (问题,引擎,模型) 不重复调用。"""
    from geo.core import monitor_runner
    brand_id, qid = _seed_mutex_brand(tmpdb)
    _patch_runner(monkeypatch)
    task_id = _seed_run_task(tmpdb, brand_id, qid)

    monitor_runner.run_monitor_task(task_id)
    # 模拟中断后重跑：任务回到 pending，上一轮结果保留
    with tmpdb.session_scope() as s:
        s.query(tmpdb.MonitorTask).filter(
            tmpdb.MonitorTask.id == task_id).update({"status": "pending"})
    monitor_runner.run_monitor_task(task_id)

    round_id = _get_round_id(tmpdb, task_id)
    with tmpdb.session_scope() as s:
        results = (s.query(tmpdb.MonitorResult)
                   .filter(tmpdb.MonitorResult.round_id == round_id).all())
        assert len(results) == 1  # 去重：没有产生第二条回答
        task = s.get(tmpdb.MonitorTask, task_id)
        assert task.status == "done"


def test_执行链路_单条失败落error_msg不整轮失败(tmpdb, monkeypatch):
    from geo.core import monitor_runner
    brand_id, qid = _seed_mutex_brand(tmpdb)
    _patch_runner(monkeypatch, behavior="error")
    task_id = _seed_run_task(tmpdb, brand_id, qid)

    monitor_runner.run_monitor_task(task_id)

    round_id = _get_round_id(tmpdb, task_id)
    with tmpdb.session_scope() as s:
        task = s.get(tmpdb.MonitorTask, task_id)
        assert task.status == "done"  # 单条失败不整轮失败
        assert "有部分问题没问到" in (task.error_msg or "")
        assert "钥匙不对" in (task.error_msg or "")
        r = (s.query(tmpdb.MonitorResult)
             .filter(tmpdb.MonitorResult.round_id == round_id).first())
        assert r.answer_text is None
        assert r.error_msg == "钥匙不对，请检查"
        # 无有效回答 → 本轮不产评分快照
        assert (s.query(tmpdb.ScoreSnapshot)
                .filter(tmpdb.ScoreSnapshot.round_id == round_id).count()) == 0


def test_执行链路_取消收尾为cancelled不产快照(tmpdb, monkeypatch):
    from geo.core import monitor_runner, monitor_task as mt
    brand_id, qid = _seed_mutex_brand(tmpdb)
    _patch_runner(monkeypatch)
    task_id = _seed_run_task(tmpdb, brand_id, qid)

    mt.cancel_monitor_task(task_id)
    monitor_runner.run_monitor_task(task_id)

    round_id = _get_round_id(tmpdb, task_id)
    with tmpdb.session_scope() as s:
        task = s.get(tmpdb.MonitorTask, task_id)
        assert task.status == "cancelled"
        assert task.error_msg == "用户主动停止"
        assert (s.query(tmpdb.ScoreSnapshot)
                .filter(tmpdb.ScoreSnapshot.round_id == round_id).count()) == 0
        assert (s.query(tmpdb.MonitorResult)
                .filter(tmpdb.MonitorResult.round_id == round_id).count()) == 0
