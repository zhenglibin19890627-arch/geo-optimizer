// 页面驱动工具：在平台编辑器页注入的辅助函数（self-contained，可被 chrome.scripting 序列化执行）
// 注意：这些函数会在目标页面上下文运行，不能引用外部变量。

export const PAGE_HELPERS_SOURCE = `
function geoSetNativeValue(el, value) {
  const setter = Object.getOwnPropertyDescriptor(
    el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
    "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function geoPasteHtml(selector, html) {
  const editor = document.querySelector(selector);
  if (!editor) throw new Error("编辑器未找到: " + selector);
  editor.focus();
  const dt = new DataTransfer();
  dt.setData("text/html", html);
  dt.setData("text/plain", html.replace(/<[^>]+>/g, " "));
  editor.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
}

function geoClickButton(text) {
  const buttons = Array.from(document.querySelectorAll("button"));
  const target = buttons.find((b) => (b.textContent || "").trim().includes(text) && !b.disabled);
  if (!target) throw new Error("未找到按钮: " + text);
  target.click();
  return true;
}
`;

// 在指定 tab 的页面上下文顺序执行语句（helpers 先注入，语句再执行）
// 语句以 async IIFE 包裹：页面脚本内可使用 await；异常原样抛回扩展侧
export async function evalInTab(tabId, statements) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: async (code) => {
      try {
        const run = new Function("return (async () => {\n" + code + "\n})()");
        return { __geo: true, value: await run() };
      } catch (err) {
        return { __geo: true, error: String((err && err.message) || err) };
      }
    },
    args: [PAGE_HELPERS_SOURCE + "\n" + statements],
  });
  const r = results && results[0] ? results[0].result : undefined;
  if (r && r.__geo) {
    if (r.error) throw new Error("页面脚本执行失败: " + r.error);
    return r.value;
  }
  return r;
}

export async function waitForTabComplete(tabId, timeoutMs = 30000) {
  // 纯轮询实现：不依赖 tabs.onUpdated 事件（免疫回调签名/竞态问题）
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    let tab;
    try {
      tab = await chrome.tabs.get(tabId);
    } catch (err) {
      throw new Error("标签页已关闭");
    }
    if (tab.status === "complete") return;
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("页面加载超时(geo-publish/0.1.2)");
}

export async function waitForConditionInTab(tabId, conditionJs, timeoutMs = 20000, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (await evalInTab(tabId, `return !!(${conditionJs});`)) return true;
    } catch (err) { /* 页面尚未就绪，继续等待 */ }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

// 真实点击：经 chrome.debugger CDP 注入浏览器级可信鼠标事件（isTrusted=true）。
// 用于校验事件可信度的平台按钮（如发布按钮）。使用后立即 detach；
// 调试期间浏览器顶部会短暂显示提示横幅，属正常现象。
export async function realClick(tabId, x, y) {
  await chrome.debugger.attach({ tabId }, "1.3");
  try {
    for (const type of ["mousePressed", "mouseReleased"]) {
      await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
        type, x: Math.round(x), y: Math.round(y), button: "left", clickCount: 1,
      });
    }
  } finally {
    await chrome.debugger.detach({ tabId }).catch(() => {});
  }
}

// 取页面元素中心坐标（配合 realClick 使用）：statements 需把目标元素赋给 geoTarget
export async function elementCenter(tabId, statements) {
  return evalInTab(tabId, `
    ${statements}
    if (typeof geoTarget === "undefined" || !geoTarget) return null;
    const geoRect = geoTarget.getBoundingClientRect();
    return { x: geoRect.left + geoRect.width / 2, y: geoRect.top + geoRect.height / 2 };
  `);
}
