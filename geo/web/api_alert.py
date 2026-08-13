"""预警与定时任务接口：预警列表/已读、定时设置（#22~#25）。"""

import re

from flask import Blueprint, request

from geo.core import scheduler
from geo.models import db as database
from geo.web import ApiError, current_brand_id, get_json, ok

bp = Blueprint("api_alert", __name__)

PLAIN_HINT = "想让系统每天自动监测，请保持本程序开着（可以最小化窗口，但不要关掉它）"


# ---------------- #22 预警列表 ----------------
@bp.route("/alerts", methods=["GET"])
def alerts_list():
    brand_id = current_brand_id()
    unread = request.args.get("unread")
    with database.session_scope() as s:
        q = (s.query(database.Alert)
             .filter(database.Alert.brand_id == brand_id)
             .order_by(database.Alert.id.desc()))
        if unread is not None:
            q = q.filter(database.Alert.is_read == (unread == "false"))
        q = q.limit(200)
        items = [a.to_dict() for a in q.all()]
        unread_count = (s.query(database.Alert)
                        .filter(database.Alert.is_read == False)
                        .filter(database.Alert.brand_id == brand_id).count())
        return ok({"items": items, "unread_count": unread_count}, "获取成功")


# ---------------- #23 标记已读 ----------------
@bp.route("/alerts/<int:alert_id>/read", methods=["POST"])
def alert_read(alert_id):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        row = s.get(database.Alert, alert_id)
        if not row:
            raise ApiError("这条预警不存在，可能已被清理")
        if (row.brand_id or 1) != brand_id:
            raise ApiError("这条预警不属于当前品牌")
        row.is_read = True
    return ok(None, "已标记为已读")


# ---------------- #24 定时任务设置（读） ----------------
@bp.route("/schedule", methods=["GET"])
def schedule_get():
    enabled = bool(database.get_setting("schedule_enabled", True))
    time_str = str(database.get_setting("schedule_time", "08:30"))
    web_mode = bool(database.get_setting("schedule_web_mode", False))
    return ok({
        "enabled": enabled,
        "time": time_str,
        "web_mode": web_mode,
        "next_run_time": scheduler.next_run_time(),
        "plain_hint": PLAIN_HINT,
    }, "获取成功")


# ---------------- #25 定时任务设置（写） ----------------
@bp.route("/schedule", methods=["PUT"])
def schedule_put():
    data = get_json()
    if "enabled" in data:
        database.set_setting("schedule_enabled", bool(data["enabled"]))
    if "web_mode" in data:
        database.set_setting("schedule_web_mode", bool(data["web_mode"]))
    if data.get("time"):
        time_str = str(data["time"]).strip()
        if not re.match(r"^\d{1,2}:\d{2}$", time_str):
            raise ApiError("时间格式不对，请填 24 小时制的「时:分」，例如 08:30")
        hour, minute = time_str.split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ApiError("时间不在有效范围内（小时 0-23，分钟 0-59），请检查后重填")
        database.set_setting("schedule_time", f"{int(hour):02d}:{int(minute):02d}")
    scheduler.reschedule()
    return schedule_get()
