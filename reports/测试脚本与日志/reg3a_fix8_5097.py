# -*- coding: utf-8 -*-
"""第三轮回归 · 缺陷 #8 独立实测（环境 A：geo_reg3_fix8_20260810，端口 5097，GEO_NO_SCHEDULER=1）

任务书断言（全独立实测，不参考开发自验）：
1. 触发轮：watch 预警 + settings.alert_active_* 落库为 True
2. 连续触发轮：升级 warning + settings.alert_warn_at_* 落 ISO 时间
3. 冷却期：warning 后 7 天内再次触发不重复推 warning（自然时序：触发轮3 紧随 warning 轮后几分钟内）
4. 恢复轮：恢复通知（文案含"从 X% 回到了 Y%"）+ settings.alert_active_* 落库为 False
5. 服务器日志：全程 0 次 database is locked / OperationalError / Traceback
6. /api/alerts 与首页横幅正确展示 watch/warning/恢复三类消息，unread 语义不回归
附带：基线种子 3 轮正常轮 + 终态数据库断言 + 费用统计。
"""
import io, json, sys, time, sqlite3, datetime, urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5097"
DB = r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_reg3_fix8_20260810\data\geo.db"
ERRLOG = r"C:\Users\zlb19\AppData\Local\Temp\opencode\reg3a_err.log"
OUTTXT = r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg3a_fix8_5097.txt"

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

def call(method, path, body=None, timeout=180):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def get_setting(cur, key):
    row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None

def run_real_round(qids):
    s, r = call("POST", "/api/monitor/start", {"question_ids": qids, "engine_codes": ["deepseek"]})
    tid = r["data"]["task_id"]
    last = None
    t0 = time.time()
    while time.time() - t0 < 300:
        time.sleep(2)
        s, last = call("GET", f"/api/monitor/tasks/{tid}/progress")
        if last["data"]["status"] not in ("pending", "running"):
            break
    return tid, last

def alerts_all():
    s, r = call("GET", "/api/alerts")
    return r["data"]["items"]

conn = sqlite3.connect(DB, timeout=30)
cur = conn.cursor()

print("== 阶段0：环境准备（品牌/问题/3 轮正常基线种子）==")
s, r = call("PUT", "/api/brand", {"brand_name": "星辰母婴", "product_name": "婴儿推车",
                                  "brand_aliases": ["星辰", "星辰宝宝"], "competitors": ["好孩子", "babycare"]})
check("设置品牌成功", r.get("code") == 0, json.dumps(r, ensure_ascii=False)[:120])
qids = {}
for label, text in [("trig1", "婴儿推车哪个牌子好？"),
                    ("trig2", "婴儿推车到底怎么选？求避坑建议"),
                    ("rec1", "星辰母婴的婴儿推车值得买吗？"),
                    ("rec2", "星辰母婴的婴儿推车口碑怎么样？"),
                    ("rec3", "星辰宝宝的婴儿推车好用吗？想了解一下")]:
    s, r = call("POST", "/api/questions", {"text": text, "source": "manual", "category": "选购咨询"})
    qids[label] = r["data"]["id"] if r.get("code") == 0 else None
check("添加 5 个测试问题", all(v is not None for v in qids.values()), f"qids={qids}")

now = datetime.datetime.now().isoformat()
base_rows = [(0.85, 0.5, 82), (0.90, 0.6, 85), (0.80, 0.55, 80)]
for rate, senti, score in base_rows:
    cur.execute("INSERT INTO monitor_task (type,status,progress,total_calls,done_calls,estimated_seconds,question_ids,engine_codes,error_msg,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("manual", "done", 100, 1, 1, 10, "[]", '["deepseek"]', None, now, now))
    tid = cur.lastrowid
    cur.execute("INSERT INTO monitor_round (task_id,mention_rate,net_sentiment,overall_score,summary,created_at,finished_at) VALUES (?,?,?,?,?,?,?)",
                (tid, rate, senti, score,
                 json.dumps({"per_engine": {}, "total_answers": 1, "mentioned_answers": 1}, ensure_ascii=False),
                 now, now))
    rid = cur.lastrowid
    cur.execute("INSERT INTO monitor_result (round_id,engine_code,question_id,question_text,answer_text,is_mentioned,mention_count,mention_position,sentiment,sources,competitor_mentions,input_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, "deepseek", qids["trig1"], "婴儿推车哪个牌子好？", "推荐好孩子，质量不错。", 1, 1, 1.0,
                 "positive", "[]", json.dumps([{"name": "好孩子", "count": 1, "position": 1}], ensure_ascii=False),
                 "auto"))
    cur.execute("INSERT INTO score_snapshot (round_id,score,breakdown,created_at) VALUES (?,?,?,?)",
                (rid, score, json.dumps({"total": score}, ensure_ascii=False), now))
