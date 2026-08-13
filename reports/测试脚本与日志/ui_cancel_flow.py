import asyncio, json, urllib.request

def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:5080" + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        page.on("pageerror", lambda exc: print(f"PAGEERROR: {exc}"))
        await page.goto("http://127.0.0.1:5080/static/monitor.html", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 只勾选 1 个问题（第 1 个手动问题）和 DeepSeek
        await page.evaluate("""() => {
          const boxes = document.querySelectorAll('#q-list input[type=checkbox]');
          boxes.forEach((b, i) => { b.checked = (i === 0); });
          const eb = document.querySelectorAll('#e-list input[type=checkbox]');
          eb.forEach(b => { b.checked = b.getAttribute('data-ecode') === 'deepseek'; });
        }""")
        q_count = await page.evaluate("document.querySelectorAll('#q-list input:checked').length")
        e_count = await page.evaluate("document.querySelectorAll('#e-list input:checked').length")
        print(f"勾选: {q_count} 问题 / {e_count} 引擎")

        # 点开始监测
        await page.click("#mon-start")
        await page.wait_for_timeout(2500)
        card_visible = await page.evaluate("!document.getElementById('mon-progress-card').classList.contains('hidden')")
        title = await page.evaluate("document.getElementById('mon-progress-title').textContent")
        print(f"进度卡可见={card_visible} 标题={title}")

        # 点停止按钮
        await page.click("#mon-stop")
        await page.wait_for_timeout(400)
        confirm = await page.evaluate("document.querySelector('.modal-confirm') ? document.querySelector('.modal-confirm').innerText : ''")
        print("确认弹窗:", confirm.replace(chr(10), " | ")[:100])

        # 点确定停止
        await page.click(".modal-confirm [data-act=ok]")
        await page.wait_for_timeout(1500)
        toasts = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent)")
        print("Toast:", toasts)
        # 轮询等 cancelled 收尾
        for i in range(15):
            await page.wait_for_timeout(1000)
            title = await page.evaluate("document.getElementById('mon-progress-title').textContent")
            if "停止" in title:
                break
        title = await page.evaluate("document.getElementById('mon-progress-title').textContent")
        sub = await page.evaluate("document.getElementById('mon-progress-sub').textContent")
        btn_text = await page.evaluate("document.getElementById('mon-start').textContent")
        print(f"收尾标题={title} sub={sub} 开始按钮={btn_text}")
        await browser.close()

asyncio.run(main())
