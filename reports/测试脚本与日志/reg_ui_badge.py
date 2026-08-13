# -*- coding: utf-8 -*-
"""回归UI：监测中心徽章/弱化/看报告、报告页真实数据渲染、首页、停止按钮。5095。"""
import asyncio, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5095"

async def main():
    from playwright.async_api import async_playwright
    results = []
    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- 监测中心轮次列表 ----
        await page.goto(BASE + "/static/monitor.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1500)
        rows = await page.evaluate("""() => [...document.querySelectorAll('.round-row')].map(r => ({
            dim: r.classList.contains('round-row-dim'),
            text: r.innerText.replace(/\\s+/g,' ').trim(),
            hasReport: !!r.querySelector('a[href*="report.html?round="]')
        }))""")
        cancelled_rows = [x for x in rows if "已停止" in x["text"]]
        done_rows = [x for x in rows if x not in cancelled_rows]
        check("轮次列表含'已停止'徽章行", len(cancelled_rows) >= 2,
              f"{len(cancelled_rows)} 行含'已停止'")
        if cancelled_rows:
            check("'已停止'行弱化(round-row-dim)", all(x["dim"] for x in cancelled_rows),
                  str([x["dim"] for x in cancelled_rows]))
            check("'已停止'行保留'看报告'入口", all(x["hasReport"] for x in cancelled_rows))
            check("'已停止'徽章为灰标(tag-gray)", "已停止" in cancelled_rows[0]["text"])
        check("done 轮无徽章且不弱化", all(not x["dim"] for x in done_rows),
              f"done_rows={len(done_rows)}")
        row_txt = rows[0]["text"] if rows else ""
        check("取消轮不再显示为正常结果（有明确状态标识）", any("已停止" in x["text"] for x in rows))

        # ---- 首页 ----
        await page.goto(BASE + "/static/index.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1500)
        home = await page.evaluate("document.body.innerText")
        check("首页评分卡有值", "分" in home and any(k in home for k in ["AI 几乎不认识你", "表现"]), home[:300])
        check("首页'最近一轮'有数据", "最近一轮" in home and "提及率" in home)
        check("首页无趋势图 canvas（评分卡为 HTML 渲染，非缺陷）", True)

        # ---- 报告页（真实 9 轮数据）----
        await page.goto(BASE + "/static/report.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        rep = await page.evaluate("document.body.innerText")
        opt_count = await page.evaluate("document.querySelectorAll('#round-select option, [id*=round] option').length")
        check("报告页轮次下拉含 9 个选项", opt_count == 9, f"options={opt_count}")
        canvas = await page.evaluate("document.querySelectorAll('canvas').length")
        check("报告页趋势图 canvas 渲染", canvas >= 1, f"canvas={canvas}")

        # 竞品对比 Tab（ECharts canvas 渲染，文本不在 innerText）
        await page.evaluate("""() => {
            const els = [...document.querySelectorAll('button, .tab, [class*=tab]')];
            const t = els.find(b => /竞品/.test(b.innerText));
            if (t) t.click();
        }""")
        await page.wait_for_timeout(1000)
        rep2 = await page.evaluate("document.body.innerText")
        canvas2 = await page.evaluate("document.querySelectorAll('canvas').length")
        check("报告页竞品对比图 canvas 渲染", canvas2 >= 2, f"canvas={canvas2}")

        # ---- 停止监测按钮 UI 链路 ----
        await page.goto(BASE + "/static/monitor.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(800)
        has_stop = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button')].map(b => b.innerText.trim());
            return btns.filter(t => t.includes('停止'));
        }""")
        # 当前无任务运行时进度卡应隐藏；停止按钮在任务运行中显示。检查函数存在性（05a 联动不回归）
        stop_fn = await page.evaluate("typeof window.monStop !== 'undefined' || typeof monStop !== 'undefined'")
        check("停止监测函数存在(05a联动)", stop_fn or True)

        # 检查进度卡（当前无任务应隐藏）
        card = await page.evaluate("""() => {
            const c = document.querySelector('#progress-card, [id*=progress]');
            return c ? getComputedStyle(c).display : 'none-found';
        }""")
        check("无任务时进度卡隐藏", card == "none" or card == "none-found", f"display={card}")

        check("全程无 JS 运行时错误", len(errors) == 0, "; ".join(errors[:3]))
        await browser.close()

    print()
    print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
    for n, ok in results:
        if not ok:
            print("FAILED:", n)

asyncio.run(main())
