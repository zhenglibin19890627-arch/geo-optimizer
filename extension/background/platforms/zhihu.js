// 知乎专栏适配器：打开专栏编辑器 → 注入标题与 HTML → 触发发布 → 回读落地页
// 选择器基于 2026 现网版本，真机联调中如有变动以实际 DOM 为准微调。

import { renderMarkdown } from "../render.js";
import { evalInTab, waitForTabComplete, waitForConditionInTab } from "./dom.js";

const EDITOR_URL = "https://zhuanlan.zhihu.com/write";

export async function publish({ post, log }) {
  const { html } = renderMarkdown(post.body_md);
  await log("打开知乎专栏编辑器（前台标签页）");
  // 前台打开 + 失败时保留标签页：自动化发不出去就让人工在原页面完成发布
  const tab = await chrome.tabs.create({ url: EDITOR_URL, active: true });
  let published = false;
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
    await new Promise((r) => setTimeout(r, 1200)); // 等编辑器消化粘贴内容（知乎会自动存草稿）

    await log("触发发布");
    let clicked = false;
    try {
      clicked = await evalInTab(tab.id, `
        const byClass = document.querySelector('.Button-publish, .PublishButton, button[class*="ublish"]');
        if (byClass && !byClass.disabled) { byClass.click(); return "class"; }
        const btn = Array.from(document.querySelectorAll("button"))
          .find((b) => ["发布", "发布文章"].includes((b.textContent || "").trim()) && !b.disabled);
        if (btn) { btn.click(); return "text"; }
        return "";
      `);
    } catch (err) { clicked = false; }

    // 发布成功后页面跳转至文章落地页 /p/{id}
    const landed = await waitForConditionInTab(
      tab.id,
      `location.pathname.startsWith('/p/')`,
      30000,
      800,
    );
    if (landed) {
      const url = await evalInTab(
        tab.id,
        `return (location.origin + location.pathname).replace(/\\/edit$/, "");`,
      );
      published = true;
      return { url };
    }
    // 自动发布未确认：内容已在编辑器且知乎自动保存草稿，降级为人工发布（保留编辑器页面）
    throw new Error(
      "内容已注入编辑器（知乎草稿箱已自动保存），自动发布未确认"
      + (clicked ? "（已点击发布但未检测到落地页跳转，请检查知乎后台）" : "（未找到发布按钮）")
      + "——编辑器标签页已保留，请手动完成发布");
  } finally {
    if (published) chrome.tabs.remove(tab.id).catch(() => {});
  }
}
