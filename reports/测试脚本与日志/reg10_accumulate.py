# -*- coding: utf-8 -*-
"""回归#10 真实轮次积累：再跑 2 轮真实监测（问题39/38），并做 #13 抽查一致性。5095。"""
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

for qid, qtext in [(39, "如何选择婴儿推车？"), (38, "婴儿推车什么牌子好？")]:
    s, r = call("POST", "/api/monitor/start", {"question_ids": [qid], "engine_codes": ["deepseek"]})
    tid = r["data"]["task_id"]
    last = None
    t0 = time.time()
    while time.time() - t0 < 120:
        time.sleep(2)
        s, last = call("GET", f"/api/monitor/tasks/{tid}/progress")
        if last["data"]["status"] not in ("pending", "running"):
            break
    check(f"真实轮 q{qid} status=done", last["data"]["status"] == "done",
          f"status={last['data']['status']} done={last['data']['done_calls']}")
    check(f"真实轮 q{qid} 无错误", not last["data"].get("error_msg"), str(last["data"].get("error_msg")))
    s, r = call("GET", "/api/monitor/rounds")
    items = r["data"]["items"]
    newr = [i for i in items if i["task_id"] == tid]
    check(f"真实轮 q{qid} 轮次已生成", len(newr) == 1)
    check(f"真实轮 q{qid} 指标写回", newr[0]["mention_rate"] is not None
          and newr[0]["overall_score"] is not None,
          f"rate={newr[0].get('mention_rate')} score={newr[0].get('overall_score')}")
    # 保存轮次详情用于 #13 抽查
    rid = newr[0]["id"]
    s, r = call("GET", f"/api/monitor/rounds/{rid}")
    with open(r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg13_round_detail.json", "a", encoding="utf-8") as f:
        f.write(json.dumps({"round_id": rid, "task_id": tid, "question": qtext, "detail": r["data"]}, ensure_ascii=False) + "\n")

print()
print("=== 当前 overview 状态 ===")
s, r = call("GET", "/api/overview")
ov = r["data"]
print(json.dumps({"round_count": ov["round_count"], "score": ov["score"],
                  "score_trend": ov["score_trend"], "hints": ov["hints"]}, ensure_ascii=False))
check("round_count=3（真实3轮，不含取消轮）", ov["round_count"] == 3, f"round_count={ov['round_count']}")
check("趋势图 3 个真实数据点", len(ov["score_trend"]) == 3, str(ov["score_trend"]))
check("预警 0 条（基线为0/不足，不误触发）", len(ov.get("unread_alerts", [])) == 0,
      json.dumps(ov.get("unread_alerts"), ensure_ascii=False))

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
