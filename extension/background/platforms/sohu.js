// 搜狐号适配器：打开发文编辑器（Quill）→ 注入标题与 HTML → 后续发布按钮逻辑真机联调补齐
// 编辑器为 .ql-editor[contenteditable]，正文以 HTML 粘贴注入（搜狐不支持 markdown 直传）。

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

    await log("注入标题、摘要与正文");
    await evalInTab(tab.id, `
      const titleEl = document.querySelector(
        'input[placeholder*="标题"], textarea[placeholder*="标题"], .publish-title input, .article-title input');
      if (titleEl) geoSetNativeValue(titleEl, ${JSON.stringify(post.title)});
      const summaryEl = document.querySelector(
        'textarea.abstract-main-textarea, textarea[placeholder*="摘要"]');
      if (summaryEl && ${JSON.stringify(post.summary || "")}) {
        geoSetNativeValue(summaryEl, ${JSON.stringify(post.summary || "")});
      }
      geoPasteHtml('.ql-editor[contenteditable="true"], [contenteditable="true"]', ${JSON.stringify(html)});
      return true;
    `);
    throw new Error("搜狐号发布按钮提交逻辑待真机联调补齐（本次已验证编辑器可达与注入，未发布任何内容）");
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}
