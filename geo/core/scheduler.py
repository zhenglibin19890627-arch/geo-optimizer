"""APScheduler 定时自动监测：默认每日 08:30、可修改、错过 26 小时自动补跑。

2026-08-18：新增「睡眠唤醒看门狗」——APScheduler 后台线程在 Windows 整机
睡眠唤醒后可能不再补发错过的任务（实测当天定时点睡眠，唤醒后 misfire
未触发、当天监测静默丢失），故由独立看门狗线程兜底：只要已过定时点
+10 分钟、且今天该跑还没跑，就自动补跑一轮。
"""

import threading
import time
import traceback
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from geo import config
from geo.engines import base as engine_base
from geo.models import db as database

_scheduler = None
_lock = threading.Lock()
_CATCHUP_KEY = "catchup_done_20260809"


def _effective_time() -> str:
    return str(database.get_setting("schedule_time", "08:30"))


def _effective_enabled() -> bool:
    return bool(database.get_setting("schedule_enabled", True))


def effective_modes() -> list:
    """定时监测模式：['normal'] / ['web'] / ['normal','web']（2026-08-16 起可多选）。

    读取设置键 schedule_modes（JSON 数组）；未设置时兼容旧 schedule_web_mode
    布尔键（True→['web']，False→['normal']）。
    """
    modes = database.jloads(database.get_setting("schedule_modes", None), []) or []
    modes = list(dict.fromkeys(str(m).strip() for m in modes
                               if str(m).strip() in ("normal", "web")))
    if not modes:
        return ["web"] if database.get_setting("schedule_web_mode", False) else ["normal"]
    return modes


def effective_interval_days() -> int:
    """监测周期（天）：1=每天，2=每 2 天，7=每周……（2026-08-16 起可调）。"""
    try:
        n = int(database.get_setting("schedule_interval_days", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, 30))


def effective_schedule_models(mode: str) -> dict:
    """定时监测的模型填写（设置页保存）：{engine: [model, ...]}。

    存在 settings 表 schedule_models 键（JSON：{"normal": {...}, "web": {...}}）。
    读取时只做宽松清洗（型号自由填写，不校验档位白名单）——某家引擎清洗后
    为空则不出现（该引擎不参加定时，其余执行时回落各自当前档）。
    """
    from geo.core import monitor_task
    raw = database.jloads(database.get_setting("schedule_models", None), {}) or {}
    if not isinstance(raw, dict):
        return {}
    per = raw.get(mode) or {}
    if not isinstance(per, dict):
        return {}
    return monitor_task.filter_models(list(per.keys()), per, web=(mode == "web"))


def _last_run_date():
    raw = database.get_setting("schedule_last_run_date", None)
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except Exception:
        return None


def _interval_due() -> bool:
    """按周期判断今天是否该跑（cron 每天触发，周期>1 时在任务内跳过不足天数）。"""
    interval = effective_interval_days()
    if interval <= 1:
        return True
    last = _last_run_date()
    if last is None:
        return True
    return (datetime.now().date() - last).days >= interval


def _last_done_task(s) -> database.MonitorTask:
    return (s.query(database.MonitorTask)
            .filter(database.MonitorTask.status == "done")
            .order_by(database.MonitorTask.finished_at.desc()).first())


