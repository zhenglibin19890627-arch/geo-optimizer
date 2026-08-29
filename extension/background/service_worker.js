// service worker 入口：接通宿主长连接 + 扩展内部页面（options/popup）消息

import "./rpc.js";
import * as store from "./store.js";
import { listPlatforms, getPlatform } from "./platforms/registry.js";
import { detectLogin } from "./platforms/login_detect.js";
import { fetchNickname } from "./platforms/profile.js";
import { runJob } from "./publish.js";

// options/popup 页通过 chrome.runtime.sendMessage 调用内部动作
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.channel !== "geo-ext") return false;
  handleUiAction(msg)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: err.message || String(err) }));
  return true; // 异步响应
});

async function handleUiAction(msg) {
  switch (msg.action) {
    case "overview": {
      const accounts = await store.listAccounts();
      const config = await store.getConfig();
      const out = [];
      for (const platform of listPlatforms()) {
        const platformAccounts = accounts.filter((a) => a.platform === platform.id);
        const loggedIn = (await detectLogin(platform.id)).loggedIn;
        out.push({
          ...platform,
          loggedIn,
          accounts: platformAccounts.map((a) => ({
            ...a,
            isDefault: config.defaultPublish[a.platform] === a.id,
          })),
        });
      }
      return out;
    }
    case "detectLogin": {
      const platform = getPlatform(msg.platform);
      const det = await detectLogin(msg.platform);
      if (det.loggedIn && !det.nickname) {
        det.nickname = await fetchNickname(msg.platform);
      }
      return { ...det, loginUrl: platform.loginUrl };
    }

    case "probeCookies": {
      const platform = getPlatform(msg.platform);
      const rootDomain = platform.loginCookies[0].domain;
      // 多角度查询：按根域 + 按平台各具体网址（两种过滤语义不同，结果合并去重）
      const urls = [rootDomain, "www." + rootDomain, platform.loginUrl, platform.editorUrl]
        .map((u) => (u.startsWith("https://") ? u : "https://" + u));
      const merged = new Map();
      const batches = await Promise.all([
        chrome.cookies.getAll({ domain: rootDomain }),
        ...urls.map((u) => chrome.cookies.getAll({ url: u }).catch(() => [])),
      ]);
      for (const batch of batches) {
        for (const c of batch) {
          const key = c.name + "@" + c.domain;
          if (!merged.has(key)) {
            merged.set(key, {
              name: c.name,
              domain: c.domain,
              hostOnly: c.hostOnly,
              persistent: !!c.expirationDate,
              partitioned: !!(c.partitionKey && c.partitionKey.topLevelSite),
            });
          }
        }
      }
      return Array.from(merged.values()).slice(0, 30);
    }
    case "addAccount": {
      getPlatform(msg.platform);
      return store.addAccount(msg.platform, msg.nickname);
    }
    case "removeAccount":
      return store.removeAccount(msg.accountId);
    case "updateAccount":
      return store.updateAccount(msg.accountId, msg.patch || {});
    case "setDefaultPublish":
      return store.setDefaultPublish(msg.platform, msg.accountId);
    case "listPosts": {
      const posts = await store.get("posts", {});
      return Object.values(posts).sort((a, b) => b.createdAt - a.createdAt);
    }
    case "listJobs": {
      const jobs = await store.get("jobs", {});
      return Object.values(jobs).sort((a, b) => b.createdAt - a.createdAt).slice(0, 50);
    }
    case "runJob":
      return runJob(msg.jobId);
    default:
      throw new Error("未知内部动作: " + msg.action);
  }
}
