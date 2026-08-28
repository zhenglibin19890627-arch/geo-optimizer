"""监测任务编排：任务生命周期（发起/停止/进度入口/僵尸回收）与统计口径。

后台执行细节（线程逐条调用 AI、落库分析、收尾统计预警）拆在 monitor_runner.py；
模型档位校验/归一拆在 model_selection.py。这里 re-export 既有公共名：
`monitor_task.run_monitor_task`、`monitor_task.get_progress`、
`from geo.core.monitor_task import build_messages` 等引用与测试注入点均不变。
"""

import threading
from datetime import datetime

from geo import config
from geo.core.model_selection import (SYSTEM_PROMPT, allowed_models, build_messages,
                                      filter_models, normalize_models)
from geo.core.monitor_runner import _analysis_for, get_progress, run_monitor_task
from geo.engines import base as engine_base, get_adapter
from geo.models import db as database

# 停止本轮监测：模块级取消标志（线程安全，避免频繁查库）
_cancel_lock = threading.Lock()
_cancelled_task_ids = set()


def _is_cancelled(task_id: int) -> bool:
    with _cancel_lock:
        return task_id in _cancelled_task_ids


def cancel_monitor_task(task_id: int):
    """停止一轮监测：校验后置取消标志，由运行线程在下一个检查点收尾保存。"""
    with database.session_scope() as s:
        task = s.get(database.MonitorTask, task_id)
        if not task:
            raise engine_base.EngineError("这轮监测找不到啦")
        if task.status not in ("pending", "running"):
            raise engine_base.EngineError("这轮监测还没开始或已经结束，不用停啦")
    with _cancel_lock:
        _cancelled_task_ids.add(task_id)


def _monitor_section():
    return config.get_section("monitor", {})


def get_engine_meta(code: str) -> dict:
    adapter = get_adapter(code)
    return {"code": code, "display_name": adapter.display_name, "note": adapter.note or ""}


def any_task_running(session) -> bool:
    return (session.query(database.MonitorTask)
            .filter(database.MonitorTask.status.in_(["pending", "running"]))
            .count()) > 0


def reap_stale_tasks() -> int:
    """回收僵尸任务：程序上次退出时中断的 pending/running 任务标记为 failed。

    服务进程退出时后台监测线程会随之死亡，任务状态永远停在 running，
    导致下次启动后 any_task_running 永久拦截新监测。程序启动时调用本函数
    把这些残留任务收尾（已问到的回答已落库保留，轮次按 failed 不参与统计）。
    返回回收的任务数。
    """
    with database.session_scope() as s:
        rows = (s.query(database.MonitorTask)
                .filter(database.MonitorTask.status.in_(["pending", "running"])).all())
        for t in rows:
            t.status = "failed"
            t.error_msg = "程序上次退出时中断了这轮监测，已自动结束；重新发起一轮即可"
            if t.finished_at is None:
                t.finished_at = datetime.now()
        return len(rows)


def enabled_auto_engines() -> list:
    """已启用 且 已填钥匙 的自动引擎（用于“全部”默认勾选）。

    遍历引擎注册表 AUTO_CODES：新增引擎（如 opencode）自动纳入，无需改这里。
    """
    from geo.engines import AUTO_CODES
    result = []
    for code in AUTO_CODES:
        try:
            adapter = get_adapter(code)
        except Exception:
            continue
        if adapter.is_enabled() and adapter.is_configured():
            result.append(code)
    return result


def web_auto_engines() -> list:
    """联网档：已启用 且 已填钥匙 的联网引擎（按 supports_web_search 过滤）。"""
    from geo.engines import AUTO_CODES
    result = []
    for code in AUTO_CODES:
        try:
            adapter = get_adapter(code)
        except Exception:
            continue
        if not adapter.supports_web_search:
            continue
        if adapter.is_enabled() and adapter.is_configured():
            result.append(code)
    return result


