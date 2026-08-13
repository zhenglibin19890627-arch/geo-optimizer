"""异常预警判定（技术方案第十章：双条件 + 二次确认 + 冷却期 + 恢复通知）。

品牌化（02d 3.1.5，后端 C 批）：基线只取该品牌该模式本轮之前的正常轮次；
设置键按品牌后缀 `alert_active_<type>_b<brand_id>` / `alert_warn_at_<type>_b<brand_id>`
（新键；旧键保留不迁移）；文案强制含品牌名。13.3 已修复的"同事务 session
写入"模式保持不变（仅键名与取数范围变化）。
"""

from datetime import datetime, timedelta

from geo.models import db as database

ALERT_TYPES = [
    {"key": "mention_drop", "label": "品牌提及率下降",
     "metric": "mention_rate", "metric_label": "提及率"},
    {"key": "sentiment_drop", "label": "情感恶化",
     "metric": "net_sentiment", "metric_label": "净情感率"},
    {"key": "score_drop", "label": "综合评分下滑",
     "metric": "score", "metric_label": "AI 可见度评分"},
]

COOLDOWN_DAYS = 7


def _active_key(alert_type: str, brand_id: int) -> str:
    return f"alert_active_{alert_type}_b{brand_id}"


def _warn_at_key(alert_type: str, brand_id: int) -> str:
    return f"alert_warn_at_{alert_type}_b{brand_id}"


def _baseline(recent: list) -> dict:
    """最近 5 轮（不足则取全部）各项指标的平均值。"""
    if not recent:
        return {}
    n = len(recent)
    pool = recent[-5:] if n > 5 else recent
    result = {}
    for key in ("mention_rate", "net_sentiment", "score", "mentioned_count"):
        values = [r.get(key) for r in pool if r.get(key) is not None]
        if values:
            result[key] = sum(values) / len(values)
    return result


def _is_triggered(metric_key: str, current, base) -> bool:
    if current is None or base is None or base is None:
        return False
    if metric_key == "mention_rate":
        # 双条件：相对下降 ≥30% 且绝对下降 ≥5 个百分点（防小基数误报）
        if base <= 0:
            return False
        return current <= base * 0.7 and (base - current) >= 0.05
    if metric_key == "net_sentiment":
        return base - current >= 0.20
    if metric_key == "score":
        return base - current >= 15
    return False


def _is_recovered(metric_key: str, current, base) -> bool:
    """回到基线 -10% 以内视为恢复。"""
    if current is None or base is None:
        return False
    return current >= base * 0.9


def _msg(alert_type: str, current, base, brand_name: str,
         cur_count=None, base_count=None, recovered=False):
    brand = f"**{brand_name}**" if brand_name else "你品牌"
    if recovered:
        names = {t["key"]: t for t in ALERT_TYPES}
        label = names[alert_type]["label"]
        if names[alert_type]["metric"] == "score":
            return (f"好消息：{brand}的{label}已经恢复（从 {base:.0f} 分回到了 {current:.0f} 分），"
                    f"这个预警已关闭。")
        b = round((base or 0) * 100)
        c = round((current or 0) * 100)
        return (f"好消息：{brand}的{label}已经恢复（从 {b}% 回到了 {c}%），"
                f"这个预警已关闭。")
    if alert_type == "mention_drop":
        b = round(base_count or (base or 0) * 10)
        c = round(cur_count or (current or 0) * 10)
        return (f"注意：{brand}被 AI 提到的次数明显变少了（上一段时间平均每轮被提到 {b} 次，"
                f"这轮只有 {c} 次）。建议到「监测报告」看看具体是哪些问题没提到它。")
    if alert_type == "sentiment_drop":
        return (f"注意：AI 对{brand}的评价变差了（上一段时间平均净情感率 {base * 100:.0f}%，"
                f"这轮只有 {current * 100:.0f}%）。建议到「监测报告」看看哪些回答的口气不太友好。")
    return (f"注意：{brand}的 AI 可见度评分下滑了（上一段时间平均 {base:.0f} 分，"
            f"这轮只有 {current:.0f} 分）。建议去「内容优化建议」页看看有什么能改的。")


def evaluate_round(session, rounds_before: list, current: dict, current_round_id: int,
                   brand_id: int = 1, brand_name: str = "") -> list:
    """在一轮监测结束后调用。

    rounds_before: 该品牌该模式本轮之前的历史正常轮次指标
                   [{mention_rate, net_sentiment, score, mentioned_count}]（时间升序）
    current: 本轮指标 {mention_rate, net_sentiment, score, mentioned_count, total_answers}
    brand_id / brand_name: 归属品牌（设置键按品牌后缀、文案强制含品牌名）
    returns: 本次新增的 Alert 对象列表（已加入 session）。
    """
    if len(rounds_before) < 2:
        return []  # 不足 3 轮（含本轮）不预警
    base = _baseline(rounds_before)
    created = []
    for t in ALERT_TYPES:
        metric_key = t["metric"]
        cur_val = current.get(metric_key)
        base_val = base.get(metric_key)
        if cur_val is None or base_val is None:
            continue

        active = database.get_setting(_active_key(t["key"], brand_id), False, session=session)
        if not _is_triggered(metric_key, cur_val, base_val):
            # 恢复检查
            if active and _is_recovered(metric_key, cur_val, base_val):
                alert = database.Alert(
                    round_id=current_round_id, brand_id=brand_id, alert_type=t["key"],
                    level="watch",
                    message=_msg(t["key"], cur_val, base_val, brand_name, recovered=True))
                session.add(alert)
                created.append(alert)
                database.set_setting(_active_key(t["key"], brand_id), False, session=session)
            continue

        # 触发了
        if not active:
            alert = database.Alert(
                round_id=current_round_id, brand_id=brand_id, alert_type=t["key"],
                level="watch",
                message=_msg(t["key"], cur_val, base_val, brand_name,
                             cur_count=current.get("mentioned_count"),
                             base_count=base.get("mentioned_count")))
            session.add(alert)
            created.append(alert)
            database.set_setting(_active_key(t["key"], brand_id), True, session=session)
            continue

        # 上一轮已触发过（watch 或 warning）：升级为正式预警，但 7 天冷却期内不重复推
        warn_at = database.get_setting(_warn_at_key(t["key"], brand_id), None, session=session)
        if warn_at:
            try:
                last_warn = datetime.fromisoformat(str(warn_at))
                if datetime.now() - last_warn < timedelta(days=COOLDOWN_DAYS):
                    continue  # 冷却期内
            except Exception:
                pass
        alert = database.Alert(
            round_id=current_round_id, brand_id=brand_id, alert_type=t["key"],
            level="warning",
            message=_msg(t["key"], cur_val, base_val, brand_name,
                         cur_count=current.get("mentioned_count"),
                         base_count=base.get("mentioned_count")))
        session.add(alert)
        created.append(alert)
        database.set_setting(_warn_at_key(t["key"], brand_id),
                             datetime.now().isoformat(), session=session)
        database.set_setting(_active_key(t["key"], brand_id), True, session=session)
    return created
