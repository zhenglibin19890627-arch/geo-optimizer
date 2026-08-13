# -*- coding: utf-8 -*-
"""第三轮回归 · 缺陷 #8 断言6：首页预警横幅（环境 A：端口 5097，链测试完成后执行）。

验证 watch/warning/恢复三类消息经 /api/overview unread_alerts 渲染为横幅，
标记已读后横幅数量变化（unread 语义前端不回归），全程 0 JS 报错。
"""
import asyncio, json, io, sys, urllib.request, datetime
from playwright.async_api import async_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:5097"
OUTTXT = r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg3a_banner_5097.txt"
results = []

def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))

def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        errors = []
        page.on("console", lambda msg: errors.append(f"CONSOLE[{msg.type}]: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"PAGEERROR: {exc}"))

        resp = await page.goto(BASE + "/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1200)
        check("首页 HTTP 200", resp is not None and resp.status == 200, str(resp.status if resp else "?"))
        banners = await page.evaluate(
            "Array.from(document.querySelectorAll('#alert-area .alert-banner')).map(function(b){return b.textContent.trim()})")
        check("横幅数量=5（未读前 5 条）", len(banners) == 5, f"n={len(banners)}")
        all_text = "|".join(banners)
        check("横幅含恢复消息（已经恢复）", "已经恢复" in all_text, all_text[:200])
        check("横幅含注意类消息（watch/warning）", "注意" in all_text, all_text[:200])

        un = api("GET", "/api/alerts?unread=true")["data"]
        total_unread = un["unread_count"]
        top5 = [a["id"] for a in un["items"][:5]]
        for aid in top5:
            api("POST", f"/api/alerts/{aid}/read")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(1200)
        banners2 = await page.evaluate(
            "Array.from(document.querySelectorAll('#alert-area .alert-banner')).map(function(b){return b.textContent.trim()})")
        check("标记 5 条已读后横幅=剩余未读（%d）" % max(0, total_unread - 5), len(banners2) == max(0, total_unread - 5),
              f"n={len(banners2)} total_unread={total_unread}")
        txt2 = "|".join(banners2)
        check("此时横幅含 watch 级消息", "明显变少" in txt2 or "评分下滑" in txt2 or "评价变差" in txt2, txt2[:200])

        rest = api("GET", "/api/alerts?unread=true")["data"]["items"]
        for a in rest:
            api("POST", f"/api/alerts/{a['id']}/read")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(1200)
        banners3 = await page.evaluate(
            "document.querySelectorAll('#alert-area .alert-banner').length")
        check("全部标记已读后横幅=0", banners3 == 0, f"n={banners3}")
        check("全程无 JS 控制台报错", not errors, "; ".join(errors) if errors else "0 errors")
        await browser.close()

    ok_n = sum(1 for x in results if x[1])
    with open(OUTTXT, "w", encoding="utf-8") as f:
        f.write(f"第三轮回归 · 首页预警横幅（环境A 端口5097）\n时间：{datetime.datetime.now().isoformat()}\n")
        f.write(f"SUMMARY: {ok_n} / {len(results)} PASS\n")
        for name, ok, det in results:
            f.write(("PASS " if ok else "FAIL ") + name + (" | " + det if det else "") + "\n")
    print(f"\n==== 汇总: {ok_n}/{len(results)} PASS ====")

asyncio.run(main())
