"""监测任务编排：任务生命周期（发起/停止/进度入口/僵尸回收）与统计口径。

后台执行细节（线程逐条调用 AI、落库分析、收尾统计预警）拆在 monitor_runner.py；
模型档位校验/归一拆在 model_selection.py。这里 re-export 既有公共名：
`monitor_task.run_monitor_task`、`monitor_task.get_progress`、
`from geo.core.monitor_task import build_messages` 等引用与测试注入点均不变。
"""

import threading
from datetime import datetime

from geo import config
# 再导出：SYSTEM_PROMPT / build_messages / filter_models 本体在 model_selection，
# _analysis_for 在 monitor_runner；tests 以 from geo.core.monitor_task import ...
# 的历史路径使用，保留以免破坏存量测试（ruff F401 用 noqa 放行）。
from geo.core.model_selection import SYSTEM_PROMPT  # noqa: F401
from geo.core.model_selection import build_messages  # noqa: F401
from geo.core.model_selection import filter_models  # noqa: F401
from geo.core.model_selection import normalize_models
from geo.core.monitor_runner import _analysis_for  # noqa: F401
# get_progress 也是既有公共名（docstring 与 geo/web/api_monitor.py:143 的
# monitor_task.get_progress(...) 调用都依赖），漏了会在请求时才炸 Attribute
from geo.core.monitor_runner import get_progress  # noqa: F401
from geo.core.monitor_runner import run_monitor_task
from geo.engines import base as engine_base, get_adapter
from geo.models import db as database

# 停止本轮监测：模块级取消标志（线程安全，避免频繁查库）
_cancel_lock = threading.Lock()
_cancelled_task_ids = set()

# 发起互斥锁（2026-09 评审 R1 修复）：把「检查有没有任务在跑 + 创建新任务」
# 包成原子临界区。此前 any_task_running 检查与建任务之间不原子，Flask 多线程
# 下并发双击/双标签页两个 POST /api/monitor/start 都能通过检查、各自建任务
# 开线程，变成两轮监测并发烧钱调 AI。单进程系统一把进程内锁即可：第二个请求
# 排队进锁后必能看到第一个请求已提交的 pending 任务，从而被大白话拒绝。
# 注意：锁只包「检查+建任务」的数据库事务；执行线程在锁外启动，不会持锁等 AI。
_start_lock = threading.Lock()

# 断点续跑窗口（秒）：与 scheduler._recover_and_catchup 共用同一口径。
# reap_stale_tasks 对窗口内的中断任务留活口不置 failed，交给调度器断点续跑；
# 窗口外的中断任务才在这里收尾为 failed。此前两处各写各的（这里全收尾、
# 那边只认 2 小时内），导致续跑分支永远查不到任务（评审 R2 死代码）。
_RESUME_WINDOW_SECONDS = 2 * 3600


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
    """回收僵尸任务：程序上次退出时中断的 pending/running 任务收尾（R2 口径）。

    服务进程退出时后台监测线程会随之死亡，任务状态永远停在 pending/running，
    导致下次启动后 any_task_running 永久拦截新监测。启动时调用本函数收尾，
    但不再一刀切全置 failed（那样 scheduler._recover_and_catchup 的
    「2 小时内断点续跑」就永远查不到任务，成为死代码）。两套机制现在分工：
    - 中断时点（started_at，从未启动的 pending 用 created_at）距今不足
      _RESUME_WINDOW_SECONDS（2 小时）：这里不动、留活口，交由
      _recover_and_catchup 断点续跑（已问到的回答已落库，续跑自动去重）；
      GEO_NO_SCHEDULER=1（测试）时调度器不启动，留活口的任务不会被续跑，
      由调用方自行处理。
    - 中断超过 2 小时：已经隔了太久，不自动重跑（避免启动即烧钱跑一轮谁也
      不记得的旧任务），在这里置 failed 收尾。
    返回置 failed 的任务数（留活口的不计入）。
    """
    now = datetime.now()
    reaped = 0
    with database.session_scope() as s:
        rows = (s.query(database.MonitorTask)
                .filter(database.MonitorTask.status.in_(["pending", "running"])).all())
        for t in rows:
            # 中断时点：跑过的看 started_at，从未启动的 pending 看 created_at；
            # 两者都没有（极老数据）视为刚中断、留活口（与续跑分支 age=None 同口径）
            ref = t.started_at or t.created_at
            if ref is None or (now - ref).total_seconds() < _RESUME_WINDOW_SECONDS:
                continue  # 刚中断（或从未启动）：留给断点续跑
            t.status = "failed"
            t.error_msg = "程序上次退出时中断了这轮监测，已自动结束；重新发起一轮即可"
            if t.finished_at is None:
                t.finished_at = now
            reaped += 1
    return reaped


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
    # R1：互斥锁包住「检查+创建」，锁释放、事务提交后才启动执行线程
    with _start_lock, database.session_scope() as s:
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
    # R1：互斥锁包住「检查+创建全部模式任务」，与 start_monitor_task、定时触发
    # 同一把锁——手动双击/双标签并发、手动与定时同时发起，都只有一个能通过检查
    with _start_lock, database.session_scope() as s:
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


