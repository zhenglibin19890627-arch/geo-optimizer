# -*- coding: utf-8 -*-
"""回归#3+#4+#7d后端：状态码 / 统计口径 / 徽章数据 / 品牌限长。隔离环境 5095。"""
import io, json, sys, urllib.request, urllib.parse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5095"

def call(method, path, body=None):
    path = urllib.parse.quote(path, safe="/?=&")
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

print("=== 缺陷#3：状态码 ===")
s, r = call("GET", "/api/不存在的路径")
check("GET /api/不存在的路径 -> HTTP 404", s == 404, f"http={s}")
check("  响应体 code=1", r.get("code") == 1, json.dumps(r, ensure_ascii=False)[:100])
check("  大白话消息", "回到首页" in (r.get("message") or ""), str(r.get("message")))

s, r = call("GET", "/api/monitor/start")
check("GET /api/monitor/start -> HTTP 405", s == 405, f"http={s}")
check("  响应体 code=1", r.get("code") == 1, json.dumps(r, ensure_ascii=False)[:100])
check("  大白话消息", "用法不对" in (r.get("message") or ""), str(r.get("message")))

s, r = call("GET", "/api/overview")
check("正常接口仍 200 + code=0", s == 200 and r.get("code") == 0, f"http={s} code={r.get('code')}")

print()
print("=== 缺陷#4：统计口径（隔离库现有 1 正常轮 + 1 取消轮） ===")
s, r = call("GET", "/api/overview")
ov = r["data"]
check("round_count=1（不含取消轮）", ov.get("round_count") == 1, f"round_count={ov.get('round_count')}")
check("last_round 为正常轮(round2) 无 task_status 徽章需求", ov.get("last_round", {}).get("id") == 2,
      json.dumps(ov.get("last_round"), ensure_ascii=False)[:120])
check("score_trend 仅正常轮", ov.get("score_trend") == [20], str(ov.get("score_trend")))
check("hints 按正常轮计数", "再监测 2 轮" in json.dumps(ov.get("hints"), ensure_ascii=False),
      json.dumps(ov.get("hints"), ensure_ascii=False)[:150])

s, r = call("GET", "/api/report/trend")
td = r["data"]
check("趋势图仅 1 点（取消轮 score=10 被排除）", td.get("values") == [20],
      json.dumps(td, ensure_ascii=False)[:200])

print()
print("=== 缺陷#4：轮次列表徽章数据源（task_status 字段） ===")
s, r = call("GET", "/api/monitor/rounds")
items = r["data"]["items"]
m = {i["id"]: i for i in items}
check("列表含 2 轮", len(items) == 2, f"total={len(items)}")
check("取消轮(round1) task_status=cancelled", m.get(1, {}).get("task_status") == "cancelled",
      f"round1.task_status={m.get(1, {}).get('task_status')}")
check("done 轮(round2) task_status=done", m.get(2, {}).get("task_status") == "done",
      f"round2.task_status={m.get(2, {}).get('task_status')}")
check("task_type 字段不受影响", m.get(1, {}).get("task_type") == "manual")

print()
print("=== 缺陷#7d 后端：品牌名限长 ===")
s, r = call("PUT", "/api/brand", {"brand_name": "长" * 51})
check("brand 51字 -> code=1", r.get("code") == 1, str(r.get("message")))
check("  大白话提示", "品牌名太长了" in (r.get("message") or ""), str(r.get("message")))
s, r = call("PUT", "/api/brand", {"brand_name": "星辰母婴", "product_name": "长" * 51})
check("product 51字 -> code=1", r.get("code") == 1, str(r.get("message")))
check("  大白话提示", "产品名太长了" in (r.get("message") or ""), str(r.get("message")))
s, r = call("PUT", "/api/brand", {"brand_name": "长" * 50})
check("brand 恰好50字 -> code=0", r.get("code") == 0, str(r.get("message")))
s, r = call("PUT", "/api/brand", {"brand_name": "星辰母婴", "product_name": "长" * 50})
check("product 恰好50字 -> code=0", r.get("code") == 0, str(r.get("message")))

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
