# -*- coding: utf-8 -*-
"""第三轮回归 · 缺陷 #8 修正断言复核 v2（环境 A 终态数据，端口 5097）

对首跑 2 处脚本预期误判的独立复核（复核对象=终态库，冷却轮行为以 round_id=6 为界甄别）：
1. 冷却轮（round 6）：已升级类型 mention_drop/score_drop 在 round6 零新增；
   sentiment_drop 因前两轮净情感率 1.0 未达阈值、从未触发，round6 首触 watch 为正确行为（非冷却期违约）
2. /api/report/trend 返回 {labels, values} 对象，7 个点
"""
import io, json, sys, sqlite3, datetime, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5097"
DB = r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_reg3_fix8_20260810\data\geo.db"
OUTTXT = r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg3a_verify_5097.txt"
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

def call(method, path):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

conn = sqlite3.connect(DB, timeout=30)
cur = conn.cursor()

print("== 1. 冷却轮（round 6）已升级类型不重复推 ==")
cur.execute("SELECT alert_type, level, round_id FROM alert ORDER BY id")
rows = cur.fetchall()
round6_rows = [r for r in rows if r[2] == 6]
check("round6 仅新增 sentiment_drop watch 1 条（其余类型零新增）",
      len(round6_rows) == 1 and round6_rows[0][0] == "sentiment_drop" and round6_rows[0][1] == "watch",
      f"rows={round6_rows}")
md6 = [r for r in rows if r[0] == "mention_drop" and r[2] == 6]
sd6 = [r for r in rows if r[0] == "score_drop" and r[2] == 6]
check("mention_drop 在 round6 零新增（冷却生效）", len(md6) == 0, f"rows={md6}")
check("score_drop 在 round6 零新增（冷却生效）", len(sd6) == 0, f"rows={sd6}")
check("冷却轮无任何新 warning", not any(r[2] == 6 and r[1] == "warning" for r in rows))
check("mention_drop 链（round4 watch→round5 warning→round7 恢复）",
      [x[2] for x in rows if x[0] == "mention_drop"] == [4, 5, 7])
cur.execute("SELECT value FROM settings WHERE key='alert_warn_at_mention_drop'")
warn_at = cur.fetchone()
check("mention_drop warn_at 仍为 round5 写入值（冷却轮未改写）",
      warn_at is not None and json.loads(warn_at[0]) == "2026-08-10T10:42:11.492614", f"warn_at={warn_at}")
cur.execute("SELECT value FROM settings WHERE key='alert_active_mention_drop'")
active = cur.fetchone()
check("mention_drop active 终态=False（round7 恢复轮写入）",
      active is not None and json.loads(active[0]) is False, f"active={active}")

print("== 2. 趋势接口结构与点数 ==")
r = call("GET", "/api/report/trend")
trend = r["data"]
check("trend 返回对象含 labels/values", isinstance(trend, dict) and "labels" in trend and "values" in trend,
      f"keys={list(trend.keys())}")
check("values 7 点（3种子+4真实）", len(trend["values"]) == 7, f"values={trend['values']}")
check("labels 7 个（第1轮..第7轮）", len(trend["labels"]) == 7, f"labels={trend['labels']}")

print("== 3. 终态再确认 ==")
r = call("GET", "/api/overview")
check("overview round_count=7", r["data"].get("round_count") == 7, f"round_count={r['data'].get('round_count')}")

conn.close()
ok_n = sum(1 for x in results if x[1])
with open(OUTTXT, "w", encoding="utf-8") as f:
    f.write(f"第三轮回归 · 缺陷 #8 修正断言复核 v2（环境A 端口5097）\n时间：{datetime.datetime.now().isoformat()}\n")
    f.write(f"SUMMARY: {ok_n} / {len(results)} PASS\n")
    for name, ok, det in results:
        f.write(("PASS " if ok else "FAIL ") + name + (" | " + det if det else "") + "\n")
print(f"\n==== 汇总: {ok_n}/{len(results)} PASS ====")