def run_scheduled_monitor(background: bool = True):
    """跑一轮定时监测：参加定时自动监测的品牌逐个串行监测（02d 3.1.4）。

    全局同一时刻只有一轮监测（any_task_running 语义不变）：手动监测进行中
    定时触发即跳过本轮；跨品牌也不并发（同一进程同一批钥匙，串行等待）。
    """

    def _do():
        from geo.core import monitor_task

        if not _interval_due():
            print(f"【定时监测】距离上次监测不足 {effective_interval_days()} 天，本轮按周期跳过。")
            return

        with database.session_scope() as s:
            if monitor_task.any_task_running(s):
                print("【定时监测】已经有一轮监测在跑了，本轮自动监测跳过，"
                      "等它结束、下次定时时间再跑。")
                return
            brands = [b.to_dict() for b in
                      (s.query(database.BrandProfile)
                       .filter(database.BrandProfile.auto_monitor == True)
                       .order_by(database.BrandProfile.id.asc()).all())]
            modes = effective_modes()

        active = [b for b in brands if (b.get("brand_name") or "").strip()]
        if not active:
            print("【定时监测】还没有任何品牌参加定时自动监测（可以在设置页打开），本轮已跳过。")
            return

        # 本轮算一次执行：记录执行日期，供周期（每 N 天）判断下次应跑时间
        database.set_setting("schedule_last_run_date",
                             datetime.now().strftime("%Y-%m-%d"))

        mode_labels = {"normal": "常规提问", "web": "联网提问"}
        names = "、".join(f"「{b['brand_name']}」" for b in active)
        mode_label = " + ".join(mode_labels[m] for m in modes)
        print(f"【定时监测】本轮按「{mode_label}」监测 {len(active)} 个品牌：先测{names}。")

        for b in active:
            brand_id = b["id"]
            brand_name = b["brand_name"]
            with database.session_scope() as s:
                qids = [q.id for q in s.query(database.QuestionBank)
                        .filter(database.QuestionBank.brand_id == brand_id,
                                database.QuestionBank.enabled == True).all()]
            if not qids:
                print(f"【定时监测】「{brand_name}」的问题库是空的，跳过它，继续下一个品牌。")
                continue
            for mode in modes:
                # 每种模式发起前复查全局互斥：手动监测若恰好在串行间隙发起，则本轮到此为止
                with database.session_scope() as s:
                    if monitor_task.any_task_running(s):
                        print("【定时监测】有一轮监测正在跑，本轮到此为止，下次定时时间再继续。")
                        return
                if mode == "web":
                    engines = monitor_task.web_auto_engines()
                else:
                    engines = monitor_task.enabled_auto_engines()
                if not engines:
                    if mode == "web":
                        print(f"【定时监测】「{brand_name}」联网提问的引擎钥匙还没填"
                              "（DeepSeek、豆包、通义千问至少一家），跳过该模式。")
                    else:
                        print(f"【定时监测】「{brand_name}」的引擎钥匙还没填，"
                              "跳过该模式。请到设置页填写钥匙。")
                    continue
                # 2026-08-22 修复：设置页填了定时模型时，没填的引擎不能参加——
                # 此前全量引擎传入 start_monitor_task，normalize_models 会给没填的
                # 引擎回落当前档，导致「明明只填了豆包/千问/元宝，deepseek、
                # opencode 也被拉去跑」（钥匙失效的 deepseek 还整轮报错）。
                # 区分两种「空」：没做过选择 → 全部启用引擎 + 当前档（原语义）；
                # 做过选择但填写的型号清洗后全为空 → 跳过该模式（不能悄悄扩到全量引擎）。
                sched_models = effective_schedule_models(mode)
                raw_models = database.jloads(
                    database.get_setting("schedule_models", None), {}) or {}
                made_choice = bool(isinstance(raw_models, dict)
                                   and (raw_models.get(mode) or {}))
                if sched_models or made_choice:
                    # 勾选即参加：清单里有条目（含空列表=回落当前档）的引擎才跑
                    engines = [c for c in engines if c in sched_models]
                    if not engines:
                        print(f"【定时监测】「{brand_name}」{mode_labels[mode]}"
                              "没有勾选任何可参加的引擎，跳过该模式。")
                        continue
                try:
                    task_id = monitor_task.start_monitor_task(
                        qids, engines, task_type="scheduled", brand_id=brand_id,
                        mode=mode, models=sched_models or None)
                except engine_base.EngineError as e:
                    print(f"【定时监测】「{brand_name}」{mode_labels[mode]}发起失败："
                          f"{e.message}，跳过该模式，继续。")
                    continue
                print(f"【定时监测】「{brand_name}」{mode_labels[mode]}开始"
                      f"（任务 {task_id}），等它跑完再继续……")
                _wait_task_end(task_id, brand_name)

    if background:
        threading.Thread(target=_do, daemon=True).start()
    else:
        _do()


def _wait_task_end(task_id: int, brand_name: str):
    """轮询等待某品牌任务结束（每 5s 查 status）；单品牌超 2 小时标记跳过并停止它。"""
    import time
    from datetime import datetime as _real_dt
    deadline = datetime.now() + timedelta(hours=2)
    stopped = False
    grace = None
    while True:
        time.sleep(5)
        status = _task_status(task_id)
        if status in ("done", "failed", "cancelled"):
            print(f"【定时监测】「{brand_name}」跑完了（{status}），继续下一个品牌。")
            return
        if status is None:
            return
        if not stopped and datetime.now() >= deadline:
            print(f"【定时监测】「{brand_name}」跑了超过 2 小时还没结束，本轮先跳过它，"
                  "已把它停下来，继续下一个品牌。")
            from geo.core import monitor_task
            try:
                monitor_task.cancel_monitor_task(task_id)
            except Exception:
                pass
            stopped = True
            # 停下来的等待用真实时间：等它真正让出全局单任务位，再测下一个品牌
            grace = _real_dt.now() + timedelta(minutes=1)
        elif stopped and _real_dt.now() >= grace:
            print(f"【定时监测】「{brand_name}」没能在宽限时间内停下，继续下一个品牌；"
                  "如果它还在跑，下一轮定时会自动跳过。")
            return


def _task_status(task_id: int):
    with database.session_scope() as s:
        t = s.get(database.MonitorTask, task_id)
        if not t:
            return None
        return t.status


# ---------------- 睡眠唤醒/错过补跑看门狗（2026-08-18） ----------------

_WAKE_CHECK_SECONDS = 30   # 看门狗巡检间隔
_WAKE_GRACE_MINUTES = 10   # 定时点过后多少分钟仍没跑才视为错过（给正点 cron 留时间）
_catchup_fired_date = None  # 看门狗当天已补跑过的日期（防与手动监测撞车时反复触发）


