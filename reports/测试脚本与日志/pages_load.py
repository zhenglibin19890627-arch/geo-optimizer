import asyncio, sys

PAGES = [
    ("/", "首页"),
    ("/static/optimize.html", "内容优化"),
    ("/static/monitor.html", "监测中心"),
    ("/static/report.html", "报告"),
    ("/static/questions.html", "问题库"),
    ("/static/settings.html", "设置"),
]

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        errors = []
        page.on("console", lambda msg: errors.append(f"CONSOLE[{msg.type}]: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"PAGEERROR: {exc}"))
        for path, name in PAGES:
            errors.clear()
            resp = await page.goto(f"http://127.0.0.1:5080{path}", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500)
            title = await page.title()
            print(f"=== {name} [{path}] HTTP={resp.status if resp else '?'} title={title!r} ===")
            if errors:
                for e in errors:
                    print(f"  [错误] {e}")
            else:
                print("  控制台无错误")
            # 页面主要容器非空检查
            body_text = await page.evaluate("document.body ? document.body.innerText.length : 0")
            print(f"  body 文本长度: {body_text}")
        await browser.close()

asyncio.run(main())
