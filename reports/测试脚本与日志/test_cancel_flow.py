import json, time, urllib.request

BASE = "http://127.0.0.1:5080"

def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))

def dbq(sql):
    import sqlite3
    con = sqlite3.connect(r"C:\Users\zlb19\Desktop\GEO\data\geo.db")
    rows = con.execute(sql).fetchall()
    con.close()
    return rows

print("========== 测试A：停止本轮监测全流程 ==========")
# 1. 发起监测：1 个问题 × DeepSeek
r = call("POST", "/api/monitor/start", {"question_ids": [38, 39], "engine_codes": ["deepseek"]})
assert r["code"] == 0, r
task_id = r["data"]["task_id"]
print(f"[1] 发起成功 task_id={task_id} estimated={r['data']['estimated_seconds']}s total_calls={r['data']['total_calls']}")

# 2. 轮询进度，等待至少 1 个回答完成（约 5-12 秒）
progress_seen = []
for i in range(30):
    time.sleep(2)
    p = call("GET", f"/api/monitor/tasks/{task_id}/progress")
    d = p["data"]
    progress_seen.append((d["status"], d["done_calls"], d["current_desc"], d["progress"], d["remain_seconds"]))
    if d["done_calls"] >= 1:
        print(f"[2] 已收到第 1 个回答 (第{i*2+2}s): status={d['status']} done={d['done_calls']}/{d['total_calls']} desc={d['current_desc']}")
        break
    if d["status"] in ("done", "failed", "cancelled"):
        break
else:
    print("[2] WARN: 30 次轮询未完成第 1 个回答，最后状态:", progress_seen[-1])

# 3. 发起停止
r = call("POST", f"/api/monitor/tasks/{task_id}/cancel")
print(f"[3] cancel 响应: code={r['code']} message={r['message']} data={r['data']}")
assert r["code"] == 0 and r["message"] == "已停止本轮监测，已问到的回答已保存" and r["data"] == {"cancelled": True}

# 4. 等待收尾（线程检查点：最多等 6 秒）
time.sleep(6)

# 5. 验证任务状态
t = dbq(f"SELECT id,type,status,error_msg,finished_at,total_calls,done_calls FROM monitor_task WHERE id={task_id}")[0]
print(f"[5] 任务状态: status={t[2]} error_msg={t[3]!r} done={t[6]}/{t[5]} finished_at={t[4]}")
assert t[2] == "cancelled", f"FAIL: status={t[2]}"
assert t[3] == "用户主动停止", f"FAIL: error_msg={t[3]}"
assert t[4] is not None, "FAIL: finished_at 未写入"

# 6. 验证结果保留
results = dbq(f"SELECT engine_code, question_text, substr(answer_text,1,40), is_mentioned, sentiment FROM monitor_result WHERE round_id=(SELECT id FROM monitor_round WHERE task_id={task_id})")
print(f"[6] 已保存结果 {len(results)} 条:")
for r6 in results:
    print("   ", r6[0], "|", r6[1][:20], "| 回答:", (r6[2] or "（无）")[:30], "| 提及:", r6[3], "| 情感:", r6[4])
assert len(results) >= 1 and results[0][2], "FAIL: 已回答内容未保留"

# 7. 验证轮次指标写回
rounds = dbq(f"SELECT id, mention_rate, net_sentiment, overall_score, summary FROM monitor_round WHERE task_id={task_id}")
print(f"[7] 轮次指标: mention_rate={rounds[0][1]} net_sentiment={rounds[0][2]} score={rounds[0][3]}")
print(f"    summary={rounds[0][4]}")

# 8. 验证 score_snapshot 无该轮记录（不被污染）
snaps = dbq(f"SELECT id, round_id, score FROM score_snapshot WHERE round_id={rounds[0][0]}")
print(f"[8] score_snapshot 该轮记录数 = {len(snaps)}")
assert len(snaps) == 0, "FAIL: 取消轮不应写入 score_snapshot"

# 9. 验证预警未被污染
alerts = dbq("SELECT id, alert_type, level, message FROM alert")
print(f"[9] alert 表记录数 = {len(alerts)}")
assert len(alerts) == 0, "FAIL: 取消轮不应产生预警"

# 10. 轮次列表接口
r = call("GET", "/api/monitor/rounds?page=1")
items = r["data"]["items"]
mine = [x for x in items if x["task_id"] == task_id]
print(f"[10] 轮次列表: total={r['data']['total']} 该轮={mine[0]['id'] if mine else None} 分数={mine[0]['overall_score'] if mine else None}")
assert len(mine) == 1

print()
print("========== 测试A 全部断言通过：停止全流程正确，快照/预警未被污染 ==========")
