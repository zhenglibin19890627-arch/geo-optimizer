import asyncio

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        failed = []
        page.on("response", lambda r: failed.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.on("requestfailed", lambda r: failed.append(f"REQFAILED {r.url} {r.failure}"))
        await page.goto("http://127.0.0.1:5080/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        for f in failed:
            print("NON-2xx:", f)
        # 首页核心内容检查
        txt = await page.evaluate("document.body.innerText")
        print("\n===== 首页可见文本 =====")
        print(txt[:1500])
        # 检查引导弹窗是否存在（全新状态应弹出）
        guide = await page.query_selector("#guide-content, .guide-modal")
        print("\n引导弹窗存在:", bool(guide))
        if guide:
            head = await page.evaluate("document.querySelector('.guide-modal') ? document.querySelector('.guide-modal').innerText : ''")
            print("引导弹窗内容:", head[:300])
        await browser.close()

asyncio.run(main())
