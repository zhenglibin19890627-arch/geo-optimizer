import json, urllib.request, asyncio

def call(path):
    return json.loads(urllib.request.urlopen("http://127.0.0.1:5080" + path, timeout=30).read().decode("utf-8"))

print("=== 报告相关 API ===")
r = call("/api/overview")
print("[overview] score=", r["data"]["score"], "| score_trend=", r["data"]["score_trend"], "| round_count=", r["data"]["round_count"])
assert r["data"]["score"] is not None and r["data"]["round_count"] == 4, "overview FAIL (含取消轮，见缺陷#4b)"

r = call("/api/report/trend?metric=score")
print("[trend score]", r["data"])
assert len(r["data"]["labels"]) == 4, "trend FAIL（含取消轮，见缺陷#4b）"

r = call("/api/report/trend?metric=mention_rate")
print("[trend mention_rate]", r["data"])

r = call("/api/report/trend?metric=sentiment")
print("[trend sentiment]", r["data"])

r = call("/api/report/competitor")
print("[competitor]", json.dumps(r["data"], ensure_ascii=False)[:300])

r = call("/api/report/sources")
print("[sources]", json.dumps(r["data"], ensure_ascii=False)[:300])

r = call("/api/monitor/rounds/2")
print("[round detail] notes=", r["data"]["notes"])
assert any("混元" in n for n in r["data"]["notes"]), "yuanbao notes FAIL"
print("结果数:", len(r["data"]["results"]), "| 竞品对比:", json.dumps(r["data"]["competitor_compare"], ensure_ascii=False)[:200])
print("信源:", json.dumps(r["data"]["sources_top"], ensure_ascii=False)[:200])

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        page.on("pageerror", lambda exc: print(f"PAGEERROR: {exc}"))
        page.on("console", lambda msg: print(f"CONSOLE[{msg.type}]: {msg.text[:150]}") if msg.type == "error" else None)
        await page.goto("http://127.0.0.1:5080/static/report.html", wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # 趋势图 canvas 是否渲染
        trend_canvas = await page.evaluate("document.querySelectorAll('#trend-chart canvas').length")
        comp_canvas = await page.evaluate("document.querySelectorAll('#competitor-chart canvas').length")
        print(f"[页面] 趋势canvas={trend_canvas} 竞品canvas={comp_canvas}")

        # 信源列表
        sources_txt = await page.evaluate("document.getElementById('sources-area').innerText")
        print("[页面] 信源区:", sources_txt[:200].replace(chr(10), " | "))

        # 轮次下拉
        sel = await page.evaluate("""() => {
          const s = document.getElementById('report-round-select');
          return { options: s.options.length, texts: Array.from(s.options).map(o=>o.text).slice(0,5) };
        }""")
        print("[页面] 轮次下拉:", sel)

        # 引擎明细（含元宝口径说明）
        await page.click("#round-detail-toggle")
        await page.wait_for_timeout(1500)
        detail = await page.evaluate("document.getElementById('round-detail-body').innerText")
        print("[页面] 引擎明细:", detail[:300].replace(chr(10), " | "))
        assert "元宝" in detail or "混元" in detail, "yuanbao note NOT rendered"

        # 切换指标 Tab
        await page.click('.tabs .tab-item[data-metric="mention_rate"]')
        await page.wait_for_timeout(1500)
        trend_canvas2 = await page.evaluate("document.querySelectorAll('#trend-chart canvas').length")
        print(f"[页面] 切提及率Tab后 canvas={trend_canvas2}")

        # 首页评分/趋势
        await page.goto("http://127.0.0.1:5080/", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        home = await page.evaluate("document.getElementById('score-area').innerText")
        print("[首页] 评分卡:", home[:150].replace(chr(10), " | "))
        last = await page.evaluate("document.getElementById('last-round-area').innerText")
        print("[首页] 最近一轮:", last[:200].replace(chr(10), " | "))
        # 展开分数明细
        await page.click("#score-expand-toggle")
        await page.wait_for_timeout(400)
        bd = await page.evaluate("document.getElementById('score-expand-body').innerText")
        print("[首页] 分项明细:", bd[:250].replace(chr(10), " | "))
        await browser.close()

asyncio.run(main())
print()
print("=== 报告页渲染验证完成 ===")
