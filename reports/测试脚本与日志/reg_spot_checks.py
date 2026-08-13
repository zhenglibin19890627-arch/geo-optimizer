# -*- coding: utf-8 -*-
"""回归抽查：手动粘贴通道 / 无钥匙拦截 / 来源标签。5095。"""
import io, json, sys, urllib.request
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

print("=== 手动粘贴通道（抽 1 条，含品牌提及+竞品+信源） ===")
answer = ("最近在挑婴儿推车，看了星辰母婴的新款，做工很扎实。"
          "好孩子的几款也不错，贝亲主要是配件。参考了 https://www.zhihu.com/question/123 和 https://www.163.com/ 的评测。")
s, r = call("POST", "/api/monitor/paste", {
    "engine_code": "manual",
    "question_text": "婴儿推车什么牌子好？",
    "answer_text": answer,
})
d = r.get("data", {}).get("result", {})
check("粘贴返回 code=0", r.get("code") == 0, str(r.get("message")))
check("提及判定 True", d.get("is_mentioned") is True, f"is_mentioned={d.get('is_mentioned')}")
check("提及次数=1", d.get("mention_count") == 1, f"count={d.get('mention_count')}")
check("情感判定", d.get("sentiment") in ("positive", "negative", "neutral"), str(d.get("sentiment")))
check("信源解析 2 个", len(d.get("sources") or []) == 2, json.dumps(d.get("sources"), ensure_ascii=False))
check("竞品明细含好孩子/贝亲", {m.get("name") for m in (d.get("competitor_mentions") or [])} == {"好孩子", "贝亲"},
      json.dumps(d.get("competitor_mentions"), ensure_ascii=False))

print()
print("=== 无钥匙拦截（kimi 无钥匙 / 分析模型无钥匙） ===")
s, r = call("POST", "/api/monitor/start", {"question_ids": [38], "engine_codes": ["kimi"]})
check("kimi 无钥匙发起监测被拦截", r.get("code") == 1 and "钥匙" in (r.get("message") or ""),
      str(r.get("message")))
s, r = call("POST", "/api/questions/expand", {"keywords": ["婴儿推车"], "count": 3})
check("扩展问法无钥匙被拦截", r.get("code") == 1 and "钥匙" in (r.get("message") or ""),
      str(r.get("message")))
s, r = call("POST", "/api/optimize", {"mode": "text", "text": "测试内容"})
check("内容优化无钥匙被拦截", r.get("code") == 1 and "钥匙" in (r.get("message") or ""),
      str(r.get("message")))

print()
print("=== 来源标签（preset/expanded/manual） ===")
s, r = call("POST", "/api/questions", {"text": "回归来源标签测试问题A", "source": "preset"})
q1 = r["data"].get("id") if r.get("code") == 0 else None
check("source=preset 透传", r.get("code") == 0, json.dumps(r.get("data"), ensure_ascii=False)[:120])
s, r = call("POST", "/api/questions", {"text": "回归来源标签测试问题B", "source": "expanded"})
q2 = r["data"].get("id") if r.get("code") == 0 else None
check("source=expanded 透传", r.get("code") == 0, json.dumps(r.get("data"), ensure_ascii=False)[:120])
s, r = call("POST", "/api/questions", {"text": "回归来源标签测试问题C", "source": "manual"})
q3 = r["data"].get("id") if r.get("code") == 0 else None
check("source=manual 透传", r.get("code") == 0, json.dumps(r.get("data"), ensure_ascii=False)[:120])
s, r = call("GET", f"/api/questions")
ids = {q["id"]: q for q in r["data"]}
check("三标签正确落库", ids[q1]["source"] == "preset" and ids[q2]["source"] == "expanded"
      and ids[q3]["source"] == "manual",
      f"{ids.get(q1, {}).get('source')}/{ids.get(q2, {}).get('source')}/{ids.get(q3, {}).get('source')}")
s, r = call("POST", "/api/questions", {"text": "非法来源测试问题", "source": "hack"})
check("非法 source 拦截", r.get("code") == 1, str(r.get("message")))
for qid in (q1, q2, q3):
    call("DELETE", f"/api/questions/{qid}")
check("清理测试问题完成", True)

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
