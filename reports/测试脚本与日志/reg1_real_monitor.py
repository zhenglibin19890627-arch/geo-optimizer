# -*- coding: utf-8 -*-
"""回归#1：真实监测全链路（SQLite 锁修复验证）。隔离环境 5095。"""
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
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

# 1. 发起监测：1 问题(38) x 1 引擎(deepseek)
status, r = call("POST", "/api/monitor/start",
                 {"question_ids": [38], "engine_codes": ["deepseek"]})
check("start 返回 code=0", r.get("code") == 0, json.dumps(r, ensure_ascii=False)[:200])
task_id = r["data"]["task_id"] if r.get("code") == 0 else None
check("task_id 返回", isinstance(task_id, int))

# 2. 轮询进度直到结束（最长 120s）
last = None
t0 = time.time()
while time.time() - t0 < 120:
    time.sleep(2)
    status, last = call("GET", f"/api/monitor/tasks/{task_id}/progress")
    st = last["data"]["status"]
    if st not in ("pending", "running"):
        break
print("final progress:", json.dumps(last, ensure_ascii=False))

check("任务 status=done", last["data"]["status"] == "done",
      f"status={last['data']['status']}")
check("done_calls=1", last["data"]["done_calls"] == 1,
      f"done={last['data']['done_calls']}/{last['data']['total_calls']}")
check("progress=100", last["data"]["progress"] == 100,
      f"progress={last['data']['progress']}")
check("error_msg 为空", not last["data"].get("error_msg"),
      str(last["data"].get("error_msg")))

# 3. 轮次与结果
status, r = call("GET", "/api/monitor/rounds")
items = r["data"]["items"]
new_round = [i for i in items if i["task_id"] == task_id]
check("轮次列表含新轮", len(new_round) == 1)
rid = new_round[0]["id"] if new_round else None
check("新轮 task_status=done", new_round and new_round[0]["task_status"] == "done")

status, r = call("GET", f"/api/monitor/rounds/{rid}")
d = r["data"]
check("详情 results=1 条", len(d["results"]) == 1)
res = d["results"][0] if d["results"] else {}
check("回答入库(answer_text 非空)", bool(res.get("answer_text")),
      (res.get("answer_text") or "")[:80])
check("提及判定已计算", res.get("is_mentioned") in (True, False),
      f"is_mentioned={res.get('is_mentioned')}, count={res.get('mention_count')}")
check("情感已计算", res.get("sentiment") in ("positive", "negative", "neutral"),
      str(res.get("sentiment")))
check("竞品明细已解析", isinstance(res.get("competitor_mentions"), list))
check("信源已解析", isinstance(res.get("sources"), list))
check("轮次指标写回", new_round[0]["mention_rate"] is not None
      and new_round[0]["overall_score"] is not None,
      f"mention_rate={new_round[0].get('mention_rate')}, score={new_round[0].get('overall_score')}")
check("竞品对比接口", isinstance(d.get("competitor_compare"), list) and len(d["competitor_compare"]) >= 3,
      json.dumps(d.get("competitor_compare"), ensure_ascii=False)[:200])
check("信源排行接口", isinstance(d.get("sources_top"), list))

# 4. overview / 趋势（验收 #11 数据链路）
status, r = call("GET", "/api/overview")
ov = r["data"]
check("round_count=1", ov.get("round_count") == 1, f"round_count={ov.get('round_count')}")
check("last_round 有数据", bool(ov.get("last_round")),
      json.dumps(ov.get("last_round"), ensure_ascii=False)[:150])
check("评分有值", isinstance(ov.get("score"), (int, float)) and ov["score"] is not None,
      f"score={ov.get('score')}")
check("趋势有数据点", isinstance(ov.get("score_trend"), list) and len(ov["score_trend"]) == 1,
      json.dumps(ov.get("score_trend"), ensure_ascii=False)[:200])
check("hints 无误导", all("cancelled" not in str(h) and "failed" not in str(h) for h in ov.get("hints", [])),
      json.dumps(ov.get("hints"), ensure_ascii=False)[:200])

status, r = call("GET", "/api/report/trend")
trend = r["data"] if r.get("code") == 0 else r
check("趋势图数据=1 点", isinstance(trend, list) and len(trend) == 1,
      json.dumps(trend, ensure_ascii=False)[:300])

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok, det in results:
    if not ok:
        print("FAILED:", n, det)
