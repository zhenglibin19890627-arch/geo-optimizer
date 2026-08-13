# -*- coding: utf-8 -*-
"""回归#2e 细化：保存后 Toast 与步骤跳转的时序验证。5096。"""
import asyncio, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5096"

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        toasts = []
        page.on("pageerror", lambda e: toasts.append(f"JSERROR {e}"))
        await page.goto(BASE + "/static/index.html", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(800)
        # 填写品牌名并保存
        await page.fill("#g-brand-name", "星辰母婴")
        await page.fill("#g-brand-product", "婴儿推车")
        await page.click("#guide-next")
        # 密集采样 toast 状态与步骤标题
        for i in range(6):
            await page.wait_for_timeout(300)
            state = await page.evaluate("""() => {
                const ts = [...document.querySelectorAll('.toast')].map(t => t.innerText);
                return {
                    toasts: ts,
                    step: document.querySelector('#guide-content .guide-step-title')?.innerText || '',
                    nextBtn: document.querySelector('#guide-next')?.innerText || '',
                };
            }""")
            print(f"t+{i*300}ms:", state)
        await browser.close()

asyncio.run(main())
