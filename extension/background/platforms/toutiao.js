// 今日头条适配器：mp.toutiao.com 图文创作（ProseMirror 编辑器）
// 流程：打开发布页 → 登录校验 → 注入标题/正文（多策略+填充验证）→ 发布按钮级联
// → 提交确认与验证；无法确认提交时降级为人工发布指引（不误发）。
// 标题候选含 ProseMirror 首行（头条新版标题在编辑器内）与独立输入框两种形态。

import { renderMarkdown } from "../render.js";
import { evalInTab, waitForTabComplete, waitForConditionInTab, realClick, elementCenter } from "./dom.js";

const EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish";

export async function publish({ post, log }) {
  const { html } = renderMarkdown(post.body_md);
  await log("打开头条创作平台（前台标签页）");
  // 前台打开：后台标签页会被 Chrome 降频，部分发布流程在不可见状态下不响应点击（weiqi 同款做法）
  const tab = await chrome.tabs.create({ url: EDITOR_URL, active: true });
  let published = false; // 失败时保留标签页，供人工完成发布
  try {
    await waitForTabComplete(tab.id);
    const loggedIn = await waitForConditionInTab(
      tab.id,
      `!location.href.includes('login') && !location.href.includes('passport')`,
      15000,
    );
    if (!loggedIn) throw new Error("头条未登录（跳转到了登录页）");

    const editorReady = await waitForConditionInTab(
      tab.id,
      `document.querySelector('.ProseMirror, [contenteditable="true"]')`,
      30000,
    );
    if (!editorReady) throw new Error("编辑器未找到（页面改版，需核对接入点）");

    await log("注入标题与正文（多策略+填充验证）");
    const filled = await evalInTab(tab.id, `
      const TITLE = ${JSON.stringify((post.title || "").slice(0, 30))};
      const HTML = ${JSON.stringify(html)};
      // 标题：独立输入框（textarea/input）或 ProseMirror 文档首段（新版内嵌标题）
      const titleEl = document.querySelector(
        'textarea[placeholder*="标题"], input[placeholder*="标题"], .article-input input, .cef-title-editor input, [class*="title"] input, [class*="title"] textarea');
      if (titleEl) {
        geoSetNativeValue(titleEl, TITLE);
      } else {
        const pm = document.querySelector('.ProseMirror');
        const first = pm && (pm.querySelector('h1, .title, [data-title]') || pm.firstElementChild);
        if (first && /^(标题|请输入标题)/.test((first.textContent || "").trim())) {
          geoSetNativeValue(first, TITLE);
        }
      }
      const editor = document.querySelector('.ProseMirror, [contenteditable="true"]');
      if (!editor) return { ok: false, reason: "编辑器元素未找到" };
      const targetLen = HTML.replace(/<[^>]+>/g, "").replace(/\\s+/g, "").length;
      const filledLen = () => (editor.textContent || "").replace(/\\s+/g, "").length;
      const clearEditor = () => {
        try { editor.focus(); } catch (e) {}
        editor.innerHTML = "";
        editor.dispatchEvent(new Event("input", { bubbles: true }));
      };
      const firePaste = () => {
        try {
          const dt = new DataTransfer();
          dt.setData("text/html", HTML);
          const ev = new ClipboardEvent("paste", { bubbles: true, cancelable: true });
          Object.defineProperty(ev, "clipboardData", { get: () => dt });
          editor.dispatchEvent(ev);
          return true;
        } catch (e) { return false; }
      };
      const strategies = [
        // 1. ProseMirror 对 paste 事件响应最好
        () => { clearEditor(); editor.focus(); return firePaste(); },
        // 2. execCommand insertHTML
        () => {
          clearEditor(); editor.focus();
          const ok = document.execCommand("insertHTML", false, HTML);
          if (!ok) editor.innerHTML = HTML;
          editor.dispatchEvent(new Event("input", { bubbles: true }));
          return true;
        },
      ];
      let used = -1;
      for (let i = 0; i < strategies.length; i++) {
        try { strategies[i](); } catch (e) { continue; }
        await new Promise((r) => setTimeout(r, 600));
        if (filledLen() >= Math.min(targetLen, Math.max(1, Math.floor(targetLen * 0.5)))) {
          used = i; break;
        }
      }
      return {
        ok: used >= 0,
        strategy: used,
        reason: used < 0 ? "注入后字符数校验未过（target=" + targetLen + " got=" + filledLen() + "）" : "",
        titleFilled: !!titleEl,
        targetLen,
      };
    `);
    if (!filled || !filled.ok) {
      throw new Error("头条正文注入未通过校验：" + ((filled && filled.reason) || "页面无响应")
        + "——未发布任何内容，请人工检查");
    }
    await log("注入完成（策略#" + filled.strategy + "，标题" + (filled.titleFilled ? "✓" : "✗") + "），触发发布（CDP 真实点击）");
    await new Promise((r) => setTimeout(r, 800));

    // ---- 发布按钮：优先主按钮（byte-btn-primary），坐标定位后 CDP 真实点击 ----
    const btnPos = await elementCenter(tab.id, `
      const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const btns = () => Array.from(document.querySelectorAll(
        'button, .btn, [role="button"], a.btn, input[type="submit"]')).filter(visible);
      let target = btns().find((b) => /byte-btn-primary|btn-primary/.test(String(b.className))
        && /发布|发表|确认/.test((b.textContent || "").trim())
        && !b.disabled && !/disabled/i.test(String(b.className)));
      if (!target) target = document.querySelector('button[class*="publish"], .btn[class*="publish"]');
      if (!target) {
        target = btns().find((b) => ["发布", "发表", "发布文章"].includes((b.textContent || "").trim()));
      }
      window.geoTarget = target || null;
    `);
    if (!btnPos) {
      throw new Error("头条发布按钮未找到——内容已注入，未发布任何内容");
    }
    await realClick(tab.id, btnPos.x, btnPos.y);

    // ---- 提交确认：字节系弹窗/抽屉内的确认按钮，同样 CDP 真实点击，多轮尝试 ----
    let coverLogged = false;
    for (let i = 0; i < 4; i++) {
      await new Promise((r) => setTimeout(r, 1800));
      // 每轮都尝试选「无封面」（抽屉可能晚出现）。「无封面」是单选项：
      // geoOptionTarget 优先点 radio 本体，点后回读 .checked 验证。
      const coverPos = await elementCenter(tab.id, `
        window.geoCoverDone = window.geoCoverDone === true;
        if (!window.geoCoverDone) geoOptionTarget("无封面", 12);
      `);
      if (coverPos) {
        if (!coverLogged) { await log("发表设置：点选「无封面」"); coverLogged = true; }
        await realClick(tab.id, coverPos.x, coverPos.y);
        await new Promise((r) => setTimeout(r, 600));
        const st = await evalInTab(tab.id, `
          if (window.geoOptionInput) {
            window.geoCoverDone = !!window.geoOptionInput.checked;
            return window.geoCoverDone ? "checked" : "not-checked";
          }
          return "no-input";
        `);
        if (st === "checked") await log("发表设置：已确认选中「无封面」");
      }
      let dlgPos = await elementCenter(tab.id, `
        const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const panels = Array.from(document.querySelectorAll(
          '.byte-modal, .byte-drawer, [class*="modal"], [class*="drawer"], [class*="Drawer"], [class*="dialog"], [class*="Dialog"]'))
          .filter(visible);
        window.geoTarget = null;
        for (const d of panels) {
          const btns = Array.from(d.querySelectorAll("button, [role=button]"))
            .filter((b) => visible(b) && /^(确认发布|立即发布|确认|确定|发布|发表)$/.test((b.textContent || "").trim()));
          const hit = btns.find((b) => /byte-btn-primary|btn-primary/.test(String(b.className))) || btns[btns.length - 1];
          if (hit) { window.geoTarget = hit; break; }
        }
      `);
      if (!dlgPos) {
        // 浮层容器类名没匹配上时的兜底：全文按精确文本找确认类按钮。
        // 「预览并发布」不是精确匹配，不会被误点。
        dlgPos = await elementCenter(tab.id, `
          const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 40 && r.height > 10; };
          window.geoTarget = null;
          const cands = Array.from(document.querySelectorAll("button, [role=button], .btn"))
            .filter((b) => visible(b)
              && /^(确认发布|立即发布|确认|确定|发布|发表)$/.test((b.textContent || "").trim()));
          if (!cands.length) return;
          const hit = cands.find((b) => /primary/i.test(String(b.className)))
            || cands[cands.length - 1];
          window.geoTarget = hit;
        `);
      }
      if (dlgPos) {
        if (i === 0) await log("提交确认：点击确认按钮");
        await realClick(tab.id, dlgPos.x, dlgPos.y);
      }
    }
    const confirmed = await waitForConditionInTab(
      tab.id,
      `!location.pathname.includes('graphic/publish')
        || /发布成功|发表成功|成功发布|审核中/.test((document.body.textContent || ""))
        || !!document.querySelector('[class*="toast-success"], [class*="success-toast"], [class*="message-success"], .byte-toast-success')`,
      12000,
      1000,
    );

    if (confirmed) {
      const url = await evalInTab(tab.id, `return location.href;`);
      return { url };
    }
    // 延迟复查：部分提交后跳转较慢，6 秒后再确认一次
    await new Promise((r) => setTimeout(r, 6000));
    const late = await evalInTab(tab.id, `return location.href;`);
    if (!late.includes("graphic/publish")) {
      published = true;
      return { url: late };
    }
    // 强信号检测：①新标签页里的头条文章页 ②页面关键词扫描（发布成功/失败/验证/封面）
    const tabs = await chrome.tabs.query({});
    const articleTab = tabs.find((t) =>
      /toutiao\.com\/(article|a\d)/i.test(t.url || "")
      || /toutiao\.com.*\/articles/i.test(t.url || ""));
    if (articleTab && articleTab.id !== tab.id) {
      published = true;
      return { url: articleTab.url };
    }
    const kw = await evalInTab(tab.id, `
      const txt = (document.body.innerText || "").replace(/\\s+/g, " ");
      const hits = txt.match(/发布成功|发布失败|发布中|验证|扫码|封面|不能为空|内容不含标题|请输入|审核中/g) || [];
      const iframes = Array.from(document.querySelectorAll("iframe")).filter((f) => {
        const r = f.getBoundingClientRect(); return r.width > 50 && r.height > 50;
      }).length;
      return "关键词[" + (hits.slice(0, 8).join(",") || "无") + "] iframe=" + iframes
        + " || 标题=" + document.title.slice(0, 30);
    `);
    // 仍未确认：带回现场信息（URL/页面标题/弹窗/提示文字/按钮）
    const scene = await evalInTab(tab.id, `
      const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const dlgs = Array.from(document.querySelectorAll(
        '.byte-modal, [class*="modal"], [class*="dialog"], [class*="Dialog"], [class*="popup"]')).filter(visible)
        .slice(0, 3).map((d) => String(d.className).slice(0, 40) + "::"
          + (d.innerText || "").replace(/\\s+/g, " ").slice(0, 70));
      const msgs = Array.from(document.querySelectorAll(
        '[class*="toast"], [class*="message"], [class*="notice"], [class*="tip"], [class*="Toast"], [class*="Message"], [class*="byte-toast"]'))
        .slice(0, 5).map((m) => (m.innerText || "").replace(/\\s+/g, " ").slice(0, 60)).filter(Boolean);
      const btnInfo = Array.from(document.querySelectorAll(
        'button, .btn, [role="button"]')).filter(visible).slice(0, 8).map((b) =>
        ((b.textContent || "").trim().slice(0, 10) || "[无文本]") + "|" + String(b.className || "").slice(0, 25));
      return "URL=" + location.href.slice(0, 60)
        + " || 标题=" + document.title.slice(0, 30)
        + " || 弹窗[" + (dlgs.join(" ;; ") || "无") + "]"
        + " || 提示[" + (msgs.join(" ;; ") || "无") + "]"
        + " || 按钮[" + btnInfo.join(" ;; ") + "]";
    `);
    throw new Error(
      "头条发布未能自动确认——编辑器标签页已保留，请手动完成发布（内容已注入，检查右侧「发表设置」后点右上角「发布」即可）。现场： " + kw + " " + scene.slice(0, 300));
  }
  // 无论发布成功与否都不关闭标签页：保留现场供人工核对
}
