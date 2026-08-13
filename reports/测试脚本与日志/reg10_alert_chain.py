# -*- coding: utf-8 -*-
"""回归#10：真实预警链路。先在隔离库构造 2 轮高提及率基线（模拟历史正常轮，
再用真实监测轮（低提及率）触发 watch -> warning。全部走真实 evaluate_round。5095。"""
import io, json, sys, time, urllib.request, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5095"
DB = r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_reg_20260810\data\geo.db"

def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=60) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

# ---- 1. 构造基线：2 轮 done 轮次（mention_rate 0.8/0.9，score 80/85）----
conn = sqlite3.connect(DB)
cur = conn.cursor()
import datetime
now = datetime.datetime.now().isoformat()
base_rows = [
    (0.8, 0.5, 80, 1),
    (0.9, 0.6, 85, 1),
]
for rate, senti, score, mc in base_rows:
    cur.execute("INSERT INTO monitor_task (type,status,progress,total_calls,done_calls,estimated_seconds,question_ids,engine_codes,error_msg,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("manual", "done", 100, 1, 1, 10, "[38]", '["deepseek"]', None, now, now))
    tid = cur.lastrowid
    cur.execute("INSERT INTO monitor_round (task_id,mention_rate,net_sentiment,overall_score,summary,created_at,finished_at) VALUES (?,?,?,?,?,?,?)",
                (tid, rate, senti, score,
                 json.dumps({"per_engine": {"deepseek": {"total": 1, "mentioned": 1}},
                             "total_answers": 1, "mentioned_answers": 1}, ensure_ascii=False),
                 now, now))
    rid = cur.lastrowid
    cur.execute("INSERT INTO monitor_result (round_id,engine_code,question_id,question_text,answer_text,is_mentioned,mention_count,mention_position,sentiment,sources,competitor_mentions,input_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, "deepseek", 38, "婴儿推车什么牌子好？",
                 "推荐星辰母婴，婴儿推车质量好，也提到好孩子。", 1, 1, 1.0,
                 "positive", "[]", json.dumps([{"name": "好孩子", "count": 1, "position": 2}], ensure_ascii=False),
                 "auto"))
    cur.execute("INSERT INTO score_snapshot (round_id,score,breakdown,created_at) VALUES (?,?,?,?)",
                (rid, score, json.dumps({"total": score}, ensure_ascii=False), now))
conn.commit()
print("构造基线完成: 2 轮 done（mention_rate 0.8/0.9）")

# ---- 2. 真实触发轮 1（低提及率）-> 预期 watch ----
def real_round(qid):
    s, r = call("POST", "/api/monitor/start", {"question_ids": [qid], "engine_codes": ["deepseek"]})
    tid = r["data"]["task_id"]
    last = None
    t0 = time.time()
    while time.time() - t0 < 120:
        time.sleep(2)
        s, last = call("GET", f"/api/monitor/tasks/{tid}/progress")
        if last["data"]["status"] not in ("pending", "running"):
            break
    return tid, last

tid, last = real_round(38)
check("触发轮1 status=done", last["data"]["status"] == "done", f"status={last['data']['status']}")
check("触发轮1 提及率=0", last["data"]["done_calls"] == 1)

s, r = call("GET", "/api/alerts?unread=true")
items = r["data"]["items"]
watch = [a for a in items if a["level"] == "watch" and a["alert_type"] == "mention_drop"]
check("触发轮1 产出 mention_drop watch 预警", len(watch) >= 1,
      json.dumps(items, ensure_ascii=False)[:300])
if watch:
    check("watch 预警文案为真实轮次信息", "明显变少了" in watch[0]["message"], watch[0]["message"])

s, r = call("GET", "/api/alerts")
all_items = r["data"]["items"]
check("预警接口全量含 1 条", len(all_items) == 1, f"total={len(all_items)}")

s, r = call("GET", "/api/overview")
ov = r["data"]
check("overview unread_alerts 含该预警", len(ov["unread_alerts"]) == 1,
      json.dumps(ov["unread_alerts"], ensure_ascii=False)[:200])

# ---- 3. 真实触发轮 2（持续低）-> 预期升级 warning ----
tid2, last2 = real_round(39)
check("触发轮2 status=done", last2["data"]["status"] == "done", f"status={last2['data']['status']}")
s, r = call("GET", "/api/alerts?unread=true")
items2 = r["data"]["items"]
warn = [a for a in items2 if a["level"] == "warning" and a["alert_type"] == "mention_drop"]
check("触发轮2 升级为 warning 预警", len(warn) >= 1,
      json.dumps(items2, ensure_ascii=False)[:300])

# ---- 4. 已读语义回归 ----
if warn:
    aid = warn[0]["id"]
    s, r = call("POST", f"/api/alerts/{aid}/read")
    check("标记已读 code=0", r.get("code") == 0)
    s, r = call("GET", "/api/alerts?unread=true")
    ids = [a["id"] for a in r["data"]["items"]]
    check("已读后 unread=true 不含该条", aid not in ids, f"ids={ids}")
    s, r = call("GET", "/api/alerts?unread=false")
    ids2 = [a["id"] for a in r["data"]["items"]]
    check("unread=false 仅含已读条", aid in ids2 and len(ids2) == 1, f"ids={ids2}")

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
conn.close()
