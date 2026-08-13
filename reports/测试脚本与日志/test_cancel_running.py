import json, time, urllib.request, sqlite3

BASE = "http://127.0.0.1:5080"

def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))

def dbq(sql):
    con = sqlite3.connect(r"C:\Users\zlb19\Desktop\GEO\data\geo.db")
    con.text_factory = str
    rows = con.execute(sql).fetchall()
    con.close()
    return rows

print("========== 专项1：停止本轮监测（running 窗口期真实执行） ==========")
r = call("POST", "/api/monitor/start", {"question_ids": [38], "engine_codes": ["deepseek"]})
task_id = r["data"]["task_id"]
print(f"[1] 发起 task_id={task_id}")

# 立即查看状态（应为 running 或 pending）
time.sleep(1)
p = call("GET", f"/api/monitor/tasks/{task_id}/progress")
print(f"[2] 1 秒后状态: {p['data']['status']} desc={p['data']['current_desc']}")

# 发起取消（趁 running）
r = call("POST", f"/api/monitor/tasks/{task_id}/cancel")
print(f"[3] cancel 响应: code={r['code']} msg={r['message']} data={r['data']}")

# 等待线程收尾
for i in range(12):
    time.sleep(1)
    p = call("GET", f"/api/monitor/tasks/{task_id}/progress")
    if p["data"]["status"] in ("cancelled", "done", "failed"):
        print(f"[4] 最终状态: {p['data']['status']} error={p['data']['error_msg']!r}")
        break
else:
    print("[4] WARN: 12 秒未收尾")

# 数据库验证
tasks = dbq(f"SELECT status, error_msg, finished_at FROM monitor_task WHERE id={task_id}")
print(f"[5] DB 任务状态: {tasks}")
rounds = dbq(f"SELECT id, mention_rate, overall_score FROM monitor_round WHERE task_id={task_id}")
print(f"[6] 轮次: {rounds}")
results = dbq(f"SELECT count(*), sum(case when answer_text is not null then 1 else 0 end) FROM monitor_result WHERE round_id={rounds[0][0] if rounds else -1}")
print(f"[7] 结果数/已回答数: {results}")
snaps = dbq(f"SELECT count(*) FROM score_snapshot WHERE round_id={rounds[0][0] if rounds else -1}")
print(f"[8] score_snapshot 该轮记录: {snaps[0][0]}")
alerts = dbq("SELECT count(*) FROM alert")
print(f"[9] alert 记录数: {alerts[0][0]}")
