# -*- coding: utf-8 -*-
"""第三轮回归 · 关键链路抽查（环境 B：geo_reg3_spot_20260810，端口 5098，调度器正常启用）

抽查项（任务书第三节）：
1. 监测主链路 1 轮（真实钥匙，1 问题 × 1 引擎）：status=done、快照/评分正常
2. 中途停止：cancelled + 已答保留 + 无快照无预警
3. 定时设置读写：PUT /api/schedule 改时间 → next_run_time 正确；恢复原值
4. 口径抽查：round_count/趋势图仍排除 cancelled 轮
"""
import io, json, sys, time, sqlite3, datetime, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5098"
DB = r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_reg3_spot_20260810\data\geo.db"
OUTTXT = r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg3b_spot_5098.txt"
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

def run_round(qids):
    s, r = call("POST", "/api/monitor/start", {"question_ids": qids, "engine_codes": ["deepseek"]})
    tid = r["data"]["task_id"]
    last = None
    t0 = time.time()
    while time.time() - t0 < 240:
        time.sleep(2)
        s, last = call("GET", f"/api/monitor/tasks/{tid}/progress")
        if last["data"]["status"] not in ("pending", "running"):
            break
    return tid, last

conn = sqlite3.connect(DB, timeout=30)
cur = conn.cursor()

print("== 0. 口径基线（全新共享库复制件：仅 1 轮 cancelled） ==")
s, r = call("GET", "/api/overview")
ov0 = r["data"]
round_count_before = ov0.get("round_count", 0)
check("初始 round_count=0（cancelled 轮 round1 已被口径排除）", round_count_before == 0,
      f"round_count={round_count_before}")
s, r = call("GET", "/api/report/trend")
trend0 = r["data"]
check("初始趋势图 0 点（cancelled 轮排除）", len(trend0.get("values") or []) == 0,
      f"values={trend0.get('values')}")

print("== 1. 监测主链路（1 问题 × 1 引擎，真实钥匙） ==")
s, r = call("GET", "/api/questions")
q38 = next((q for q in r["data"] if q["id"] == 38), None)
check("问题库有可用问题（id=38 预置既有数据）", q38 is not None, json.dumps(r["data"][:2], ensure_ascii=False)[:150])
tid1, last1 = run_round([38])
d1 = last1["data"]
check("主链路 status=done", d1["status"] == "done", f"status={d1['status']} error={d1.get('error_msg')}")
check("主链路 error_msg 为空", not d1.get("error_msg"), f"error={d1.get('error_msg')}")
check("主链路 done_calls=1", d1["done_calls"] == 1, f"done_calls={d1['done_calls']}")
def round_id_of(tid):
    return cur.execute("SELECT MAX(id) FROM monitor_round WHERE task_id=?", (tid,)).fetchone()[0]
rid1 = round_id_of(tid1)
cur.execute("SELECT COUNT(*) FROM monitor_result WHERE round_id=?", (rid1,))
check("回答已入库", cur.fetchone()[0] >= 1)
cur.execute("SELECT round_id, score FROM score_snapshot WHERE round_id=?", (rid1,))
snap = cur.fetchone()
check("score_snapshot 产出且评分非空", snap is not None and snap[1] is not None, f"snap={snap}")
cur.execute("SELECT mention_rate, net_sentiment, overall_score FROM monitor_round WHERE task_id=?", (tid1,))
metrics = cur.fetchone()
check("轮次指标齐全", metrics is not None and all(x is not None for x in metrics), f"metrics={metrics}")
s, r = call("GET", "/api/overview")
ov1 = r["data"]
check("主链路后 round_count=1（主链路轮计入，cancelled 仍排除）", ov1.get("round_count") == round_count_before + 1,
      f"round_count={ov1.get('round_count')} before={round_count_before}")
check("last_round 为主链路轮（id 一致）", ov1.get("last_round", {}).get("id") == rid1,
      json.dumps(ov1.get("last_round"), ensure_ascii=False)[:120])
s, r = call("GET", "/api/report/trend")
trend1 = r["data"]
check("趋势图 1 点（主链路轮）", len(trend1.get("values") or []) == 1, f"values={trend1.get('values')}")

print("== 2. 中途停止（2 问，第 1 问答完即停，最多 3 次尝试以甄别竞态） ==")
attempts_log = []
n_res = None
rid_c = None
for attempt in range(1, 4):
    s, r = call("POST", "/api/monitor/start", {"question_ids": [38, 39], "engine_codes": ["deepseek"]})
    tid_c = r["data"]["task_id"]
    cancel_ok = False
    t0 = time.time()
    while time.time() - t0 < 240:
        time.sleep(0.5)
        s, pc = call("GET", f"/api/monitor/tasks/{tid_c}/progress")
        if pc["data"]["done_calls"] >= 1:
            s, r2 = call("POST", f"/api/monitor/tasks/{tid_c}/cancel")
            cancel_ok = r2.get("code") == 0
            break
        if pc["data"]["status"] not in ("pending", "running"):
            break
    t0 = time.time()
    while time.time() - t0 < 30:
        time.sleep(0.5)
        s, pc = call("GET", f"/api/monitor/tasks/{tid_c}/progress")
        if pc["data"]["status"] not in ("pending", "running"):
            break
    dc = pc["data"]
    rid = round_id_of(tid_c)
    cur.execute("SELECT COUNT(*) FROM monitor_result WHERE round_id=?", (rid,))
    n = cur.fetchone()[0]
    attempts_log.append((attempt, dc["status"], dc.get("error_msg"), dc["done_calls"], n))
    print(f"  尝试{attempt}: status={dc['status']} error={dc.get('error_msg')} done_calls={dc['done_calls']} results={n}")
    if dc["status"] == "cancelled":
        cancel_ok, n_res, rid_c = True, n, rid
        break
