import { requireString } from "./actions.js";

// 深度遍历 JSON：收集带 title + url 字段的对象（平台列表接口结构各异，宽松解析）
function walk(node, out, depth) {
  if (!node || typeof node !== "object" || depth > 6 || out.length >= 60) return;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out, depth + 1)); return; }
  const title = String(node.title || node.article_title || node.display_title || "").trim();
  const url = String(node.url || node.article_url || node.open_url || node.url_string || "").trim();
  if (title && url && (url.startsWith("http") || url.startsWith("/"))) {
    const t = node.created_time || node.create_time || node.publish_time
      || node.updated_at || node.created_at || node.created || node.updated
      || node.ctime || node.publish_at || node.published_at || "";
    const time = typeof t === "number" ? new Date(t < 1e12 ? t * 1000 : t).toISOString().slice(0, 10) : String(t).slice(0, 10);
    out.push({ title, url: url.startsWith("http") ? url : "https://www.toutiao.com" + url, publish_time: time });
  }
  for (const k of Object.keys(node)) walk(node[k], out, depth + 1);
}

async function fetchJson(url) {
  // 15 秒强制超时：避免某个候选接口挂起拖满宿主 90 秒等待
  const r = await fetch(url, { credentials: "include", signal: AbortSignal.timeout(15000) });
  const text = await r.text();
  try { return JSON.parse(text); } catch (e) {
    throw new Error("非 JSON 响应（" + r.status + "）： " + text.slice(0, 150));
  }
}

async function fetchZhihu() {
  const me = await fetchJson("https://www.zhihu.com/api/v4/me");
  const token = String(me.url_token || "");
  if (!token) throw new Error("未取到 url_token（未登录？）");
  const list = await fetchJson("https://www.zhihu.com/api/v4/members/" + token + "/articles?offset=0&limit=20&sort_by=created");
  const out = [];
  walk(list, out, 0);
  return out;
}

async function fetchToutiao() {
  const candidates = [
    "https://mp.toutiao.com/api/article/list/list?page=0&pageSize=20",
    "https://mp.toutiao.com/api/article/list/list/?page=0&pageSize=20",
    "https://mp.toutiao.com/api/pc/article/list?page=0&pageSize=20",
    "https://mp.toutiao.com/api/article/list?page=0&pageSize=20",
    "https://mp.toutiao.com/api/pc/content/list?page=0&pageSize=20&state=published",
  ];
  let lastErr = "";
  for (const u of candidates) {
    try {
      const list = await fetchJson(u);
      const out = [];
      walk(list, out, 0);
      if (out.length) return out;
      lastErr = u + " → 响应可解析但未找到文章字段： " + JSON.stringify(list).slice(0, 120);
    } catch (e) { lastErr = u + " → " + e.message; }
  }
  // 接口全部失效：退而读取内容管理页 DOM（与人工查看一致，最稳）
  try {
    return await fetchToutiaoByPage();
  } catch (e2) {
    throw new Error(lastErr + "；页面抓取也失败： " + e2.message);
  }
}

async function fetchToutiaoByPage() {
  const pageUrl = "https://mp.toutiao.com/profile_v4/graphic/articles";
  let tabId;
  const tabs = await chrome.tabs.query({ url: "https://mp.toutiao.com/*" });
  if (tabs.length) {
    tabId = tabs[0].id;
  } else {
    const tab = await chrome.tabs.create({ url: pageUrl, active: false });
    tabId = tab.id;
  }
  // 轮询抓取：页面慢就多等几轮（最长约 24 秒）
  let items = [];
  for (let i = 0; i < 8; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const seen = {};
          const out = [];
          // 途径一：<a> 链接里的文章（标题在链接文字或标题属性里）
          document.querySelectorAll("a").forEach((a) => {
            const href = a.href || "";
            if (!/toutiao\.com\/(article|pgc-detail|news)\/|pgc_id=/.test(href)) return;
            const title = (a.getAttribute("title") || a.textContent || "")
              .replace(/\s+/g, " ").trim();
            if (!title || title.length < 6 || seen[href]) return;
            seen[href] = 1;
            const box = a.closest("li,article,section,div[class*='item'],div[class*='card']") || a.parentElement;
            const txt = (box && box.textContent) || "";
            const tm = txt.match(/\d{4}-\d{2}-\d{2}/);
            out.push({ title: title.slice(0, 120), url: href.split("?")[0], publish_time: tm ? tm[0] : "" });
          });
          // 途径二：SPA 内嵌 JSON 里的 article_id + title（页面结构再变也能拿到）
          if (out.length < 3) {
            const html = document.documentElement.innerHTML;
            const re = /"article_id":\s*"?(\d{12,25})"?[\s\S]{0,400}?"title":\s*"((?:[^"\\]|\\.){6,140})"/g;
            let m;
            while ((m = re.exec(html)) !== null) {
              const id = m[1];
              const url = "https://www.toutiao.com/article/" + id;
              if (seen[url]) continue;
              let title = m[2];
              try { title = JSON.parse('"' + title + '"'); } catch (e) { /* 保持原样 */ }
              title = String(title).replace(/\s+/g, " ").trim();
              if (!title) continue;
              seen[url] = 1;
              out.push({ title: title.slice(0, 120), url, publish_time: "" });
            }
          }
          return out;
        },
      });
      items = (results && results[0] && results[0].result) || [];
      if (items.length) break;
    } catch (e) {
      if (i === 7) throw new Error("无法注入内容管理页： " + e.message);
    }
  }
  if (!items.length) {
    throw new Error("内容管理页未解析出文章——请先手动打开 " + pageUrl
      + " 确认已登录且能看到文章列表，再点同步重试");
  }
  return items;
}

async function fetchSohu() {
  const candidates = [
    "https://mp.sohu.com/mpfe/v4/contentManagement/news/list?pageNo=1&pageSize=20",
    "https://mp.sohu.com/mpfe/api/content/news/list?pageNo=1&pageSize=20",
  ];
  let lastErr = "";
  for (const u of candidates) {
    try {
      const list = await fetchJson(u);
      const out = [];
      walk(list, out, 0);
      if (out.length) return out;
      lastErr = "响应可解析但未找到文章字段： " + JSON.stringify(list).slice(0, 150);
    } catch (e) { lastErr = e.message; }
  }
  throw new Error(lastErr || "全部候选接口失败");
}

const FETCHERS = { zhihu: fetchZhihu, toutiao: fetchToutiao, sohu: fetchSohu };

export async function syncArticles(payload) {
  const platform = requireString(payload.platform, "platform");
  if (!FETCHERS[platform]) throw new Error("不支持的平台: " + platform);
  // 不再用 cookie 猜测登录态（分区 cookie 会误判），直接请求，失败时错误会带响应片段
  const articles = await FETCHERS[platform]();
  return { platform, articles };
}
