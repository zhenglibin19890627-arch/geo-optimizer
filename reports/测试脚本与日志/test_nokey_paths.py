import json, time, urllib.request

def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:5080" + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))

print("=== 1. 关键词扩展（无分析钥匙） ===")
r = call("POST", "/api/questions/expand", {"keywords": "婴儿车, 宝宝推车"})
print("expand:", r.get("code"), "|", r.get("message"))
assert r.get("code") == 1 and "钥匙" in r.get("message", ""), "FAIL: 无钥匙错误路径"

print()
print("=== 2. 内容优化文字（无分析钥匙） ===")
r = call("POST", "/api/optimize", {"type": "text",
     "content": "这是一个用于测试内容优化功能的文章内容，长度超过二十个字，用来验证无钥匙情况下的错误提示是否足够清晰明白。"})
rid = r.get("data", {}).get("record_id")
print("optimize start:", r.get("code"), "|", r.get("message"), "| record_id =", rid)
for i in range(15):
    time.sleep(1)
    rr = call("GET", f"/api/optimize/{rid}")
    st = rr.get("data", {}).get("status")
    if st in ("done", "failed"):
        print(f"optimize result after {i+1}s: status={st} error={rr.get('data', {}).get('error_msg')!r}")
        assert st == "failed" and "钥匙" in rr.get("data", {}).get("error_msg", ""), "FAIL: 优化无钥匙错误路径"
        break
else:
    print("FAIL: 优化任务 15 秒未结束")
print()
print("=== 3. 无钥匙路径全部 PASS ===")
