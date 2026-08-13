"""预警规则单测（geo/analyzers/alerting.py，五场景，参照验收脚本 test_alerting.py）。

使用 conftest 的 alert_db 临时库，不碰正式 data/geo.db。
"""

from geo.analyzers import alerting


def _baseline_normal():
    return [
        {"mention_rate": 0.6, "net_sentiment": 0.5, "score": 85, "mentioned_count": 6},
        {"mention_rate": 0.6, "net_sentiment": 0.5, "score": 85, "mentioned_count": 6},
    ]


def _current(mention_rate=0.5, net_sentiment=0.5, score=80,
             mentioned_count=5, total_answers=10):
    return {"mention_rate": mention_rate, "net_sentiment": net_sentiment,
            "score": score, "mentioned_count": mentioned_count,
            "total_answers": total_answers}


def test_轮次不足不预警(alert_db):
    with alert_db.session_scope() as s:
        alerts = alerting.evaluate_round(
            s, [], _current(), 1)
        assert len(alerts) == 0


def test_提及率骤降触发watch(alert_db):
    with alert_db.session_scope() as s:
        alerts = alerting.evaluate_round(
            s, _baseline_normal(), _current(mention_rate=0.1, mentioned_count=1), 2)
        assert any(a.alert_type == "mention_drop" and a.level == "watch"
                   for a in alerts)


def test_连续下降升级warning(alert_db):
    # 依赖上一场景写下的 alert_active_* 状态：第二次触发同一类型应升级 warning
    with alert_db.session_scope() as s:
        alerts = alerting.evaluate_round(
            s, _baseline_normal(), _current(mention_rate=0.08, mentioned_count=1), 3)
        assert any(a.alert_type == "mention_drop" and a.level == "warning"
                   for a in alerts)


def test_恢复通知(alert_db):
    with alert_db.session_scope() as s:
        alerts = alerting.evaluate_round(
            s, _baseline_normal(), _current(mention_rate=0.65, mentioned_count=6), 4)
        assert any("恢复" in (a.message or "") for a in alerts)


def test_小基数不误报(alert_db):
    # 只有 1 条回答的轮次，单轮从 100% 到 0% 不触发（避免小样本误报）
    base = [{"mention_rate": 1.0, "net_sentiment": 1.0, "score": 90,
             "mentioned_count": 1}]
    with alert_db.session_scope() as s:
        alerts = alerting.evaluate_round(
            s, base, _current(mention_rate=0.0, net_sentiment=0.0,
                              score=40, mentioned_count=0, total_answers=1), 5)
        assert len(alerts) == 0


def test_文案含品牌名(alert_db):
    with alert_db.session_scope() as s:
        alerts = alerting.evaluate_round(
            s, _baseline_normal(),
            _current(mention_rate=0.1, mentioned_count=1), 6,
            brand_id=1, brand_name="威启")
        assert any("威启" in (a.message or "") for a in alerts)
