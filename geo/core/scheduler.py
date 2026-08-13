"""APScheduler 定时自动监测：默认每日 08:30、可修改、错过 26 小时自动补跑。"""

import threading
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


def _last_done_task(s) -> database.MonitorTask:
    return (s.query(database.MonitorTask)
            .filter(database.MonitorTask.status == "done")
            .order_by(database.MonitorTask.finished_at.desc()).first())


def run_scheduled_monitor(background: bool = True):
    """跑一轮定时监测：参加每日自动监测的品牌逐个串行监测（02d 3.1.4）。

    全局同一时刻只有一轮监测（any_task_running 语义不变）：手动监测进行中
    定时触发即跳过本轮；跨品牌也不并发（同一进程同一批钥匙，串行等待）。
    """

    def _do():
        from geo.core import monitor_task

        with database.session_scope() as s:
            if monitor_task.any_task_running(s):
                print("【定时监测】已经有一轮监测在跑了，本轮自动监测跳过，"
                      "等它结束、下次定时时间再跑。")
                return
            brands = [b.to_dict() for b in
                      (s.query(database.BrandProfile)
                       .filter(database.BrandProfile.auto_monitor == True)
                       .order_by(database.BrandProfile.id.asc()).all())]
            web_mode = bool(database.get_setting("schedule_web_mode", False))

        active = [b for b in brands if (b.get("brand_name") or "").strip()]
        if not active:
            print("【定时监测】还没有任何品牌参加每日自动监测（可以在设置页打开），本轮已跳过。")
            return

        names = "、".join(f"「{b['brand_name']}」" for b in active)
        mode_label = "联网提问" if web_mode else "常规提问"
        print(f"【定时监测】本轮用{mode_label}监测 {len(active)} 个品牌：先测{names}。")

        for b in active:
            brand_id = b["id"]
            brand_name = b["brand_name"]
            # 发起前复查全局互斥：手动监测若恰好在串行间隙发起，则本轮到此为止
            with database.session_scope() as s:
                if monitor_task.any_task_running(s):
                    print("【定时监测】有一轮监测正在跑，本轮到此为止，下次定时时间再继续。")
                    return
                qids = [q.id for q in s.query(database.QuestionBank)
                        .filter(database.QuestionBank.brand_id == brand_id,
                                database.QuestionBank.enabled == True).all()]

            if web_mode:
                engines = monitor_task.web_auto_engines()
            else:
                engines = monitor_task.enabled_auto_engines()
            if not qids:
                print(f"【定时监测】「{brand_name}」的问题库是空的，跳过它，继续下一个品牌。")
                continue
            if not engines:
                if web_mode:
                    print(f"【定时监测】「{brand_name}」联网提问的引擎钥匙还没填"
                          "（豆包、Kimi、通义千问、腾讯元宝至少一家），跳过它，继续下一个品牌。")
                else:
                    print(f"【定时监测】「{brand_name}」的引擎钥匙还没填，"
                          "跳过它，继续下一个品牌。请到设置页填写钥匙。")
                continue
            try:
                task_id = monitor_task.start_monitor_task(
                    qids, engines, task_type="scheduled", brand_id=brand_id,
                    mode="web" if web_mode else "normal")
            except engine_base.EngineError as e:
                print(f"【定时监测】「{brand_name}」发起失败：{e.message}，跳过它，继续下一个品牌。")
                continue
            print(f"【定时监测】「{brand_name}」开始（任务 {task_id}），等它跑完再测下一个……")
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


def ensure_scheduler_started():
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return
        _scheduler = BackgroundScheduler()
        _schedule_job()
        _scheduler.start()
        _recover_and_catchup()


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
            if missed and not database.get_setting(_CATCHUP_KEY, False):
                database.set_setting(_CATCHUP_KEY, True)
                if last is None:
                    # 全新库：从未监测过，不自动补跑（避免首启产生费用），仅给一句提示
                    print("【定时监测】还没有成功监测过任何一轮：暂不自动补跑，"
                          "填好钥匙后可以随时手动发起，或等每天的定时时间自动开跑。")
                else:
                    print("【定时监测】发现距上次成功监测已超过 26 小时，正在自动补跑一轮……")
                    run_scheduled_monitor(background=True)


def reschedule():
    """设置页修改时间/开关后调用。"""
    if _scheduler is not None:
        _schedule_job()


def next_run_time():
    """下次自动监测时间（ISO 字符串或 None）。"""
    if _scheduler is None:
        return None
    if not _effective_enabled():
        return None
    job = _scheduler.get_job("daily_monitor")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    return None
