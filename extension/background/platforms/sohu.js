// 搜狐号适配器：打开发文编辑器（Quill）→ 注入标题/摘要/正文（四级策略+填充验证）
// → 发布按钮级联点击 → 提交确认与验证；无法确认提交时降级为人工发布指引（不误发）。
// 注入策略参考 weiqi 对同一编辑器的行为观察，代码全部自研。

import { renderMarkdown } from "../render.js";
import { evalInTab, waitForTabComplete, waitForConditionInTab } from "./dom.js";

const EDITOR_URL = "https://mp.sohu.com/mpfe/v4/contentManagement/news/addarticle";

export async function publish({ post, log }) {
  const { html } = renderMarkdown(post.body_md);
  await log("打开搜狐号发文编辑器");
  const tab = await chrome.tabs.create({ url: EDITOR_URL, active: false });
  try {
    await waitForTabComplete(tab.id);
    const loggedIn = await waitForConditionInTab(
      tab.id,
      `!location.href.includes('mpfe/v4/login') && !location.href.includes('passport.sohu.com')`,
      15000,
    );
    if (!loggedIn) throw new Error("搜狐号未登录（跳转到了登录页）");

    await log("等待编辑器加载（Quill）");
    const editorReady = await waitForConditionInTab(
      tab.id,
      `document.querySelector('.ql-editor[contenteditable="true"], [contenteditable="true"]')`,
      30000,
    );
    if (!editorReady) throw new Error("编辑器未找到（页面改版，需核对接入点）");

    await log("注入标题、摘要与正文（四级策略+填充验证）");
    const filled = await evalInTab(tab.id, `
      const TITLE = ${JSON.stringify(post.title)};
      const SUMMARY = ${JSON.stringify(post.summary || "")};
      const HTML = ${JSON.stringify(html)};
      // ---- 标题与摘要：原生赋值 + 事件 ----
      const titleEl = document.querySelector(
        'input[placeholder*="标题"], textarea[placeholder*="标题"], .publish-title input, .article-title input, .title-input input, input[placeholder*="title"]');
      if (titleEl) geoSetNativeValue(titleEl, TITLE);
      const summaryEl = document.querySelector(
        'textarea.abstract-main-textarea, textarea[placeholder*="摘要"]');
      if (summaryEl && SUMMARY) geoSetNativeValue(summaryEl, SUMMARY);
      // ---- 正文：定位 Quill 编辑区 ----
      const editor = document.querySelector('.ql-editor[contenteditable="true"]')
        || document.querySelector('[contenteditable="true"]');
      if (!editor) return { ok: false, reason: "编辑器元素未找到" };
      const targetLen = HTML.replace(/<[^>]+>/g, "").replace(/\\s+/g, "").length;
      const filledLen = () => (editor.textContent || "").replace(/\\s+/g, "").length;
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
      const clearEditor = () => {
        try { editor.focus(); } catch (e) {}
        editor.innerHTML = "";
        editor.dispatchEvent(new Event("input", { bubbles: true }));
      };
      const strategies = [
        // 1. Quill 实例直插（经 DOM 节点找 __quill/.quill/__vue__ 引用）
        () => {
          const okQ = (q) => q && q.clipboard
            && typeof q.clipboard.dangerouslyPasteHTML === "function"
            && typeof q.setText === "function";
          const fromNode = (node, seen) => {
            if (!node || typeof node !== "object" || seen.has(node)) return null;
            seen.add(node);
            if (okQ(node.__quill)) return node.__quill;
            if (okQ(node.quill)) return node.quill;
            if (node.__vue__) {
              const refs = node.__vue__.$refs || {};
              for (const k of Object.keys(refs)) {
                const hit = fromNode(refs[k], seen) || fromNode(node.__vue__.$data, seen);
                if (hit) return hit;
              }
            }
            return null;
          };
          let quill = null;
          for (const host of document.querySelectorAll("#editor, .ql-container, .ql-editor")) {
            quill = fromNode(host, new Set());
            if (quill) break;
          }
          if (!quill) return false;
          clearEditor();
          quill.clipboard.dangerouslyPasteHTML(HTML);
          return true;
        },
        // 2. 剪贴板粘贴事件模拟
        () => { clearEditor(); editor.focus(); return firePaste(); },
        // 3. execCommand insertHTML
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
        reason: used < 0 ? "四级注入后字符数校验未过（target=" + targetLen + " got=" + filledLen() + "）" : "",
        titleFilled: !!titleEl,
        targetLen,
      };
    `);
    if (!filled || !filled.ok) {
      throw new Error("搜狐号正文注入未通过校验：" + ((filled && filled.reason) || "页面无响应")
        + "——未发布任何内容，请人工检查");
    }
    await log("注入完成（策略#" + filled.strategy + "，标题" + (filled.titleFilled ? "✓" : "✗") + "），触发发布");
    await new Promise((r) => setTimeout(r, 800));

    // ---- 发布按钮：类名候选 → 精确文本 → 包含文本 ----
    const clicked = await evalInTab(tab.id, `
      const btns = () => Array.from(document.querySelectorAll(
        'button, .btn, [role="button"], a.btn, input[type="submit"]'))
        .filter((b) => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
      const byClass = document.querySelector(
        '.publish-btn, .btn-publish, .submit-btn, .btn-submit, [class*="publish-btn"], [class*="btn-publish"]');
      if (byClass && !byClass.disabled) { byClass.click(); return "class"; }
      const exact = btns().find((b) => ["发布", "发布文章", "发表"].includes((b.textContent || "").trim()));
      if (exact) { exact.click(); return "text-exact"; }
      const partial = btns().find((b) => /发布|发表/.test((b.textContent || "").trim())
        && !/定时|预览|存草稿|草稿/.test((b.textContent || "").trim()));
      if (partial) { partial.click(); return "text-partial"; }
      return "";
    `);

    // ---- 提交确认：处理可能的确认弹窗，等待离开编辑器或出现成功提示 ----
    await new Promise((r) => setTimeout(r, 1500));
    await evalInTab(tab.id, `
      const dlg = document.querySelector('.modal.show button.primary, .el-dialog button.primary, [class*="dialog"] button.primary');
      if (dlg) { dlg.click(); }
    `);
    const confirmed = await waitForConditionInTab(
      tab.id,
      `!location.pathname.includes('addarticle')
        || /发布成功|发表成功|成功发布/.test((document.body.textContent || ""))
        || !!document.querySelector('[class*="toast-success"], [class*="success-toast"], [class*="message-success"]')`,
      20000,
      1000,
    );

    if (confirmed) {
      const url = await evalInTab(
        tab.id,
        `return location.href;`,
      );
      return { url };
    }
    throw new Error(
      "内容已注入编辑器（搜狐自动保存草稿），但发布提交未能自动确认"
      + (clicked ? "（已点击「" + clicked + "」按钮但未见成功反馈）" : "（未找到发布按钮）")
      + "——请到 " + EDITOR_URL + " 人工确认发布，未发出任何误内容");
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}
