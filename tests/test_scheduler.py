# -*- coding: utf-8 -*-
"""调度器测试：睡眠唤醒看门狗补跑 + 定时模型档位选择（2026-08-18）。

保障点：
- 不碰正式 data/geo.db（需要读写的用例换临时库，测完还原）；
- 不真正发起监测（monkeypatch 拦截 run_scheduled_monitor）；
- 看门狗判定用固定时间，避免测试在凌晨/午夜附近跑到边界值不稳定。
"""

import os
import tempfile
from datetime import datetime

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.models import db as database


@pytest.fixture(scope="module")
def tmpdb():
    """临时库：看门狗/模型选择涉及 settings 表读写，不碰正式数据。"""
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


# ---------------- 睡眠唤醒看门狗 ----------------

# 固定「现在」：2026-08-18（周二）11:30，定时点设 08:45 → 已过宽限期
_FIXED_NOW = datetime(2026, 8, 18, 11, 30)


class _FakeDateTime(datetime):
    @classmethod
    def now(cls):
        return _FIXED_NOW


def _patch_watchdog(monkeypatch, *, enabled=True, due=True,
                    last_date=None, sched_time="08:45"):
    """把看门狗依赖的外部条件全部打桩，返回 run_scheduled_monitor 调用记录。"""
    from geo.core import scheduler
    monkeypatch.setattr(scheduler, "datetime", _FakeDateTime)
    monkeypatch.setattr(scheduler, "_effective_enabled", lambda: enabled)
    monkeypatch.setattr(scheduler, "_interval_due", lambda: due)
    monkeypatch.setattr(scheduler, "_last_run_date", lambda: last_date)
    monkeypatch.setattr(scheduler, "_effective_time", lambda: sched_time)
    monkeypatch.setattr(scheduler, "_catchup_fired_date", None)
    calls = []
    monkeypatch.setattr(scheduler, "run_scheduled_monitor",
                        lambda background=True: calls.append(background))
    return calls


def test_看门狗_错过定时点自动补跑(monkeypatch):
    from geo.core import scheduler
    # 上次定时跑在 08-16，周期 2 天 → 今天该跑；定时点 08:45 已过 +10 分钟
    calls = _patch_watchdog(monkeypatch, last_date=datetime(2026, 8, 16).date())
    scheduler._catchup_if_missed()
    assert calls == [True]
    # 一天只兜底一次：再次巡检不重复发起
    scheduler._catchup_if_missed()
    assert calls == [True]


def test_看门狗_今天已跑过不补(monkeypatch):
    from geo.core import scheduler
    calls = _patch_watchdog(monkeypatch, last_date=_FIXED_NOW.date())
    scheduler._catchup_if_missed()
    assert calls == []


def test_看门狗_定时关闭不补(monkeypatch):
    from geo.core import scheduler
    calls = _patch_watchdog(monkeypatch, enabled=False,
                            last_date=datetime(2026, 8, 16).date())
    scheduler._catchup_if_missed()
    assert calls == []


def test_看门狗_监测周期未到不补(monkeypatch):
    from geo.core import scheduler
    # 周期 2 天、昨天刚跑过 → 今天不该跑，看门狗不能凑热闹
    calls = _patch_watchdog(monkeypatch, due=False,
                            last_date=datetime(2026, 8, 17).date())
    scheduler._catchup_if_missed()
    assert calls == []


def test_看门狗_还没到定时点不补(monkeypatch):
    from geo.core import scheduler
    # 定时点 23:00 还没到（含 +10 分钟宽限），留给正点 cron
    calls = _patch_watchdog(monkeypatch, last_date=datetime(2026, 8, 16).date(),
                            sched_time="23:00")
    scheduler._catchup_if_missed()
    assert calls == []


def test_看门狗_宽限期内不抢跑(monkeypatch):
    from geo.core import scheduler
    # 定时点 11:35，宽限到 11:45，「现在」11:30 还在宽限期内 → 不补
    calls = _patch_watchdog(monkeypatch, last_date=datetime(2026, 8, 16).date(),
                            sched_time="11:35")
    scheduler._catchup_if_missed()
    assert calls == []


# ---------------- 定时模型档位选择 ----------------

def _clear_models_setting():
    database.set_setting("schedule_models", None)


def test_定时模型选择_过滤掉已下架档位(tmpdb, monkeypatch):
    from geo.core import scheduler
    _clear_models_setting()
    database.set_setting("schedule_models", database.jdumps({
        "normal": {"deepseek": ["deepseek-v4-flash", "ghost-tier"]},
        "web": {},
    }))
    got = scheduler.effective_schedule_models("normal")
    # ghost-tier 不在当前档位白名单里，宽松剔除而不是抛错
    assert got.get("deepseek") == ["deepseek-v4-flash"]
    assert scheduler.effective_schedule_models("web") == {}


