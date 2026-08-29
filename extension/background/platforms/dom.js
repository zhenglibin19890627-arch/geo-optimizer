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
export async function evalInTab(tabId, statements) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: (code) => {
      const geoRun = new Function(code);
      return geoRun();
    },
    args: [PAGE_HELPERS_SOURCE + "\n" + statements],
  });
  return results && results[0] ? results[0].result : undefined;
}

export async function waitForTabComplete(tabId, timeoutMs = 30000) {
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { cleanup(); reject(new Error("页面加载超时")); }, timeoutMs);
    // onUpdated 回调签名：(tabId: number, changeInfo: {status...}, tab)
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo && changeInfo.status === "complete") {
        cleanup(); resolve();
      }
    }
    function cleanup() {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
    }
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab.status === "complete") { cleanup(); resolve(); }
    }).catch(() => { cleanup(); reject(new Error("标签页已关闭")); });
  });
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
