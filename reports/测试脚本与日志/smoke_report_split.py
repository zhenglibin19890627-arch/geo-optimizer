"""report.js 拆分后的浏览器冒烟测试（Playwright，headless）。

验证点：
1. 报告页三个拆分文件全部加载、无 JS 报错（console error / pageerror）；
2. 趋势图 canvas 渲染、轮次下拉有数据、信源排行有内容；
3. 切换趋势 Tab（mention_rate）无报错；
4. 竞品深度分析卡片/本轮明细正常。
启动服务：GEO_NO_SCHEDULER=1 python -c "...create_app()...app.run(port=5091)"
"""

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5091"
errors = []
pageerrors = []


def launch(browser_type):
    try:
        return browser_type.launch(headless=True)
    except Exception as e:
        print(f"[fallback] chromium 启动失败（{e}），改用系统 Edge")
        return browser_type.launch(headless=True, channel="msedge")


with sync_playwright() as p:
    browser = launch(p.chromium)
    page = browser.new_page()
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: pageerrors.append(str(e)))

    page.goto(BASE + "/static/report.html", wait_until="networkidle")
    page.wait_for_timeout(2500)

    canvas = page.locator("#trend-chart canvas").count()
    options = page.locator("#report-round-select option").count()
    sources = page.locator("#sources-area").inner_text() if page.locator("#sources-area").count() else ""
    deep_hidden = page.locator("#deep-card").evaluate("el => el.classList.contains('hidden')")
    detail_toggle = page.locator("#round-detail-toggle").count()

    page.click('.tab-item[data-metric="mention_rate"]')
    page.wait_for_timeout(1500)
    page.click('.tab-item[data-metric="sentiment"]')
    page.wait_for_timeout(1500)

    print(f"canvas={canvas} select_options={options} deep_card_hidden={deep_hidden} detail_toggle={detail_toggle}")
    print(f"sources_area_len={len(sources.strip())}")
    print("console_errors=", errors if errors else "NONE")
    print("page_errors=", pageerrors if pageerrors else "NONE")

    browser.close()

    ok = (
        canvas > 0
        and options > 0
        and len(sources.strip()) > 0
        and not errors
        and not pageerrors
        and detail_toggle == 1
    )
    print("SMOKE_RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
