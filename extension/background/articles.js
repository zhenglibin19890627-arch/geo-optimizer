import { requireString } from "./actions.js";

// 深度遍历 JSON：收集带 title + url 字段的对象（平台列表接口结构各异，宽松解析）
function walk(node, out, depth) {
  if (!node || typeof node !== "object" || depth > 6 || out.length >= 60) return;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out, depth + 1)); return; }
  const title = String(node.title || node.article_title || node.display_title || "").trim();
  const url = String(node.url || node.article_url || node.open_url || node.url_string || "").trim();
  if (title && url && (url.startsWith("http") || url.startsWith("/"))) {
    const t = node.created_time || node.create_time || node.publish_time || node.updated_at || node.created_at || "";
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
  const list = await fetchJson("https://mp.toutiao.com/api/article/list/list?page=0&pageSize=20");
  const out = [];
  walk(list, out, 0);
  return out;
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
