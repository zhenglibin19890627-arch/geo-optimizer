import json, urllib.request, sys

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
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
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

def msg(body):
    return body.get("message", "")

print("========== 边界用例测试开始 ==========")

# ---- 1. 品牌信息 ----
st, b = call("PUT", "/api/brand", {"brand_name": "", "competitors": ""})
check("PUT /api/brand 空品牌名", st == 200 and b.get("code") == 1 and "品牌名" in msg(b), f"msg={msg(b)}")

st, b = call("PUT", "/api/brand", {"brand_name": "测试品牌", "product_name": "测试产品",
             "brand_aliases": "别名A, 别名B，别名C;别名D", "brand_description": "一句话介绍内容",
             "competitors": "竞品1, 竞品2；竞品3"})
ok_data = b.get("data") or {}
check("PUT /api/brand 有效保存(含逗号分号解析)", st == 200 and b.get("code") == 0
      and ok_data.get("brand_name") == "测试品牌"
      and len(ok_data.get("brand_aliases", [])) == 4
      and len(ok_data.get("competitors", [])) == 3,
      f"aliases={ok_data.get('brand_aliases')} competitors={ok_data.get('competitors')}")

st, b = call("PUT", "/api/brand", {"brand_name": "超长品牌" + "X" * 500})
check("PUT /api/brand 超长品牌名(501字)", st == 200 and b.get("code") == 0,
      "后端未限长，截断策略待确认，DB字段String(200)")