def start_monitor_task(question_ids: list, engine_codes: list,
                       task_type: str = "manual", brand_id: int = 1,
                       mode: str = "normal", models: dict = None) -> int:
    """校验并创建任务，后台线程执行。返回 task_id。

    models（可选）：{engine_code: [model, ...]}，同一把钥匙下同时监测多个模型
    （常规/联网档均支持）；缺省每家引擎用当前档（联网档用联网档模型）。
    """
    mode = str(mode or "normal").strip() or "normal"
    if mode not in ("normal", "web"):
        raise engine_base.EngineError("这个模式不认，请选择「常规提问」或「联网提问」")
    model_map = normalize_models(engine_codes or [], models, web=(mode == "web"))
    with database.session_scope() as s:
        if any_task_running(s):
            raise engine_base.EngineError("已经有一轮监测在跑了，请等它完成后再发起新一轮")

        if not database.brand_exists(brand_id):
            raise engine_base.EngineError("这个品牌不存在，可能已被删除")

        qs = (s.query(database.QuestionBank)
              .filter(database.QuestionBank.brand_id == brand_id)
              .filter(database.QuestionBank.id.in_(question_ids or []))
              .filter(database.QuestionBank.enabled == True).all())
        if not qs:
            raise engine_base.EngineError("问题库是空的（或选中的问题已停用），请先到「问题库」页添加问题")

        missing = []
        for code in engine_codes or []:
            try:
                adapter = get_adapter(code)
            except Exception:
                continue
            if not adapter.is_configured():
                missing.append(adapter.display_name)
        if missing:
            names = "、".join(dict.fromkeys(missing))
            raise engine_base.EngineError(f"{names}的钥匙（API Key）还没填，请先到设置页填写")

        total = len(qs) * sum(len(model_map.get(code, [1])) for code in engine_codes or [])
        per = int(_monitor_section().get("estimated_seconds_per_call", 10) or 10)
        task = database.MonitorTask(
            type=task_type, status="pending", progress=0,
            brand_id=brand_id, mode=mode,
            total_calls=total, done_calls=0,
            estimated_seconds=total * per,
            question_ids=database.jdumps([q.id for q in qs]),
            engine_codes=database.jdumps(engine_codes or []),
            models=database.jdumps(model_map),
        )
        s.add(task)
        s.flush()
        task_id = task.id
    threading.Thread(target=run_monitor_task, args=(task_id,), daemon=True).start()
    return task_id


def start_serial_monitor_task(question_ids: list, mode_specs: list,
                              task_type: str = "manual", brand_id: int = 1) -> list:
    """一轮多模式串行监测（2026-08-19 手动版）：预创建全部任务，单线程逐个执行。

    mode_specs: [(mode, engine_codes, models), ...]，按给定顺序串行
    （如先 normal 后 web）；mode 重复以首次为准。返回全部 task_id，
    前端一次拿到所有 id 即可整体展示进度/整体停止。
    全局互斥语义不变：创建时校验 any_task_running，执行线程逐个跑，
    同一时刻仍只有一个任务在跑（与定时多品牌串行同一口径）。
    """
    specs = []
    seen = set()
    for mode, engines, models in mode_specs or []:
        mode = str(mode or "normal").strip() or "normal"
        if mode not in ("normal", "web"):
            raise engine_base.EngineError("这个模式不认，请选择「常规提问」或「联网提问」")
        if mode in seen:
            continue
        seen.add(mode)
        specs.append((mode, list(engines or []), models))
    if not specs:
        raise engine_base.EngineError("请至少选择一种监测模式（常规提问或联网提问）")

    per = int(_monitor_section().get("estimated_seconds_per_call", 10) or 10)
    with database.session_scope() as s:
        if any_task_running(s):
            raise engine_base.EngineError("已经有一轮监测在跑了，请等它完成后再发起新一轮")
        if not database.brand_exists(brand_id):
            raise engine_base.EngineError("这个品牌不存在，可能已被删除")
        qs = (s.query(database.QuestionBank)
              .filter(database.QuestionBank.brand_id == brand_id)
              .filter(database.QuestionBank.id.in_(question_ids or []))
              .filter(database.QuestionBank.enabled == True).all())
        if not qs:
            raise engine_base.EngineError("问题库是空的（或选中的问题已停用），请先到「问题库」页添加问题")

        all_engines = []
        for _, engines, _ in specs:
            for c in engines:
                if c not in all_engines:
                    all_engines.append(c)
        missing = []
        for code in all_engines:
            try:
                adapter = get_adapter(code)
            except Exception:
                continue
            if not adapter.is_configured():
                missing.append(adapter.display_name)
        if missing:
            names = "、".join(dict.fromkeys(missing))
            raise engine_base.EngineError(f"{names}的钥匙（API Key）还没填，请先到设置页填写")

        task_ids = []
        for mode, engines, models in specs:
            model_map = normalize_models(engines, models, web=(mode == "web"))
            total = len(qs) * sum(len(model_map.get(code, [1])) for code in engines)
            task = database.MonitorTask(
                type=task_type, status="pending", progress=0,
                brand_id=brand_id, mode=mode,
                total_calls=total, done_calls=0,
                estimated_seconds=total * per,
                question_ids=database.jdumps([q.id for q in qs]),
                engine_codes=database.jdumps(engines),
                models=database.jdumps(model_map),
            )
            s.add(task)
            s.flush()
            task_ids.append(task.id)

    def _run_all():
        for tid in task_ids:
            run_monitor_task(tid)

    threading.Thread(target=_run_all, daemon=True).start()
    return task_ids


def task_status_map(s) -> dict:
    """任务 id -> status 映射：用于判断轮次是否正常完成（统计口径）。"""
    return {t.id: t.status for t in s.query(database.MonitorTask).all()}


def round_is_normal(status_map: dict, round_row) -> bool:
    """轮次是否正常完成：任务缺失视为正常；cancelled/failed 不算正常。"""
    return status_map.get(round_row.task_id) in (None, "done")


