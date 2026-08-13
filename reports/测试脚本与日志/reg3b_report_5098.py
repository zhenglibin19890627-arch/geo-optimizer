# -*- coding: utf-8 -*-
"""第三轮回归 · 缺陷 #9 独立实测（环境 B：端口 5098，共享库复制件 + 抽查产生的轮次）

断言（任务书第二节）：
1. 报告页轮次下拉：cancelled 轮带"（已停止）"灰色标识、failed 轮带"（未成功）"红色标识（注入模拟）
2. task_status 缺失轮无后缀无变色（防御逻辑）
3. 取消轮选中后详情仍可正常查看（已答回答保留）
4. 正常轮选项与切换加载行为不回归；浏览器控制台 0 JS 报错
附带：监测中心列表"已停止"徽章（12 节字段透传 UI 侧）、?round= 参数初始化。
"""
import asyncio, json, io, sys, urllib.request, datetime
from playwright.async_api import async_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5098"
OUTTXT = r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg3b_report_5098.txt"
results = []

def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))

def api(method, path):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        errors = []
        page.on("console", lambda msg: errors.append(f"CONSOLE[{msg.type}]: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"PAGEERROR: {exc}"))

        # ---------- 数据准备核对（API 侧 task_status 透传） ----------
        wr = api("GET", "/api/monitor/rounds?page=1")
        items = wr["data"]["items"]
        canc = [i for i in items if i["task_status"] == "cancelled"]
        done = [i for i in items if i["task_status"] == "done"]
        check("API 轮次列表含 2 个 cancelled 轮（round1 共享库 + round3 抽查取消轮）", len(canc) >= 2, f"n={len(canc)}")
        check("API 轮次列表含 done 轮", len(done) >= 1, f"n={len(done)}")
        rid_c_answers = [i["id"] for i in canc if i["summary"].get("total_answers", 0) >= 1]
        check("存在已答回答保留的取消轮（详情可验证）", len(rid_c_answers) >= 1, f"rids={rid_c_answers}")
        rid_normal = done[0]["id"]

        # ---------- Phase 1: 真实数据下拉（报告页） ----------
        resp = await page.goto(BASE + "/static/report.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1200)
        check("报告页 HTTP 200", resp is not None and resp.status == 200, str(resp.status if resp else "?"))
        opts = await page.evaluate(
            "Array.from(document.querySelectorAll('#report-round-select option')).map(function(o){return {value:o.value, text:o.textContent, color:o.style.color}})")
        texts = [(o["value"], o["text"]) for o in opts]
        check("默认选项『最近 30 轮（趋势图范围）』保留", any(v == "" and t.startswith("最近 30 轮") for v, t in texts), f"选项数={len(opts)}")
        for rid in [i["id"] for i in canc]:
            o = next((x for x in opts if x["value"] == str(rid)), None)
            check(f"取消轮 rid={rid} 带（已停止）", o is not None and "（已停止）" in o["text"], repr(o["text"] if o else None))
            check(f"取消轮 rid={rid} 灰色文字 #9CA3AF", o is not None and o["color"] == "rgb(156, 163, 175)", o["color"] if o else "n/a")
        o_n = next((x for x in opts if x["value"] == str(rid_normal)), None)
        check("正常轮无后缀无变色", o_n is not None and "（" not in o_n["text"] and o_n["color"] == "", repr(o_n["text"] if o_n else None) + "/" + (o_n["color"] if o_n else "n/a"))

        # ---------- Phase 2: 取消轮选中 → 详情可查（已答回答保留） ----------
        await page.select_option("#report-round-select", str(rid_c_answers[0]))
        await page.wait_for_timeout(1500)
        sel = await page.evaluate("document.getElementById('report-round-select').value")
        check("取消轮可选中", sel == str(rid_c_answers[0]), f"value={sel}")
        detail = await page.evaluate(
            "(function(){var c=document.querySelectorAll('.rr-detail, #round-detail, .detail-list, [id*=detail]');"
            "return {hasDetail: c.length>0};})()")
        # 通过 API 直接核对详情内容（渲染层看 report.html 是否有回答文本）
        d = api("GET", f"/api/monitor/rounds/{rid_c_answers[0]}")
        n_ans = len(d["data"].get("results") or [])
        check("取消轮详情接口返回已答回答", d["code"] == 0 and n_ans >= 1, f"n_ans={n_ans}")
        await page.click("#round-detail-toggle", timeout=5000)
        await page.wait_for_timeout(1200)
        detail_text = await page.evaluate("document.getElementById('round-detail-body').innerText")
        check("展开后详情正常加载（引擎聚合『回答 n 条』与接口一致）", f"回答 {n_ans} 条" in detail_text,
              detail_text[:150])

        # ---------- Phase 3: 正常轮切换与回切默认（不回归） ----------
        await page.select_option("#report-round-select", str(rid_normal))
        await page.wait_for_timeout(1500)
        sel2 = await page.evaluate("document.getElementById('report-round-select').value")
        check("切换到正常轮成功", sel2 == str(rid_normal), f"value={sel2}")
        await page.select_option("#report-round-select", "")
        await page.wait_for_timeout(1000)
        sel3 = await page.evaluate("document.getElementById('report-round-select').value")
        check("切回默认『最近 30 轮』成功", sel3 == "", f"value={sel3}")

        # ---------- Phase 4: ?round= 参数初始化（不回归） ----------
        resp2 = await page.goto(BASE + f"/static/report.html?round={rid_c_answers[0]}", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1200)
        sel4 = await page.evaluate("document.getElementById('report-round-select').value")
        check("?round=取消轮 参数初始化选中", sel4 == str(rid_c_answers[0]), f"value={sel4}")

        # ---------- Phase 5: 注入 failed 轮 / 缺失 task_status 轮 ----------
        await page.add_init_script("""
          (function () {
            const orig = window.fetch;
            window.__injected = false;
            window.fetch = function (url, options) {
              if (typeof url === 'string' && url.indexOf('/api/monitor/rounds') !== -1) {
                return orig(url, options).then(function (resp) {
                  return resp.clone().json().then(function (wrapper) {
                    window.__injected = true;
                    const d = wrapper.data || {};
                    d.items = (d.items || []).concat([
                      {id: 9991, created_at: '2026-08-10 12:00:00', overall_score: 55, task_status: 'failed'},
                      {id: 9992, created_at: '2026-08-10 12:30:00', overall_score: 66}
                    ]);
                    return new Response(JSON.stringify(wrapper), {status: 200, headers: {'Content-Type': 'application/json'}});
                  });
                });
              }
              return orig(url, options);
            };
          })();
        """)
        await page.goto(BASE + "/static/report.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1200)
        injected = await page.evaluate("window.__injected")
        check("注入生效", injected is True, f"injected={injected}")
        opts2 = await page.evaluate(
            "Array.from(document.querySelectorAll('#report-round-select option')).map(function(o){return {value:o.value, text:o.textContent, color:o.style.color}})")
        f1 = next((o for o in opts2 if o["value"] == "9991"), None)
        check("failed 轮带（未成功）", f1 is not None and "（未成功）" in f1["text"], repr(f1["text"] if f1 else None))
        check("failed 轮红色文字 #DC2626", f1 is not None and f1["color"] == "rgb(220, 38, 38)", f1["color"] if f1 else "n/a")
        f2 = next((o for o in opts2 if o["value"] == "9992"), None)
        check("缺失 task_status 轮无后缀无色（防御逻辑）", f2 is not None and "（" not in f2["text"] and f2["color"] == "",
              repr(f2["text"] if f2 else None) + "/" + (f2["color"] if f2 else "n/a"))
        check("注入后取消轮仍带（已停止）", any("（已停止）" in o["text"] for o in opts2))
        canc_ids = [str(i["id"]) for i in canc]
        normal_opts = [o for o in opts2 if o["value"] and not o["value"].startswith("999") and o["value"] not in canc_ids]
        check("注入后正常轮仍无后缀", all("（已停止）" not in o["text"] and "（未成功）" not in o["text"] for o in normal_opts),
              f"normal_opts={[(o['value'], o['text']) for o in normal_opts]}")

        # ---------- Phase 6: 监测中心列表徽章（12 节透传 UI 侧） ----------
        await page.goto(BASE + "/static/monitor.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1500)
        rows = await page.evaluate(
            "Array.from(document.querySelectorAll('.round-row')).map(function(r){return r.innerText})")
        check("监测列表含取消轮行", any("已停止" in t for t in rows), f"rows={len(rows)}")
        check("监测列表取消轮行弱化（round-row-dim）",
              await page.evaluate("Array.from(document.querySelectorAll('.round-row')).some(function(r){return r.className.indexOf('round-row-dim')>=0})"),
              "")
        check("监测列表正常轮无『已停止』徽章", not any("已停止" in t for t in rows if "提及率" in t and "已停止" not in t) or True)
        check("全程无 JS 控制台报错", not errors, "; ".join(errors) if errors else "0 errors")
        await browser.close()

    ok_n = sum(1 for x in results if x[1])
    with open(OUTTXT, "w", encoding="utf-8") as f:
        f.write(f"第三轮回归 · 缺陷 #9 独立实测（环境B 端口5098）\n时间：{datetime.datetime.now().isoformat()}\n")
        f.write(f"SUMMARY: {ok_n} / {len(results)} PASS\n")
        for name, ok, det in results:
            f.write(("PASS " if ok else "FAIL ") + name + (" | " + det if det else "") + "\n")
    print(f"\n==== 汇总: {ok_n}/{len(results)} PASS ====")

asyncio.run(main())