# ---- 2. 问题库 ----
st, b = call("POST", "/api/questions", {"text": "", "source": "manual"})
check("POST /api/questions 空内容", st == 200 and b.get("code") == 1 and "不能为空" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/questions", {"text": "非法来源测试", "source": "hacker"})
check("POST /api/questions 非法source", st == 200 and b.get("code") == 1 and "这个来源不认" in msg(b), f"msg={msg(b)}")

q_ids = []
for src in ("preset", "expanded", "manual"):
    st, b = call("POST", "/api/questions", {"text": f"来源测试-{src}", "category": "测试分类", "source": src})
    ok = b.get("data") or {}
    q_ids.append(ok.get("id"))
    check(f"POST /api/questions source={src}", st == 200 and b.get("code") == 0 and ok.get("source") == src,
          f"data.source={ok.get('source')}")

st, b = call("GET", "/api/questions?source=expanded")
items = b.get("data") or []
check("GET /api/questions?source=expanded 过滤", st == 200 and b.get("code") == 0
      and all(i.get("source") == "expanded" for i in items) and len(items) == 1,
      f"count={len(items)}")

st, b = call("PUT", f"/api/questions/{q_ids[0]}", {"enabled": False, "text": "来源测试-改"})
check("PUT /api/questions 开关+改名", st == 200 and b.get("code") == 0
      and b.get("data", {}).get("enabled") is False and b.get("data", {}).get("text") == "来源测试-改",
      f"data={b.get('data')}")

st, b = call("PUT", "/api/questions/999999", {"enabled": True})
check("PUT /api/questions 不存在", st == 200 and b.get("code") == 1 and "不存在" in msg(b), f"msg={msg(b)}")

st, b = call("DELETE", "/api/questions/999999")
check("DELETE /api/questions 不存在", st == 200 and b.get("code") == 1 and "不存在" in msg(b), f"msg={msg(b)}")

# 清理测试问题
for qid in q_ids:
    call("DELETE", f"/api/questions/{qid}")

# ---- 3. 关键词 ----
st, b = call("POST", "/api/keywords", {"texts": ["关键词1", "关键词1", " 关键词2 ", "关键词1"]})
check("POST /api/keywords 去重", st == 200 and b.get("code") == 0 and b.get("data", {}).get("added") == 2,
      f"added={b.get('data', {}).get('added')}")

st, b = call("POST", "/api/keywords", {"texts": "a,b；c,d,e"})
check("POST /api/keywords 逗号分号字符串", st == 200 and b.get("code") == 0 and b.get("data", {}).get("added") == 3,
      f"added={b.get('data', {}).get('added')}")

st, b = call("GET", "/api/keywords?enabled=true")
items = b.get("data") or []
check("GET /api/keywords?enabled=true", st == 200 and b.get("code") == 0 and len(items) == 5,
      f"count={len(items)}")

st, b = call("DELETE", "/api/keywords/999999")
check("DELETE /api/keywords 不存在", st == 200 and b.get("code") == 1 and "不存在" in msg(b), f"msg={msg(b)}")

# ---- 4. 监测相关 ----
st, b = call("POST", "/api/monitor/start", {"question_ids": [1], "engine_codes": ["deepseek"]})
check("POST /api/monitor/start 无钥匙", st == 200 and b.get("code") == 1 and "钥匙" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/monitor/start", {"question_ids": [999999], "engine_codes": []})
check("POST /api/monitor/start 问题不存在且引擎空", st == 200 and b.get("code") == 1, f"msg={msg(b)}")

st, b = call("GET", "/api/monitor/tasks/999999/progress")
check("GET progress 任务不存在", st == 200 and b.get("code") == 1 and "找不到" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/monitor/tasks/999999/cancel")
check("POST cancel 任务不存在", st == 200 and b.get("code") == 1 and "找不到" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/monitor/paste", {"engine_code": "manual", "question_text": "", "answer_text": "回答"})
check("POST paste 空问题", st == 200 and b.get("code") == 1 and "问题" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/monitor/paste", {"engine_code": "manual", "question_text": "问题", "answer_text": ""})
check("POST paste 空回答", st == 200 and b.get("code") == 1 and "回答" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/monitor/paste", {"engine_code": "no_such_engine", "question_text": "问题", "answer_text": "回答"})
check("POST paste 非法引擎", st == 200 and b.get("code") == 1 and "没找到这家引擎" in msg(b), f"msg={msg(b)}")

# ---- 5. 定时 ----
for bad, expect in [("abc", "格式"), ("25:00", "范围"), ("08:70", "范围"), ("8:5", "格式")]:
    st, b = call("PUT", "/api/schedule", {"enabled": True, "time": bad})
    check(f"PUT /api/schedule 非法时间 {bad}", st == 200 and b.get("code") == 1 and expect in msg(b), f"msg={msg(b)}")

st, b = call("PUT", "/api/schedule", {"enabled": True, "time": "8:30"})
check("PUT /api/schedule 合法 8:30 归一化", st == 200 and b.get("code") == 0 and b.get("data", {}).get("time") == "08:30",
      f"time={b.get('data', {}).get('time')}")

st, b = call("PUT", "/api/schedule", {"enabled": False})
check("PUT /api/schedule 仅关开关", st == 200 and b.get("code") == 0 and b.get("data", {}).get("enabled") is False,
      f"data={b.get('data', {}).get('enabled')}")

# 还原 08:30 开启
call("PUT", "/api/schedule", {"enabled": True, "time": "08:30"})

# ---- 6. 设置/档位 ----
st, b = call("POST", "/api/settings", {"engine_model": {"deepseek": "no-such-model"}})
check("POST /api/settings 非法档位", st == 200 and b.get("code") == 1 and "没有这个档位" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/settings", {"analysis_model": "no-such-model"})
check("POST /api/settings 非法分析档位", st == 200 and b.get("code") == 1 and "没有这个档位" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/settings", {"engine_model": {"deepseek": "deepseek-chat"}, "engine_enabled": {"deepseek": False}})
check("POST /api/settings 合法保存", st == 200 and b.get("code") == 0, f"data={b.get('data')}")

st, b = call("POST", "/api/settings/keys/test", {"engine_code": "deepseek"})
check("POST keys/test 无钥匙", st == 200 and b.get("code") == 0 and b.get("data", {}).get("ok") is False
      and "还没填" in b.get("data", {}).get("message", ""), f"data={b.get('data')}")

st, b = call("POST", "/api/settings/keys/test", {"engine_code": "bad"})
check("POST keys/test 非法引擎", st == 200 and b.get("code") == 1 and "没找到这家引擎" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/settings/keys/test", {"engine_code": "analysis"})
check("POST keys/test analysis 无钥匙", st == 200 and b.get("code") == 0 and b.get("data", {}).get("ok") is False,
      f"data={b.get('data')}")

# 还原 deepseek 开关
call("POST", "/api/settings", {"engine_enabled": {"deepseek": True}})

# ---- 7. 优化 ----
st, b = call("POST", "/api/optimize", {"type": "url", "url": ""})
check("POST optimize url 空链接", st == 200 and b.get("code") == 1 and "链接" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/optimize", {"type": "url", "url": "ftp://abc.com"})
check("POST optimize url 非http(s)", st == 200 and b.get("code") == 1 and "http" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/optimize", {"type": "text", "content": ""})
check("POST optimize text 空内容", st == 200 and b.get("code") == 1 and "粘贴" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/optimize", {"type": "text", "content": "太短了"})
check("POST optimize text 少于20字", st == 200 and b.get("code") == 1 and "20" in msg(b), f"msg={msg(b)}")

st, b = call("POST", "/api/optimize", {"type": "badtype", "content": "内容"})
check("POST optimize 非法type", st == 200 and b.get("code") == 1 and "输入方式" in msg(b), f"msg={msg(b)}")

# 超长 text 50001 字（后端应截断而非报错；无钥匙则后台失败）
long_text = "字" * 50001
st, b = call("POST", "/api/optimize", {"type": "text", "content": long_text})
check("POST optimize text 超50000字(后端截断策略)", st == 200 and b.get("code") == 0,
      f"msg={msg(b)} record_id={b.get('data')}")

st, b = call("GET", "/api/optimize/999999")
check("GET optimize 记录不存在", st == 200 and b.get("code") == 1 and "不存在" in msg(b), f"msg={msg(b)}")

# ---- 8. 报告 ----
st, b = call("GET", "/api/report/trend?metric=bad")
check("GET trend 非法指标", st == 200 and b.get("code") == 1 and "指标" in msg(b), f"msg={msg(b)}")

st, b = call("GET", "/api/report/competitor?round_id=999999")
check("GET competitor 轮次不存在", st == 200 and b.get("code") == 1 and "不存在" in msg(b), f"msg={msg(b)}")

st, b = call("GET", "/api/monitor/rounds/999999")
check("GET round detail 轮次不存在", st == 200 and b.get("code") == 1 and "不存在" in msg(b), f"msg={msg(b)}")

# ---- 9. 预警已读 ----
st, b = call("POST", "/api/alerts/999999/read")
check("POST alerts/read 不存在", st == 200 and b.get("code") == 1 and "不存在" in msg(b), f"msg={msg(b)}")

# ---- 10. 404/405 ----
st, b = call("GET", "/api/no_such_api")
check("GET /api/不存在接口 404", st == 404 and b.get("code") == 1, f"HTTP={st} msg={msg(b)}")

st, b = call("GET", "/api/monitor/start")
check("GET /api/monitor/start 方法不允许 405", st == 405 and b.get("code") == 1, f"HTTP={st} msg={msg(b)}")

print(f"\n========== 结果: PASS={PASS} FAIL={FAIL} ==========")
with open(r"C:\Users\zlb19\AppData\Local\Temp\opencode\geo_test\edge_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(RESULTS))
sys.exit(1 if FAIL else 0)
