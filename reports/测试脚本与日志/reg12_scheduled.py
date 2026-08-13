# -*- coding: utf-8 -*-
"""回归验收#12 + 专项5：定时自动触发完整快照链路。5095。
流程：仅保留 1 个启用问题 -> 定时改到 2 分钟后 -> 等自动触发 -> 验证 -> 恢复 08:30。"""
import io, json, sys, time, urllib.request, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5095"

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

# 0. 先等 专项1 遗留的 task14 结束（避免任务互斥）
for _ in range(60):
    s, r = call("GET", "/api/monitor/rounds")
    items = r["data"]["items"]
    latest = items[0] if items else {}
    tid_now = latest.get("task_id")
    s3, prog = call("GET", f"/api/monitor/tasks/{tid_now}/progress")
    if prog["data"]["status"] not in ("pending", "running"):
        break
    time.sleep(2)
print("当前最新任务:", prog["data"]["status"])

# 1. 禁用全部问题，仅保留 38
s, r = call("GET", "/api/questions")
qids = [q["id"] for q in r["data"]]
for qid in qids:
    call("PUT", f"/api/questions/{qid}", {"enabled": qid == 38})
s, r = call("GET", "/api/questions")
enabled_now = [q["id"] for q in r["data"] if q.get("enabled")]
check("仅 38 参与监测（控费）", enabled_now == [38], str(enabled_now))

# 2. 定时改到 2 分钟后
now = datetime.datetime.now()
target = now + datetime.timedelta(minutes=2)
tstr = f"{target.hour:02d}:{target.minute:02d}"
s, r = call("PUT", "/api/schedule", {"enabled": True, "time": tstr})
check("定时改为目标时间", r.get("code") == 0, json.dumps(r.get("data"), ensure_ascii=False)[:150])
check("next_run_time 顺延到目标时间", tstr in str(r["data"].get("next_run_time")),
      f"next={r['data'].get('next_run_time')}")
print(f"目标触发时间: {tstr}  现在: {now.strftime('%H:%M:%S')}")

# 3. 等待自动触发（最长 5 分钟）
sched_tid = None
for _ in range(150):
    time.sleep(2)
    s, r = call("GET", "/api/monitor/rounds")
    items = r["data"]["items"]
    for it in items:
        if it.get("task_type") == "scheduled":
            sched_tid = it["task_id"]
            break
    if sched_tid:
        break
check("出现 scheduled 类型任务", sched_tid is not None, f"tid={sched_tid}")
if not sched_tid:
    print("5 分钟内未触发，中止")
    sys.exit(1)

# 4. 等待该任务完成
last = None
for _ in range(90):
    time.sleep(2)
    s, last = call("GET", f"/api/monitor/tasks/{sched_tid}/progress")
    if last["data"]["status"] not in ("pending", "running"):
        break
d = last["data"]
check("自动轮 status=done", d["status"] == "done", f"status={d['status']}")
check("自动轮 done_calls=1", d["done_calls"] == 1, f"done={d['done_calls']}")
check("自动轮无错误", not d.get("error_msg"), str(d.get("error_msg")))

s, r = call("GET", "/api/monitor/rounds")
sched_round = [i for i in r["data"]["items"] if i["task_id"] == sched_tid]
check("自动轮轮次 task_type=scheduled", sched_round[0]["task_type"] == "scheduled")
check("自动轮轮次 task_status=done", sched_round[0]["task_status"] == "done")
rid = sched_round[0]["id"]

# 5. 快照/趋势/报告链路
s, r = call("GET", "/api/overview")
ov = r["data"]
check("overview 趋势含自动轮快照", ov["score"] is not None and len(ov["score_trend"]) >= 8,
      f"trend_len={len(ov['score_trend'])} last={ov['score']}")
s, r = call("GET", "/api/report/trend")
td = r["data"]
check("趋势图含自动轮", len(td["values"]) >= 8, json.dumps(td, ensure_ascii=False)[:150])
s, r = call("GET", f"/api/monitor/rounds/{rid}")
det = r["data"]
check("自动轮详情有回答与指标", len(det["results"]) == 1 and det["summary"]["overall_score"] is not None)

import sqlite3
conn = sqlite3.connect(r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_reg_20260810\data\geo.db")
cur = conn.cursor()
snap = cur.execute("SELECT COUNT(*) FROM score_snapshot WHERE round_id=?", (rid,)).fetchone()[0]
check("自动轮 score_snapshot 已产出", snap == 1, f"snap={snap}")
conn.close()

# 6. 恢复：定时改回 08:30，恢复全部问题启用
s, r = call("PUT", "/api/schedule", {"enabled": True, "time": "08:30"})
check("定时已改回 08:30", r["data"]["time"] == "08:30" and "08:30" in str(r["data"]["next_run_time"]),
      json.dumps(r["data"], ensure_ascii=False)[:120])
for qid in qids:
    call("PUT", f"/api/questions/{qid}", {"enabled": True})
s, r = call("GET", "/api/questions")
check("问题已全部恢复启用", all(q.get("enabled") for q in r["data"]),
      f"enabled={sum(1 for q in r['data'] if q.get('enabled'))}/{len(r['data'])}")

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
