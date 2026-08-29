// 搜狐号适配器：mp.sohu.com 图文发布
// 编辑器为自研富文本；首版走“打开发布页 → 注入 → 发布”流程，选择器待真机联调核对。

import { renderMarkdown } from "../render.js";
import { evalInTab, waitForTabComplete, waitForConditionInTab } from "./dom.js";

const EDITOR_URL = "https://mp.sohu.com/main/home/index";

export async function publish({ post, log }) {
  const { html } = renderMarkdown(post.body_md);
  await log("打开搜狐号后台");
  const tab = await chrome.tabs.create({ url: EDITOR_URL, active: false });
  try {
    await waitForTabComplete(tab.id);
    // 发布入口在后台首页；未登录会跳转到登录页
    const loggedIn = await waitForConditionInTab(
      tab.id,
      `!location.href.includes('passport.sohu.com')`,
      15000,
    );
    if (!loggedIn) throw new Error("搜狐号未登录（跳转到了登录页）");

    // 打开发文页：后台首页的「发文」按钮（或直接进入图文编辑路由）
    await evalInTab(tab.id, `
      const btn = Array.from(document.querySelectorAll('a,button,div'))
        .find((el) => (el.textContent || '').trim() === '发文');
      if (btn) { btn.click(); return true; }
      location.href = 'https://mp.sohu.com/main/news/edit';
      return true;
    `);
    await log("等待图文编辑器加载");
    const editorReady = await waitForConditionInTab(
      tab.id,
      `document.querySelector('.edui-editor, .UEditor, [contenteditable="true"], iframe.edui')`,
      30000,
    );
    if (!editorReady) throw new Error("编辑器未找到（页面改版，需核对接入点）");

    await log("注入标题与正文（首版联调）");
    await evalInTab(tab.id, `
      const titleEl = document.querySelector('input[placeholder*="标题"], .title-input input, input.title');
      if (titleEl) geoSetNativeValue(titleEl, ${JSON.stringify(post.title)});
      const editor = document.querySelector('[contenteditable="true"]') ||
        (document.querySelector('iframe.edui, .edui-editor-iframeholder iframe') || {}).contentWindow?.document?.body;
      if (editor) geoPasteHtml('[contenteditable="true"]', ${JSON.stringify(html)});
      return true;
    `);
    throw new Error("搜狐号发布流程待真机联调：已定位编辑器，发布按钮提交逻辑将在联调中补齐（本条不会发布任何内容）");
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}
