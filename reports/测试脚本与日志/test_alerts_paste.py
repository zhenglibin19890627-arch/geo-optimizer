import json, urllib.request, sys, sqlite3

BASE = "http://127.0.0.1:5080"
PASS = 0
FAIL = 0
RESULTS = []

def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))
    except Exception as e:
        return -1, {"error": str(e)}

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"PASS | {name} | {detail}")
        print(f"[PASS] {name} | {detail}")
    else:
        FAIL += 1
        RESULTS.append(f"FAIL | {name} | {detail}")
        print(f"[FAIL] {name} | {detail}")

print("========== 预警 unread 参数测试（专项3） ==========")
# 直接插入 2 条预警：1 已读 1 未读
con = sqlite3.connect(r"C:\Users\zlb19\Desktop\GEO\data\geo.db")
con.execute("INSERT INTO alert (round_id, alert_type, level, message, is_read) VALUES (?,?,?,?,?)",
            (1, "mention_drop", "warning", "测试未读预警A", 0))
con.execute("INSERT INTO alert (round_id, alert_type, level, message, is_read) VALUES (?,?,?,?,?)",
            (1, "score_drop", "watch", "测试已读预警B", 1))
con.commit()
con.close()

st, b = call("GET", "/api/alerts")
items = b.get("data", {}).get("items") or []
check("GET /api/alerts 不带参数=全量", st == 200 and b.get("code") == 0 and len(items) == 2,
      f"count={len(items)} unread_count={b.get('data', {}).get('unread_count')}")

st, b = call("GET", "/api/alerts?unread=true")
items = b.get("data", {}).get("items") or []
check("GET /api/alerts?unread=true 仅未读", st == 200 and b.get("code") == 0
      and len(items) == 1 and items[0].get("is_read") is False,
      f"count={len(items)} items={[i.get('message') for i in items]}")

st, b = call("GET", "/api/alerts?unread=false")
items = b.get("data", {}).get("items") or []
check("GET /api/alerts?unread=false 仅已读", st == 200 and b.get("code") == 0
      and len(items) == 1 and items[0].get("is_read") is True,
      f"count={len(items)} items={[i.get('message') for i in items]}")

st, b = call("GET", "/api/overview")
ua = b.get("data", {}).get("unread_alerts") or []
check("GET /api/overview 含未读预警", st == 200 and b.get("code") == 0 and len(ua) == 1 and ua[0].get("message") == "测试未读预警A",
      f"unread_alerts={[x.get('message') for x in ua]}")

# 标记已读（标记未读的那条 id=1）
st, b = call("POST", "/api/alerts/1/read")
check("POST /api/alerts/1/read 标记已读", st == 200 and b.get("code") == 0, f"msg={b.get('message')}")

st, b = call("GET", "/api/alerts?unread=true")
items = b.get("data", {}).get("items") or []
check("标记已读后 unread=true 为空", st == 200 and b.get("code") == 0 and len(items) == 0,
      f"count={len(items)}")

# 清理测试预警
con = sqlite3.connect(r"C:\Users\zlb19\Desktop\GEO\data\geo.db")
con.execute("DELETE FROM alert")
con.commit()
con.close()

print()
print("========== 手动粘贴全流程（专项：零费用链路） ==========")
# 恢复品牌：测试品牌 + 竞品1/竞品2/竞品3
call("PUT", "/api/brand", {"brand_name": "测试品牌", "product_name": "测试产品",
     "brand_aliases": "别名A, 别名B", "competitors": "竞品1, 竞品2, 竞品3"})
st, b = call("POST", "/api/monitor/paste", {
    "engine_code": "manual",
    "question_text": "测试品牌和竞品1哪个好？",
    "answer_text": "推荐测试品牌，性价比很高，质量可靠，比竞品1好很多。很多人把测试品牌列为第一选择。引用来源：https://www.zhihu.com/question/123 和 https://baike.baidu.com/item/测试品牌 的内容。",
})
d = b.get("data") or {}
r = d.get("result") or {}
check("paste 提及判定", d.get("result_id") and r.get("is_mentioned") is True, f"is_mentioned={r.get('is_mentioned')}")
check("paste 提及次数≥2", r.get("mention_count", 0) >= 2, f"mention_count={r.get('mention_count')}")
check("paste 情感=正向", r.get("sentiment") == "positive", f"sentiment={r.get('sentiment')}")
check("paste 信源解析≥2", len(r.get("sources") or []) >= 2, f"sources={r.get('sources')}")
check("paste 竞品明细", len(r.get("competitor_mentions") or []) >= 1, f"competitor={r.get('competitor_mentions')}")
check("paste 顺位=第1位", r.get("mention_position") == 1, f"position={r.get('mention_position')}")
check("paste 显示名=手动粘贴", r.get("display_name") == "手动粘贴", f"display_name={r.get('display_name')}")

# 负面情感
st, b = call("POST", "/api/monitor/paste", {
    "engine_code": "manual",
    "question_text": "测试品牌质量如何？",
    "answer_text": "测试品牌很垃圾，质量差，不推荐购买，问题多。",
})
r = b.get("data", {}).get("result") or {}
check("paste 负面情感", r.get("sentiment") == "negative", f"sentiment={r.get('sentiment')}")

# 中性
st, b = call("POST", "/api/monitor/paste", {
    "engine_code": "manual",
    "question_text": "随便聊聊",
    "answer_text": "今天天气不错，适合出门走走。",
})
r = b.get("data", {}).get("result") or {}
check("paste 未提及+中性", r.get("is_mentioned") is False and r.get("sentiment") == "neutral",
      f"mentioned={r.get('is_mentioned')} sentiment={r.get('sentiment')}")

print(f"\n========== 结果: PASS={PASS} FAIL={FAIL} ==========")
with open(r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_test\alerts_paste_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(RESULTS))
sys.exit(1 if FAIL else 0)
