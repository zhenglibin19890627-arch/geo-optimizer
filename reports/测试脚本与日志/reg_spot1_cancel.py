# -*- coding: utf-8 -*-
"""回归专项1：真实轮中途停止全链路（已答保留/快照不写/预警不评估/已停止标识）。5095。"""
import io, json, sys, time, urllib.request
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

# 1. 发起 2 问 x 1 引擎
s, r = call("POST", "/api/monitor/start", {"question_ids": [38, 39], "engine_codes": ["deepseek"]})
tid = r["data"]["task_id"]
check("发起 2 问监测", r.get("code") == 0 and tid, f"task_id={tid} total={r['data'].get('total_calls')}")

# 2. 轮询直到 done_calls>=1 立即停止（利用 q2 调用前 1.5-3s 的检查窗口）
last = None
t0 = time.time()
stopped = False
while time.time() - t0 < 120:
    time.sleep(0.5)
    s, last = call("GET", f"/api/monitor/tasks/{tid}/progress")
    if last["data"]["done_calls"] >= 1:
        s, rc = call("POST", f"/api/monitor/tasks/{tid}/cancel")
        check("cancel 返回 code=0", rc.get("code") == 0, str(rc.get("message")))
        stopped = True
        break
check("第 1 问答完后成功停止", stopped, f"done_calls={last['data']['done_calls'] if last else '?'}")

# 3. 等待收尾（cancelled 落库）
time.sleep(3)
s, last = call("GET", f"/api/monitor/tasks/{tid}/progress")
d = last["data"]
check("任务 status=cancelled", d["status"] == "cancelled", f"status={d['status']}")
check("error_msg='用户主动停止'", d.get("error_msg") == "用户主动停止", str(d.get("error_msg")))
check("已答回答保留 done_calls=1", d["done_calls"] == 1, f"done={d['done_calls']}")

# 4. 轮次与结果
s, r = call("GET", "/api/monitor/rounds")
items = r["data"]["items"]
this_round = [i for i in items if i["task_id"] == tid]
check("轮次列表含该轮", len(this_round) == 1)
check("该轮 task_status=cancelled（前端可渲染'已停止'徽章）",
      this_round[0]["task_status"] == "cancelled", f"status={this_round[0].get('task_status')}")
rid = this_round[0]["id"]
s, r = call("GET", f"/api/monitor/rounds/{rid}")
res = r["data"]["results"]
check("已答 1 条回答保留", len(res) == 1 and bool(res[0].get("answer_text")),
      f"results={len(res)}")

# 5. 快照/预警/口径
s, r = call("GET", "/api/overview")
ov = r["data"]
# 当前库里正常轮：3 真实 + 2 构造 + 触发轮7/8 = 7；取消轮不计入
check("round_count 不含取消轮", ov["round_count"] == 7, f"round_count={ov['round_count']}")
check("last_round 不是取消轮（为触发轮8）", ov["last_round"]["id"] != rid,
      f"last_round.id={ov['last_round'].get('id')}")

import sqlite3
conn = sqlite3.connect(r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_reg_20260810\data\geo.db")
cur = conn.cursor()
snap = cur.execute("SELECT COUNT(*) FROM score_snapshot WHERE round_id=?", (rid,)).fetchone()[0]
check("score_snapshot 无该取消轮记录", snap == 0, f"snapshots={snap}")
al = cur.execute("SELECT COUNT(*) FROM alert WHERE round_id=?", (rid,)).fetchone()[0]
check("alert 无该取消轮记录", al == 0, f"alerts={al}")
trend = cur.execute("SELECT score FROM score_snapshot ORDER BY id").fetchall()
check("趋势快照序列不含取消轮", rid not in [t[0] for t in cur.execute('SELECT round_id FROM score_snapshot')],
      f"snapshot_rounds={[x[0] for x in cur.execute('SELECT round_id FROM score_snapshot')]}")
conn.close()

# 6. 取消后再发起新监测不受影响
s, r = call("POST", "/api/monitor/start", {"question_ids": [38], "engine_codes": ["deepseek"]})
check("停止后可再次发起", r.get("code") == 0, str(r.get("code")))

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
