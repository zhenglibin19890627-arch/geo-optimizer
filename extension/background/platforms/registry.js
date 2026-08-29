// 平台注册表与登录态检测
// 每个平台声明：展示名、编辑器地址、登录探测（cookie 名单 + 用户信息接口）。
// 发布流程见各平台文件的 publish()，按“打开编辑器页 → 注入内容 → 触发发布 → 回落地页”实现。

const PLATFORMS = {
  zhihu: {
    id: "zhihu",
    name: "知乎",
    support: { markdown: true, html: true, latex: false },
    editorUrl: "https://zhuanlan.zhihu.com/write",
    // 登录判定：z_c0 为知乎登录凭证 cookie（.zhihu.com 域）
    loginCookies: [{ domain: "zhihu.com", name: "z_c0" }],
  },
  sohu: {
    id: "sohu",
    name: "搜狐号",
    support: { markdown: true, html: true, latex: false },
    editorUrl: "https://mp.sohu.com/main/home/index",
    // 搜狐 mp 登录 cookie 名以真机核对为准，先探测 mp.sohu.com 域任意 cookie
    loginCookies: [{ domain: "sohu.com", name: "*" }],
  },
  toutiao: {
    id: "toutiao",
    name: "今日头条",
    support: { markdown: true, html: true, latex: false },
    editorUrl: "https://mp.toutiao.com/profile_v4/graphic/publish",
    // 头条 mp 登录态常见 cookie：sid_tt / sessionid（.toutiao.com 域）
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

// 登录检测：按平台声明的 cookie 名单探测（name="*" 表示该域存在任意 cookie 即算）
export async function checkLogin(platformId) {
  const platform = getPlatform(platformId);
  try {
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
