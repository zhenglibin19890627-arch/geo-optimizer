import os, sys
sys.path.insert(0, r"C:\Users\zlb19\Desktop\GEO")
os.environ["GEO_NO_SCHEDULER"] = "1"

from geo.models import db as database
from geo.analyzers import alerting

# 用临时内存库避免污染正式数据
import sqlite3, tempfile
tmp = tempfile.mktemp(suffix=".db")
database.engine.dispose()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
database.engine = create_engine(f"sqlite:///{tmp}", connect_args={"check_same_thread": False})
database.SessionLocal = sessionmaker(bind=database.engine, expire_on_commit=False)
database.init_db()

def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} | {detail}")
    if not cond:
        raise SystemExit(1)

print("========== 预警规则纯函数验证（技术方案第十章） ==========")

# 场景1：轮次不足（<2 轮基线）不预警
with database.session_scope() as s:
    alerts = alerting.evaluate_round(s, [], {"mention_rate": 0.5, "net_sentiment": 0.5, "score": 80,
                                             "mentioned_count": 5, "total_answers": 10}, 1)
check("不足3轮不预警", len(alerts) == 0, f"alerts={len(alerts)}")

# 场景2：基线正常（mention 60%），当前骤降到 10% → 触发 mention_drop
baseline = []
for i in range(2):
    baseline.append({"mention_rate": 0.6, "net_sentiment": 0.5, "score": 85, "mentioned_count": 6})
with database.session_scope() as s:
    alerts = alerting.evaluate_round(s, baseline,
        {"mention_rate": 0.1, "net_sentiment": 0.5, "score": 80, "mentioned_count": 1, "total_answers": 10}, 2)
    print("场景2 触发 alerts:", [(a.alert_type, a.level, a.message) for a in alerts])
    check("提及率骤降触发 watch", len(alerts) >= 1 and alerts[0].alert_type == "mention_drop" and alerts[0].level == "watch",
          f"count={len(alerts)}")

# 场景3：连续两轮下降 → 升级为 warning（正式预警）
with database.session_scope() as s:
    alerts = alerting.evaluate_round(s, baseline,
        {"mention_rate": 0.08, "net_sentiment": 0.5, "score": 80, "mentioned_count": 1, "total_answers": 10}, 3)
    print("场景3 升级 alerts:", [(a.alert_type, a.level) for a in alerts])
    check("连续下降升级 warning", any(a.alert_type == "mention_drop" and a.level == "warning" for a in alerts),
          f"alerts={[(a.alert_type, a.level) for a in alerts]}")

# 场景4：恢复 → 关闭预警
with database.session_scope() as s:
    alerts = alerting.evaluate_round(s, baseline,
        {"mention_rate": 0.65, "net_sentiment": 0.5, "score": 86, "mentioned_count": 6, "total_answers": 10}, 4)
    print("场景4 恢复 alerts:", [(a.alert_type, a.level, a.message[:30]) for a in alerts])
    check("恢复通知", any("恢复" in (a.message or "") for a in alerts), f"alerts={[(a.alert_type, a.level) for a in alerts]}")

# 场景5：小基数不误报（mention 从 100% 单轮到 0，但只有 1 条回答）
with database.session_scope() as s:
    alerts = alerting.evaluate_round(s, [{"mention_rate": 1.0, "net_sentiment": 1.0, "score": 90, "mentioned_count": 1}],
        {"mention_rate": 0.0, "net_sentiment": 0.0, "score": 40, "mentioned_count": 0, "total_answers": 1}, 5)
    check("小基数防误报(双条件)", len(alerts) == 0, f"alerts={len(alerts)}")

print()
print("========== 预警规则验证完成 ==========")
