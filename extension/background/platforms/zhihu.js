// 知乎专栏适配器：打开专栏编辑器 → 注入标题与 HTML → 触发发布 → 回读落地页
// 选择器基于 2026 现网版本，真机联调中如有变动以实际 DOM 为准微调。

import { renderMarkdown } from "../render.js";
import { evalInTab, waitForTabComplete, waitForConditionInTab } from "./dom.js";

const EDITOR_URL = "https://zhuanlan.zhihu.com/write";

export async function publish({ post, log }) {
  const { html } = renderMarkdown(post.body_md);
  await log("打开知乎专栏编辑器");
  const tab = await chrome.tabs.create({ url: EDITOR_URL, active: false });
  try {
    await waitForTabComplete(tab.id);
    const editorReady = await waitForConditionInTab(
      tab.id,
      `document.querySelector('.public-DraftEditor-content, [data-contents]')`,
    );
    if (!editorReady) throw new Error("编辑器未加载（未登录或页面改版）");

    await log("注入标题与正文");
    await evalInTab(tab.id, `
      const titleEl = document.querySelector('textarea[placeholder*="标题"], textarea');
      if (!titleEl) throw new Error("标题输入框未找到");
      geoSetNativeValue(titleEl, ${JSON.stringify(post.title.slice(0, 100))});
      geoPasteHtml('.public-DraftEditor-content, [data-contents] .DraftEditor-editorContainer', ${JSON.stringify(html)});
      return true;
    `);
    await new Promise((r) => setTimeout(r, 1200)); // 等编辑器消化粘贴内容

    await log("触发发布");
    await evalInTab(tab.id, `return geoClickButton("发布");`);

    // 发布成功后页面跳转至文章落地页 /p/{id}
    const landed = await waitForConditionInTab(
      tab.id,
      `location.pathname.startsWith('/p/')`,
      30000,
      800,
    );
    if (!landed) throw new Error("发布后未跳转到落地页（可能有弹窗或校验提示）");

    const url = await evalInTab(tab.id, `return location.origin + location.pathname;`);
    return { url };
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}