def _start_wake_watchdog():
    def _loop():
        while True:
            time.sleep(_WAKE_CHECK_SECONDS)
            try:
                _catchup_if_missed()
            except Exception:
                traceback.print_exc()

    threading.Thread(target=_loop, daemon=True, name="geo-schedule-watchdog").start()


def _catchup_if_missed():
    """错过定时点的兜底：电脑睡眠/调度器漏发时，醒来后自动补跑当天该跑的一轮。

    判定条件（全部满足才补跑）：
    - 定时开关开着，且按周期今天该跑（_interval_due）；
    - 今天还没跑过定时监测（schedule_last_run_date < 今天）；
    - 已过「定时点 + 10 分钟」——正常情况正点 cron 早就把它置为今天；
    - 看门狗今天没补跑过（一天最多兜底一次，避免与手动监测撞车时反复起线程）。
    """
    global _catchup_fired_date
    if not _effective_enabled():
        return
    if not _interval_due():
        return
    today = datetime.now().date()
    last = _last_run_date()
    if last is not None and last >= today:
        return  # 今天已经跑过定时监测
    if _catchup_fired_date == today:
        return  # 看门狗今天已兜底过一次
    hour, minute = _parse_time(_effective_time(), fallback=(8, 30))
    scheduled_today = datetime.now().replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    if datetime.now() < scheduled_today + timedelta(minutes=_WAKE_GRACE_MINUTES):
        return  # 还没到「定时点+10分钟」，先让正点 cron 去跑
    _catchup_fired_date = today
    print("【定时监测】发现今天到点的定时监测还没跑（可能是电脑睡眠或调度器错过），"
          "现在自动补跑一轮……")
    run_scheduled_monitor(background=True)


def ensure_scheduler_started():
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _schedule_job()
        _scheduler.start()
        _recover_and_catchup()
        _start_wake_watchdog()


def _schedule_job():
    time_str = _effective_time()
    hour, minute = _parse_time(time_str, fallback=(8, 30))
    job = _scheduler.get_job("daily_monitor")
    if job:
        _scheduler.remove_job("daily_monitor")
    _scheduler.add_job(
        run_scheduled_monitor,
        CronTrigger(hour=hour, minute=minute),
        id="daily_monitor",
        coalesce=True,
        misfire_grace_time=3600,
    )


def _parse_time(time_str: str, fallback=(8, 30)):
    try:
        parts = str(time_str).split(":")
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return fallback


def _recover_and_catchup():
    """① 断点续跑：中断 2 小时内的任务重新拉起；② 错过昨日定时则补跑一轮。"""
    from geo.core import monitor_task
    with database.session_scope() as s:
        tasks = (s.query(database.MonitorTask)
                 .filter(database.MonitorTask.status.in_(["pending", "running"])).all())
        for t in tasks:
            age = None
            if t.started_at:
                age = (datetime.now() - t.started_at).total_seconds()
            if age is None or age < 7200:
                # 刚中断（或从未启动）：交给断点续跑
                import threading as th
                th.Thread(target=monitor_task.run_monitor_task, args=(t.id,), daemon=True).start()
            else:
                t.status = "failed"
                t.error_msg = "上一次监测被中断超过 2 小时，已自动停止；可以重新发起一轮"
                t.finished_at = datetime.now()

        if _effective_enabled():
            last = _last_done_task(s)
            hours = float(config.get_section("monitor", {}).get("catchup_hours", 26) or 26)
            missed = last is None or (datetime.now() - last.finished_at) > timedelta(hours=hours)
            # 补跑同样遵守监测周期（每 N 天时不能每次启动都补）
            if missed and _interval_due() and not database.get_setting(_CATCHUP_KEY, False):
                database.set_setting(_CATCHUP_KEY, True)
                if last is None:
                    # 全新库：从未监测过，不自动补跑（避免首启产生费用），仅给一句提示
                    print("【定时监测】还没有成功监测过任何一轮：暂不自动补跑，"
                          "填好钥匙后可以随时手动发起，或等下次定时时间自动开跑。")
                else:
                    print("【定时监测】发现距上次成功监测已超过 26 小时，正在自动补跑一轮……")
                    run_scheduled_monitor(background=True)


def reschedule():
    """设置页修改时间/开关后调用。"""
    if _scheduler is not None:
        _schedule_job()


def next_run_time():
    """下次自动监测时间（ISO 字符串或 None）；周期>1 时按上次执行日期+周期计算。"""
    if not _effective_enabled():
        return None
    hour, minute = _parse_time(_effective_time(), fallback=(8, 30))
    now = datetime.now()
    interval = effective_interval_days()
    candidate_date = now.date()
    if interval > 1:
        last = _last_run_date()
        if last is not None:
            due = last + timedelta(days=interval)
            if due > now.date():
                candidate_date = due
    candidate = datetime(candidate_date.year, candidate_date.month, candidate_date.day,
                         hour, minute)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M:%S")
