# -*- coding: utf-8 -*-
"""第三轮回归 · 中途停止竞态专项复现（环境 B：端口 5098）
复现手法：2s 轮询（与首次观测相同时序），done_calls>=1 后立即发取消。
预期：取消若落在第 2 道调用在途（monitor_task.py:246 仅在调用前检查），任务以 done 收尾。
"""
import io, json, sys, time, sqlite3, datetime, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5098"
DB = r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_reg3_spot_20260810\data\geo.db"
OUTTXT = r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg3b_cancel_race_5098.txt"

def call(method, path, body=None, timeout=180):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

lines = [f"第三轮回归 · 中途停止竞态专项复现（环境B 端口5098）\n时间：{datetime.datetime.now().isoformat()}"]

for attempt in range(1, 4):
    s, r = call("POST", "/api/monitor/start", {"question_ids": [38, 39], "engine_codes": ["deepseek"]})
    tid = r["data"]["task_id"]
    t_start = time.time()
    cancel_ok = False
    t_cancel = None
    while time.time() - t_start < 240:
        time.sleep(2)
        s, pc = call("GET", f"/api/monitor/tasks/{tid}/progress")
        if pc["data"]["done_calls"] >= 1:
            t_cancel = time.time() - t_start
            s, r2 = call("POST", f"/api/monitor/tasks/{tid}/cancel")
            cancel_ok = r2.get("code") == 0
            break
        if pc["data"]["status"] not in ("pending", "running"):
            break
    while time.time() - t_start < 300:
        time.sleep(0.5)
        s, pc = call("GET", f"/api/monitor/tasks/{tid}/progress")
        if pc["data"]["status"] not in ("pending", "running"):
            break
    d = pc["data"]
    msg = (f"尝试{attempt}: 取消于第1问答完(+{t_cancel:.1f}s) 请求接受={cancel_ok} "
           f"终态={d['status']} error={d.get('error_msg')} done_calls={d['done_calls']} "
           f"总耗时={time.time()-t_start:.1f}s")
    print(msg)
    lines.append(msg)
    if d["status"] == "cancelled":
        break

with open(OUTTXT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