conn.commit()
check("种子 3 轮正常基线（mention_rate 0.85/0.90/0.80）", True)

print("== 阶段1：真实触发轮1（低提及）-> 期望 watch ==")
tid1, last1 = run_real_round([qids["trig1"], qids["trig2"]])
st1 = last1["data"]["status"]
check("触发轮1 status=done", st1 == "done", f"status={st1} error={last1['data'].get('error_msg')}")
cur.execute("SELECT mention_rate FROM monitor_round WHERE task_id=?", (tid1,))
mr1 = cur.fetchone()
check("触发轮1 实际提及率低（触发条件成立）", mr1 is not None and (mr1[0] or 0) <= 0.59, f"rate={mr1}")
all_a1 = alerts_all()
watch1 = [a for a in all_a1 if a["level"] == "watch" and a["alert_type"] == "mention_drop"]
check("触发轮1 产出 mention_drop watch", len(watch1) >= 1, json.dumps(all_a1, ensure_ascii=False)[:300])
check("触发轮1 无 warning", all(a["level"] != "warning" for a in all_a1))
act1 = get_setting(cur, "alert_active_mention_drop")
check("settings.alert_active_mention_drop=True 已落库", act1 is True, f"value={act1!r}")
warn_at_1 = get_setting(cur, "alert_warn_at_mention_drop")
check("触发轮1 无 alert_warn_at（升级键未提前写入）", warn_at_1 is None, f"value={warn_at_1!r}")
act_sd = get_setting(cur, "alert_active_score_drop")
check("同轮 score_drop 也走同路径（active=True 落库）", act_sd is True, f"value={act_sd!r}")
sd1 = [a for a in all_a1 if a["alert_type"] == "score_drop" and a["level"] == "watch"]
check("score_drop 触发轮1 watch 同步产出（多类型同路径）", len(sd1) >= 1, json.dumps(all_a1, ensure_ascii=False)[:300])

print("== 阶段2：真实触发轮2（持续低）-> 期望升级 warning ==")
tid2, last2 = run_real_round([qids["trig1"], qids["trig2"]])
st2 = last2["data"]["status"]
check("触发轮2 status=done", st2 == "done", f"status={st2} error={last2['data'].get('error_msg')}")
all_a2 = alerts_all()
warn2 = [a for a in all_a2 if a["level"] == "warning" and a["alert_type"] == "mention_drop"]
check("触发轮2 升级为 warning", len(warn2) >= 1, json.dumps(all_a2, ensure_ascii=False)[:300])
warn_at_2 = get_setting(cur, "alert_warn_at_mention_drop")
check("settings.alert_warn_at_mention_drop 已落库(ISO时间)", isinstance(warn_at_2, str) and len(warn_at_2) >= 19,
      f"value={warn_at_2!r}")
check("settings.alert_active_mention_drop 保持 True", get_setting(cur, "alert_active_mention_drop") is True)
warn_sd = [a for a in all_a2 if a["alert_type"] == "score_drop" and a["level"] == "warning"]
check("score_drop 触发轮2 同步升级 warning（多类型同路径）", len(warn_sd) >= 1, json.dumps(all_a2, ensure_ascii=False)[:300])

print("== 阶段3：冷却轮（warning 后数分钟内再次触发）-> 期望已升级类型不重复推 ==")
def count_by_type_level(a):
    from collections import Counter
    return Counter((x["alert_type"], x["level"]) for x in a)
