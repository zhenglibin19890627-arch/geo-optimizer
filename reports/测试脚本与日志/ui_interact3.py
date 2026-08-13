import asyncio

async def main():
    from playwright.async_api import async_playwright
    results = []
    def check(name, cond, detail=""):
        results.append((name, cond, detail))
        print(f"[{'PASS' if cond else 'FAIL'}] {name} | {detail}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        page.on("pageerror", lambda exc: print(f"PAGEERROR: {exc}"))

        await page.goto("http://127.0.0.1:5080/static/monitor.html", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 点击引擎行左侧标签文字（未填引擎，JS click 模拟真实用户点击）
        await page.evaluate("document.querySelector('#e-list .disabled .label-text').click()")
        await page.wait_for_timeout(400)
        toast = await page.evaluate("document.querySelector('.toast') ? document.querySelector('.toast').textContent : ''")
        check("监测中心 未填引擎点击提示", "钥匙（API Key）还没填" in toast, f"toast={toast}")

        # 等待 toast 消失
        await page.wait_for_timeout(3000)

        # 停止按钮在无任务时应隐藏（进度卡隐藏）
        stop_visible = await page.evaluate("(() => { const c = document.getElementById('mon-progress-card'); return !c.classList.contains('hidden'); })()")
        check("无进行中任务时进度卡隐藏(停止按钮不可见)", stop_visible is False, f"card visible={stop_visible}")

        # 手动粘贴：空问题 → 空回答（分两次，等待 toast 过期）
        await page.click("#paste-toggle")
        await page.wait_for_timeout(300)
        await page.click("#paste-submit")
        await page.wait_for_timeout(400)
        t1 = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
        check("粘贴 空问题拦截", "问题内容" in t1, f"toast={t1}")
        await page.wait_for_timeout(3000)
        await page.fill("#paste-question", "测试问题")
        await page.click("#paste-submit")
        await page.wait_for_timeout(400)
        t2 = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
        check("粘贴 空回答拦截", "回答内容" in t2, f"toast={t2}")

        # 粘贴成功（用当前品牌：全新用户品牌）
        await page.wait_for_timeout(3000)
        await page.fill("#paste-answer", "全新用户品牌质量很不错，值得推荐，比竞品A好。来源：https://www.zhihu.com/question/1")
        await page.click("#paste-submit")
        await page.wait_for_timeout(1500)
        pres = await page.evaluate("document.getElementById('paste-result') ? document.getElementById('paste-result').innerText : ''")
        check("粘贴 成功结果展示", "已记录" in pres and "提到你 1 次" in pres, f"result={pres[:120]}")

        # ============ 问题库 ============
        await page.goto("http://127.0.0.1:5080/static/questions.html", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        txt = await page.evaluate("document.body.innerText")
        check("问题库 列表+预置标签", "我的问题库" in txt and "预置" in txt, "qbank")

        await page.click("#exp-generate")
        await page.wait_for_timeout(400)
        t = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
        check("问题库 空关键词拦截", "至少一个关键词" in t, f"toast={t}")
        await page.wait_for_timeout(3000)
        await page.fill("#exp-keywords", "婴儿车")
        await page.click("#exp-generate")
        await page.wait_for_timeout(1500)
        t = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
        check("问题库 生成失败(无钥匙)提示", "钥匙" in t, f"toast={t}")

        # 手动添加
        await page.click("#qbank-add-btn")
        await page.wait_for_timeout(400)
        await page.fill("#add-text", "测试手动添加的问题B")
        await page.click("#add-save")
        await page.wait_for_timeout(1000)
        txt = await page.evaluate("document.body.innerText")
        check("手动添加成功(手动标签)", "测试手动添加的问题B" in txt and "手动" in txt, "added")

        # 删除确认
        await page.click('[data-del]')
        await page.wait_for_timeout(400)
        confirm = await page.evaluate("document.querySelector('.modal-confirm') ? document.querySelector('.modal-confirm').innerText : ''")
        check("删除确认文案", "删除后无法找回" in confirm and "确认删除" in confirm, confirm[:60])
        await page.click(".modal-confirm [data-act=ok]")
        await page.wait_for_timeout(800)
        txt = await page.evaluate("document.body.innerText")
        check("确认删除后移除", "测试手动添加的问题B" not in txt, "deleted")

        # 参与监测开关
        switches = await page.query_selector_all("#qbank-list .switch input")
        check("参与监测开关数量", len(switches) >= 37, f"count={len(switches)}")
        await page.evaluate("document.querySelectorAll('#qbank-list .switch input')[0].click()")
        await page.wait_for_timeout(1000)
        t = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
        check("开关保存提示", "参与监测" in t, f"toast={t}")
        await page.evaluate("document.querySelectorAll('#qbank-list .switch input')[0].click()")
        await page.wait_for_timeout(500)

        # ============ 设置页 ============
        await page.goto("http://127.0.0.1:5080/static/settings.html", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        txt = await page.evaluate("document.body.innerText")
        check("设置页 全未填横幅", "先填一把钥匙" in txt, "banner")
        check("设置页 钥匙写法合规", txt.count("钥匙（API Key）") >= 4 and "API Key" not in txt.replace("钥匙（API Key）", ""), f"count={txt.count('钥匙（API Key）')}")
        check("设置页 数据本地说明", "你自己电脑" in txt, "local data")
        check("设置页 定时固定文案", "保持程序开着，才能每天自动监测" in txt, "schedule")
        check("设置页 费用空状态", "还没有监测记录" in txt, "cost empty")

        await page.fill("#set-brand-desc", "好" * 300)
        n = await page.evaluate("document.getElementById('set-brand-desc').value.length")
        c = await page.evaluate("document.getElementById('brand-desc-count').textContent")
        check("品牌介绍200字截断", n == 200 and c == "200/200 字", f"{n} {c}")

        await page.fill("#set-brand-name", "")
        await page.click("#brand-save")
        await page.wait_for_timeout(400)
        t = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
        check("设置页 空品牌名拦截", "品牌名" in t, f"toast={t}")

        await page.evaluate("document.getElementById('schedule-enabled').click()")
        await page.wait_for_timeout(300)
        disabled = await page.evaluate("document.getElementById('schedule-time').disabled")
        check("定时关闭时间禁用", disabled, "")
        await page.evaluate("document.getElementById('schedule-enabled').click()")
        await page.wait_for_timeout(300)
        await page.fill("#schedule-time", "09:00")
        await page.click("#schedule-save")
        await page.wait_for_timeout(500)
        t = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
        check("定时保存提示", "定时设置已保存" in t, f"toast={t}")
        await page.fill("#schedule-time", "08:30")
        await page.click("#schedule-save")
        await page.wait_for_timeout(500)

        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(1500)
        test_btns = await page.query_selector_all("[data-test]")
        check("无钥匙时无测试按钮", len(test_btns) == 0, f"count={len(test_btns)}")
        go_links = await page.query_selector_all("a[target=_blank]")
        check("去平台拿钥匙链接5个", len(go_links) == 5, f"count={len(go_links)}")

        select = await page.query_selector("#tiers-list select")
        if select:
            opts = await select.evaluate("s => s.options.length")
            check("档位下拉存在", opts >= 2, f"options={opts}")
            current_opt = await select.evaluate("s => s.selectedIndex")
            await select.select_option(index=1 if current_opt != 1 else 2)
            await page.wait_for_timeout(1000)
            t = await page.evaluate("Array.from(document.querySelectorAll('.toast')).map(t=>t.textContent).join('|')")
            check("档位切换保存提示", "已切换为" in t, f"toast={t}")
            await select.select_option(index=current_opt)
            await page.wait_for_timeout(800)

        await browser.close()

    fails = [r for r in results if not r[1]]
    print(f"\n========== 交互测试2: PASS={len(results)-len(fails)} FAIL={len(fails)} ==========")
    for name, cond, detail in fails:
        print(f"  FAIL: {name} | {detail}")
    import sys
    sys.exit(1 if fails else 0)

asyncio.run(main())
