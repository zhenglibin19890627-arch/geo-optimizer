// 今日头条适配器：mp.toutiao.com 图文创作
// 编辑器为 ProseMirror；首版走“打开发布页 → 注入 → 发布”流程，选择器待真机联调核对。

import { renderMarkdown } from "../render.js";
import { evalInTab, waitForTabComplete, waitForConditionInTab } from "./dom.js";

const EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish";

export async function publish({ post, log }) {
  const { html } = renderMarkdown(post.body_md);
  await log("打开头条创作平台");
  const tab = await chrome.tabs.create({ url: EDITOR_URL, active: false });
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

    await log("注入标题与正文（首版联调）");
    await evalInTab(tab.id, `
      const titleEl = document.querySelector('textarea[placeholder*="标题"], input[placeholder*="标题"], .article-input input');
      if (titleEl) geoSetNativeValue(titleEl, ${JSON.stringify(post.title)});
      geoPasteHtml('.ProseMirror, [contenteditable="true"]', ${JSON.stringify(html)});
      return true;
    `);
    throw new Error("头条发布流程待真机联调：已定位编辑器，发布按钮提交逻辑将在联调中补齐（本条不会发布任何内容）");
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}