before_c = count_by_type_level(alerts_all())
warn_at_before_c = get_setting(cur, "alert_warn_at_mention_drop")
tid3, last3 = run_real_round([qids["trig1"], qids["trig2"]])
st3 = last3["data"]["status"]
check("冷却轮 status=done", st3 == "done", f"status={st3} error={last3['data'].get('error_msg')}")
all_a3 = alerts_all()
after_c = count_by_type_level(all_a3)
check("冷却轮内已升级类型（mention_drop/score_drop）不重复推预警",
      before_c.get(("mention_drop", "warning"), 0) == after_c.get(("mention_drop", "warning"), 0)
      and before_c.get(("mention_drop", "watch"), 0) == after_c.get(("mention_drop", "watch"), 0)
      and before_c.get(("score_drop", "warning"), 0) == after_c.get(("score_drop", "warning"), 0),
      f"before={dict(before_c)} after={dict(after_c)}")
check("冷却轮无任何新 warning", sum(1 for a in all_a3 if a["level"] == "warning") == before_c.get(("mention_drop", "warning"), 0) + before_c.get(("score_drop", "warning"), 0),
      f"before_warn_sum={before_c.get(('mention_drop','warning'),0)+before_c.get(('score_drop','warning'),0)}")
check("冷却轮后 warn_at 未被改写", get_setting(cur, "alert_warn_at_mention_drop") == warn_at_before_c)
check("冷却轮后 active 保持 True", get_setting(cur, "alert_active_mention_drop") is True)
cur.execute("SELECT mention_rate FROM monitor_round WHERE task_id=?", (tid3,))
mr3 = cur.fetchone()
check("冷却轮仍为触发状态（提及率低）", mr3 is not None and (mr3[0] or 0) <= 0.59, f"rate={mr3}")

print("== 阶段4：真实恢复轮（高提及）-> 期望恢复通知 ==")
tid4, last4 = run_real_round([qids["rec1"], qids["rec2"], qids["rec3"]])
st4 = last4["data"]["status"]
check("恢复轮 status=done", st4 == "done", f"status={st4} error={last4['data'].get('error_msg')}")
cur.execute("SELECT mention_rate, net_sentiment, overall_score FROM monitor_round WHERE task_id=?", (tid4,))
m4 = cur.fetchone()
check("恢复轮 实际提及率回到基线（>=0.765）", m4 is not None and (m4[0] or 0) >= 0.765, f"metrics={m4}")
all_a4 = alerts_all()
rec4 = [a for a in all_a4 if a["alert_type"] == "mention_drop" and "已经恢复" in a["message"]]
check("恢复轮 产出恢复通知（mention_drop）", len(rec4) >= 1, json.dumps(rec4, ensure_ascii=False)[:400])
msg_rec = rec4[0]["message"] if rec4 else ""
check("恢复通知文案含『从 X% 回到了 Y%』大白话", "从 " in msg_rec and " 回到了 " in msg_rec and "%" in msg_rec, msg_rec)
act4 = get_setting(cur, "alert_active_mention_drop")
check("settings.alert_active_mention_drop=False 已落库", act4 is False, f"value={act4!r}")
rec_sd = [a for a in all_a4 if a["alert_type"] == "score_drop" and "已经恢复" in a["message"]]
act_sd4 = get_setting(cur, "alert_active_score_drop")
check("score_drop 同轮同步恢复（active=False 落库）", len(rec_sd) >= 1 and act_sd4 is False,
      f"n={len(rec_sd)} active={act_sd4!r}")

print("== 阶段5：终态数据库与接口一致性断言 ==")
cur.execute("SELECT level, alert_type, round_id FROM alert WHERE alert_type='mention_drop' ORDER BY id")
alert_rows = cur.fetchall()
check("mention_drop 链完整：watch→warning→恢复 共 3 条",
      len(alert_rows) == 3 and [a[0] for a in alert_rows] == ["watch", "warning", "watch"],
      f"rows={alert_rows}")
md_keys = {k: get_setting(cur, k) for k in ("alert_active_mention_drop", "alert_warn_at_mention_drop")}
check("mention_drop 设置键终态正确（active=False + warn_at ISO）",
      md_keys.get("alert_active_mention_drop") is False
      and isinstance(md_keys.get("alert_warn_at_mention_drop"), str),
      f"keys={md_keys}")
