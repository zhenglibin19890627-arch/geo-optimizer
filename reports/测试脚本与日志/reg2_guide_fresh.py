# -*- coding: utf-8 -*-
"""回归#2/#5/#7a：全新库浏览器实测（引导全流程/占位符提示/favicon 无 404）。5096。"""
import asyncio, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5096"

async def main():
    from playwright.async_api import async_playwright
    results = []
    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()

        # ============ #2 引导全流程 ============
        bad = []
        page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.on("pageerror", lambda e: bad.append(f"JSERROR {e}"))

        await page.goto(BASE + "/static/index.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(800)
        guide = await page.query_selector(".guide-modal")
        check("#2a 全新库打开首页弹出引导", bool(guide))
        if guide:
            step1_title = await page.evaluate("document.querySelector('#guide-content .guide-step-title')?.innerText || ''")
            check("#2b 步骤1标题为'告诉我你的品牌'", step1_title == "告诉我你的品牌", step1_title)

            # 空品牌名拦截
            await page.click("#guide-next")
            await page.wait_for_timeout(500)
            toast = await page.evaluate("document.querySelector('.toast')?.innerText || ''")
            check("#2c 空品牌名拦截 Toast", "请先填品牌名" in toast, toast)
            cur_title = await page.evaluate("document.querySelector('#guide-content .guide-step-title')?.innerText || ''")
            check("#2d 拦截后仍在步骤1", cur_title == "告诉我你的品牌")

            # 填品牌名 -> 保存并下一步
            await page.fill("#g-brand-name", "回归测试品牌")
            await page.fill("#g-brand-product", "婴儿推车")
            await page.click("#guide-next")
            await page.wait_for_timeout(800)
            toast2 = await page.evaluate("document.querySelector('.toast')?.innerText || ''")
            step2_title = await page.evaluate("document.querySelector('#guide-content .guide-step-title')?.innerText || ''")
            check("#2e 保存后 Toast '品牌信息已保存'", "品牌信息已保存" in toast2, toast2)
            check("#2f 进入步骤2'去设置页填钥匙'", step2_title == "去设置页填钥匙", step2_title)

            # 圆点跳回步骤1，品牌名回填
            await page.click('.guide-dot[data-g="0"]')
            await page.wait_for_timeout(500)
            backfill = await page.evaluate("document.querySelector('#g-brand-name')?.value || ''")
            check("#2g 圆点跳回步骤1 品牌名回填", backfill == "回归测试品牌", backfill)

            # 再跳步骤2 -> 步骤2"先跳过，以后填"关闭
            await page.click('.guide-dot[data-g="1"]')
            await page.wait_for_timeout(500)
            await page.click("#guide-skip")
            await page.wait_for_timeout(500)
            closed = await page.query_selector(".guide-modal")
            check("#2h 步骤2'先跳过，以后填'关闭弹窗", not closed)

            # 刷新后重新弹出（条件未满足：无钥匙/无轮次）
            await page.reload(wait_until="networkidle")
            await page.wait_for_timeout(800)
            guide2 = await page.query_selector(".guide-modal")
            check("#2i 刷新后引导重新弹出（不回归）", bool(guide2))

            # 引导品牌名 50 字截断 + Toast
            await page.click(".guide-dot[data-g='0']")
            await page.wait_for_timeout(400)
            await page.fill("#g-brand-name", "")
            await page.type("#g-brand-name", "超" * 60)
            await page.wait_for_timeout(400)
            v = await page.evaluate("document.querySelector('#g-brand-name').value.length")
            t = await page.evaluate("document.querySelector('.toast')?.innerText || ''")
            check("#2j 引导输入60字截断为50", v == 50, f"len={v}")
            check("#2k 截断 Toast 提示", "品牌名太长了" in t, t)

        # ============ #5 问题库占位符提示 ============
        await page.goto(BASE + "/static/questions.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(800)
        body_txt = await page.evaluate("document.body.innerText")
        check("#5 问题库页占位符提示", "含{品类}的问题，记得把{品类}换成你的产品再监测" in body_txt)

        # ============ #7a favicon + 6 页无 404 ============
        bad2 = []
        page.on("response", lambda r: bad2.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        for name in ["index", "monitor", "optimize", "questions", "report", "settings"]:
            await page.goto(f"{BASE}/static/{name}.html", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(500)
        check("#7a 6 页加载 0 个 4xx 响应", len(bad2) == 0, "; ".join(bad2[:5]))
        resp = await page.request.get(BASE + "/static/favicon.svg")
        check("#7a favicon.svg HTTP 200", resp.status == 200, f"http={resp.status}")
        link = await page.evaluate("document.querySelector('link[rel=\"icon\"]')?.getAttribute('href') || ''")
        check("#7a head 有 favicon 声明", "favicon.svg" in link, link)
        check("#2 全程无 JS 运行时错误", not any("JSERROR" in b for b in bad),
              "; ".join([b for b in bad if "JSERROR" in b][:3]))

        await browser.close()

    print()
    print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
    for n, ok in results:
        if not ok:
            print("FAILED:", n)

asyncio.run(main())