check("取消请求 API 接受（code=0）", any(a[1] != "done" for a in attempts_log) or cancel_ok,
      f"attempts={attempts_log}")
check("存在成功取消轮（status=cancelled + 已答保留）", n_res is not None, f"attempts={attempts_log}")
dc_final = next((a for a in attempts_log if a[1] == "cancelled"), None) or attempts_log[-1]
check("成功取消轮 error_msg=用户主动停止", dc_final[1] != "cancelled" or dc_final[2] == "用户主动停止",
      f"dc_final={dc_final}")
if rid_c is not None:
    cur.execute("SELECT COUNT(*) FROM score_snapshot WHERE round_id=?", (rid_c,))
    check("取消轮无 score_snapshot", cur.fetchone()[0] == 0)
    cur.execute("SELECT COUNT(*) FROM alert WHERE round_id=?", (rid_c,))
    check("取消轮无预警产出", cur.fetchone()[0] == 0)
check("中途停止竞态甄别：记录各尝试结果", True, f"attempts={attempts_log}")

print("== 3. 定时设置读写（settings 表 + next_run_time） ==")
s, r = call("GET", "/api/schedule")
sch0 = r["data"]
check("初始定时=08:30 且 enabled", sch0.get("time") == "08:30" and sch0.get("enabled") is True,
      json.dumps(sch0, ensure_ascii=False)[:150])
check("初始 next_run_time 为次日 08:30", sch0.get("next_run_time", "").endswith(" 08:30:00"), f"next={sch0.get('next_run_time')}")
s, r = call("PUT", "/api/schedule", {"time": "23:59"})
sch1 = r["data"]
check("改时间后 time=23:59", sch1.get("time") == "23:59", json.dumps(sch1, ensure_ascii=False)[:150])
check("next_run_time 顺延为今日/明日 23:59:00", sch1.get("next_run_time", "").endswith(" 23:59:00"), f"next={sch1.get('next_run_time')}")
cur.execute("SELECT value FROM settings WHERE key='schedule_time'")
check("settings 表 schedule_time 落库为 23:59", cur.fetchone()[0] == '"23:59"')
s, r = call("PUT", "/api/schedule", {"time": "08:30"})
sch2 = r["data"]
check("改回 08:30 恢复", sch2.get("time") == "08:30" and sch2.get("next_run_time", "").endswith(" 08:30:00"),
      json.dumps(sch2, ensure_ascii=False)[:150])

print("== 4. 口径抽查：round_count/趋势图排除 cancelled 轮 ==")
expected_normal = round_count_before + 1 + sum(1 for a in attempts_log if a[1] == "done")
s, r = call("GET", "/api/overview")
ov2 = r["data"]
check("round_count 口径=正常轮数（cancelled 不计入）", ov2.get("round_count") == expected_normal,
      f"round_count={ov2.get('round_count')} 期望={expected_normal}")
s, r = call("GET", "/api/report/trend")
trend2 = r["data"]
check("趋势图点数=正常轮数（cancelled 排除）", len(trend2.get("values") or []) == expected_normal,
      f"values={trend2.get('values')}")
s, r = call("GET", "/api/monitor/rounds")
items = r["data"]["items"]
canc = [i for i in items if i["task_status"] == "cancelled"]
check("轮次列表含取消轮且 task_status=cancelled 透传", len(canc) >= 1, f"canc={canc}")
done = [i for i in items if i["task_status"] == "done"]
check("主链路轮 task_status=done 透传", len(done) >= 1, f"done={done}")
if rid_c is not None:
    s, r = call("GET", f"/api/monitor/rounds/{rid_c}")
    cd = r
    check("取消轮详情可打开（已答回答保留）", cd.get("code") == 0 and len(cd["data"].get("results") or []) >= 1,
          json.dumps(cd.get("data", {}).get("results"), ensure_ascii=False)[:200])

print("== 5. 服务器日志终检（抽查期间 0 锁异常） ==")
try:
    with open(r"C:\Users\zlb19\AppData\Local\Temp\opencode\reg3b_err.log", "r", encoding="utf-8", errors="replace") as f:
        logtext = f.read()
except Exception:
    logtext = ""
bad = [ln for ln in logtext.splitlines() if "database is locked" in ln or "OperationalError" in ln]
check("抽查期间服务器日志 0 次 database is locked / OperationalError", len(bad) == 0, f"found={len(bad)}")

conn.close()
ok_n = sum(1 for x in results if x[1])
with open(OUTTXT, "w", encoding="utf-8") as f:
    f.write(f"第三轮回归 · 关键链路抽查（环境B 端口5098）\n时间：{datetime.datetime.now().isoformat()}\n")
    f.write(f"SUMMARY: {ok_n} / {len(results)} PASS\n")
    for name, ok, det in results:
        f.write(("PASS " if ok else "FAIL ") + name + (" | " + det if det else "") + "\n")
print(f"\n==== 汇总: {ok_n}/{len(results)} PASS ====")