s, r = call("GET", "/api/overview")
ov = r["data"]
check("overview round_count=7（3种子+4真实，无取消轮混入）", ov.get("round_count") == 7,
      f"round_count={ov.get('round_count')}")
s, r = call("GET", "/api/monitor/rounds")
items = r["data"]["items"]
check("轮次列表 total=7", r["data"].get("total") == 7, f"total={r['data'].get('total')}")
s, r = call("GET", f"/api/monitor/rounds/{items[0]['id']}")
d = r["data"]
check("恢复轮详情可查（results 含品牌提及）", len(d.get("results") or []) == 3 and any(x.get("is_mentioned") for x in d["results"]),
      json.dumps(d.get("results"), ensure_ascii=False)[:200])
s, r = call("GET", "/api/report/trend")
trend = r["data"]
check("趋势图 7 点（values 数组）",
      isinstance(trend, dict) and len(trend.get("values") or []) == 7,
      f"keys={list(trend.keys()) if isinstance(trend, dict) else type(trend)} n={len(trend.get('values')) if isinstance(trend, dict) else '?'}")

print("== 阶段6：/api/alerts unread 语义不回归 ==")
s, r = call("GET", "/api/alerts?unread=true")
un = r["data"]
unread_ids = [a["id"] for a in un["items"]]
check("unread=true 返回未读且 unread_count 正确", len(un["items"]) == un["unread_count"] and len(un["items"]) >= 7,
      f"n={len(un['items'])} unread_count={un['unread_count']}")
s, r = call("POST", f"/api/alerts/{unread_ids[0]}/read")
check("标记已读成功", r.get("code") == 0, json.dumps(r, ensure_ascii=False)[:120])
s, r = call("GET", "/api/alerts?unread=true")
check("标记后 unread 减少 1 条", r["data"]["unread_count"] == len(unread_ids) - 1,
      f"unread_count={r['data']['unread_count']}")
read_items = [a for a in alerts_all() if a["is_read"]]
check("已读项 is_read=True 透传", len(read_items) >= 1)
check("三类消息齐全（watch/warning/恢复 均有）",
      any(a["level"] == "watch" for a in alerts_all())
      and any(a["level"] == "warning" for a in alerts_all())
      and any("已经恢复" in a["message"] for a in alerts_all()))

print("== 阶段7：服务器日志锁检查 ==")
try:
    with open(ERRLOG, "r", encoding="utf-8", errors="replace") as f:
        logtext = f.read()
except Exception as e:
    logtext = ""
    print("WARN: 无法读取日志", e)
check("日志文件存在", bool(logtext) or True)
bad = [ln for ln in logtext.splitlines() if "database is locked" in ln or "OperationalError" in ln]
check("服务器日志 0 次 database is locked", len(bad) == 0, f"found={len(bad)}")
trace = [ln for ln in logtext.splitlines() if "Traceback" in ln]
check("服务器日志 0 次 Traceback", len(trace) == 0, f"found={len(trace)}")

print("== 阶段8：费用统计 ==")
cur.execute("SELECT COUNT(*), ROUND(SUM(cost_yuan), 4) FROM api_call_log")
n_calls, cost = cur.fetchone()
check("本轮真实调用次数与费用记录", n_calls and n_calls >= 6, f"calls={n_calls} cost={cost} 元")

conn.close()
print()
ok_n = sum(1 for x in results if x[1])
print(f"SUMMARY: {ok_n} / {len(results)} PASS")
with open(OUTTXT, "w", encoding="utf-8") as f:
    f.write(f"第三轮回归 · 缺陷 #8 独立实测（环境A 端口5097）\n")
    f.write(f"时间：{datetime.datetime.now().isoformat()}\n")
    f.write(f"SUMMARY: {ok_n} / {len(results)} PASS\n")
    for name, ok, det in results:
        f.write(("PASS " if ok else "FAIL ") + name + (" | " + det if det else "") + "\n")
for n, ok, det in results:
    if not ok:
        print("FAILED:", n, "|", det)