def test_定时模型选择_空与坏数据回落(tmpdb):
    from geo.core import scheduler
    _clear_models_setting()
    assert scheduler.effective_schedule_models("normal") == {}
    # 坏形状（存成数组/字符串）不炸，回落空
    database.set_setting("schedule_models", "[1,2,3]")
    assert scheduler.effective_schedule_models("normal") == {}


def test_filter_models_宽松过滤():
    from geo.core.monitor_task import filter_models
    assert filter_models(
        ["deepseek", "qwen"],
        {"deepseek": ["deepseek-v4-flash", "ghost"], "qwen": []},
    ) == {"deepseek": ["deepseek-v4-flash"]}
    # 字符串形态容错 + 去重
    assert filter_models(
        ["deepseek"],
        {"deepseek": "deepseek-v4-flash"},
    ) == {"deepseek": ["deepseek-v4-flash"]}
    assert filter_models(["deepseek"], {"deepseek": ["a", "a"]}) == {}


# ---------------- 定时模型选择 → 引擎收敛（2026-08-22 修复） ----------------

def _seed_sched_brand():
    """建测试品牌（id=9）+ 一个启用问题，返回品牌 id。幂等。"""
    with database.session_scope() as s:
        if not s.get(database.BrandProfile, 9):
            s.add(database.BrandProfile(id=9, brand_name="测试牌", auto_monitor=True))
            s.flush()
            s.add(database.QuestionBank(brand_id=9, text="问题A", enabled=True))


def _patch_sched_run(monkeypatch, engines_all):
    from geo.core import monitor_task, scheduler
    captured = {}

    def fake_start(qids, engines, task_type="manual", brand_id=1,
                   mode="normal", models=None):
        captured[mode] = (list(engines or []), models)
        return 999

    monkeypatch.setattr(monitor_task, "start_monitor_task", fake_start)
    monkeypatch.setattr(monitor_task, "enabled_auto_engines",
                        lambda: list(engines_all))
    monkeypatch.setattr(scheduler, "_wait_task_end", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "effective_modes", lambda: ["normal"])
    monkeypatch.setattr(scheduler, "_interval_due", lambda: True)
    return captured


def test_定时模型勾选后没勾的引擎不参加(tmpdb, monkeypatch):
    """设置页勾了豆包/千问/元宝 → 定时只跑这三家；deepseek/opencode
    不能被 normalize_models 的「回落当前档」拉进来（8-22 实际发生过）。"""
    from geo.core import scheduler
    _seed_sched_brand()
    _clear_models_setting()
    database.set_setting("schedule_models", database.jdumps({
        "normal": {"doubao": ["doubao-seed-2-0-mini-260428"],
                   "qwen": ["qwen3.7-max-2026-05-17"],
                   "yuanbao": ["hy3"]},
        "web": {},
    }))
    captured = _patch_sched_run(
        monkeypatch, ["deepseek", "doubao", "qwen", "yuanbao", "opencode"])

    scheduler.run_scheduled_monitor(background=False)

    engines, models = captured["normal"]
    assert sorted(engines) == ["doubao", "qwen", "yuanbao"]
    assert set((models or {}).keys()) == {"doubao", "qwen", "yuanbao"}


def test_定时模型全空时保持全量引擎当前档(tmpdb, monkeypatch):
    from geo.core import scheduler
    _seed_sched_brand()
    _clear_models_setting()
    captured = _patch_sched_run(monkeypatch, ["deepseek", "doubao"])

    scheduler.run_scheduled_monitor(background=False)

    engines, models = captured["normal"]
    assert engines == ["deepseek", "doubao"]
    assert models is None  # 全空 → normalize_models 各家回落当前档


def test_定时模型勾选全被过滤时空名单跳过(tmpdb, monkeypatch):
    """勾选的档位后来全部下架 → 该模式跳过，不发起空任务。"""
    from geo.core import scheduler
    _seed_sched_brand()
    _clear_models_setting()
    database.set_setting("schedule_models", database.jdumps({
        "normal": {"deepseek": ["ghost-tier"]}, "web": {}}))
    captured = _patch_sched_run(monkeypatch, ["deepseek", "doubao"])

    scheduler.run_scheduled_monitor(background=False)

    assert "normal" not in captured  # 没有发起任何任务
