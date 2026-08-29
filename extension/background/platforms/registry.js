// 平台注册表与登录态检测
// 每个平台声明：展示名、编辑器地址、登录探测（cookie 名单 + 用户信息接口）。
// 发布流程见各平台文件的 publish()，按“打开编辑器页 → 注入内容 → 触发发布 → 回落地页”实现。

const PLATFORMS = {
  zhihu: {
    id: "zhihu",
    name: "知乎",
    support: { markdown: true, html: true, latex: false },
    editorUrl: "https://zhuanlan.zhihu.com/write",
    loginUrl: "https://www.zhihu.com/signin?next=%2F",
    // 登录判定：z_c0 为知乎登录凭证 cookie（.zhihu.com 域）
    loginCookies: [{ domain: "zhihu.com", name: "z_c0" }],
  },
  sohu: {
    id: "sohu",
    name: "搜狐号",
    support: { markdown: true, html: true, latex: false },
    editorUrl: "https://mp.sohu.com/mpfe/v4/contentManagement/news/addarticle",
    loginUrl: "https://mp.sohu.com/mpfe/v4/login",
    // 登录检测不走 cookie（域过滤读不到其会话），由 login_detect.js 调后台 API 验证
    loginCookies: [{ domain: "sohu.com", name: "*" }],
  },
  toutiao: {
    id: "toutiao",
    name: "今日头条",
    support: { markdown: true, html: true, latex: false },
    editorUrl: "https://mp.toutiao.com/profile_v4/graphic/publish",
    loginUrl: "https://mp.toutiao.com",
    // 登录判定：按 URL 探测会话 cookie 名单（域过滤可能读不到 host-only 会话）
    probeUrls: ["https://mp.toutiao.com/", "https://sso.toutiao.com/", "https://www.toutiao.com/"],
    loginCookieNames: ["sso_uid_tt", "sso_uid_tt_ss", "toutiao_sso_user", "passport_csrf_token",
      "sid_guard", "uid_tt", "sid_tt", "sessionid", "sid_ucp_v1"],
    loginCookies: [{ domain: "toutiao.com", name: "sid_tt" }, { domain: "toutiao.com", name: "sessionid" }],
  },
};

export function listPlatforms() {
  return Object.values(PLATFORMS).map((p) => ({
    id: p.id,
    name: p.name,
    supportsMarkdown: p.support.markdown,
    supportsHtml: p.support.html,
    supportsLatex: p.support.latex,
  }));
}

export function getPlatform(id) {
  const platform = PLATFORMS[id];
  if (!platform) {
    const err = new Error("不支持的平台: " + id);
    err.code = "unsupported_platform";
    throw err;
  }
  return platform;
}

// 登录检测：优先按 URL × 会话 cookie 名单探测（weiqi 验证过的方式），
// 无名单时回退到旧域过滤逻辑
export async function checkLogin(platformId) {
  const platform = getPlatform(platformId);
  try {
    if (platform.probeUrls && platform.loginCookieNames) {
      for (const url of platform.probeUrls) {
        for (const name of platform.loginCookieNames) {
          const found = await chrome.cookies.get({ url, name });
          if (found) return true;
        }
      }
      return false;
    }
    for (const probe of platform.loginCookies) {
      if (probe.name === "*") {
        const all = await chrome.cookies.getAll({ domain: probe.domain });
        if (all.length > 0) return true;
      } else {
        const found = await chrome.cookies.get({ url: "https://" + probe.domain, name: probe.name });
        if (found) return true;
      }
    }
    return false;
  } catch (err) {
    console.warn("登录检测失败", platformId, err);
    return false;
  }
}
