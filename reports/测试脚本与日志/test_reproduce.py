import json, time, urllib.request, sqlite3

BASE = "http://127.0.0.1:5080"

def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))

print("=== 复测：真实监测任务是否稳定失败 ===")
for n in range(2):
    r = call("POST", "/api/monitor/start", {"question_ids": [38], "engine_codes": ["deepseek"]})
    task_id = r["data"]["task_id"]
    print(f"第{n+1}次发起 task_id={task_id}")
    # 轮询直到结束
    for i in range(40):
        time.sleep(2)
        p = call("GET", f"/api/monitor/tasks/{task_id}/progress")
        d = p["data"]
        if d["status"] in ("done", "failed", "cancelled"):
            print(f"  状态={d['status']} done={d['done_calls']}/{d['total_calls']} error={d['error_msg']!r}")
            break
    else:
        print("  40 次轮询仍未结束")

# 数据库核对
con = sqlite3.connect(r"C:\Users\zlb19\Desktop\GEO\data\geo.db")
con.text_factory = str
print()
print("tasks:", con.execute("SELECT id,status,error_msg,done_calls,total_calls FROM monitor_task").fetchall())
print("rounds:", con.execute("SELECT id,task_id,overall_score FROM monitor_round").fetchall())
print("api_call_log:", con.execute("SELECT engine_code,model,tokens_in,tokens_out,cost_yuan FROM api_call_log").fetchall())
print("score_snapshot:", con.execute("SELECT round_id,score FROM score_snapshot").fetchall())
con.close()
