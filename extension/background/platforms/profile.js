// 各平台账号昵称抓取（尽力而为）：检测到登录后尽量显示真实账号名
// 全部失败时返回 null，由用户在添加账号时手填备注。

export async function fetchNickname(platformId) {
  try {
    switch (platformId) {
      case "zhihu":
        // 知乎个人接口：登录态 cookie 直接可读
        {
          const res = await fetch("https://www.zhihu.com/api/v4/me", {
            credentials: "include",
          });
          if (!res.ok) return null;
          const data = await res.json();
          return data && data.name ? String(data.name) : null;
        }
      case "sohu":
        // 搜狐号后台为 SPA，从主页 HTML 里尽力提取昵称字段
        {
          const res = await fetch("https://mp.sohu.com/main/home/index", {
            credentials: "include",
          });
          if (!res.ok) return null;
          const html = await res.text();
          const m = html.match(/"nick[n|N]ame"\s*:\s*"([^"]{1,60})"/)
            || html.match(/"nickname"\s*:\s*"([^"]{1,60})"/);
          return m ? m[1] : null;
        }
      case "toutiao":
        // 头条创作平台 SPA：尝试从页面初始状态里提取 screen_name
        {
          const res = await fetch("https://mp.toutiao.com/profile_v4/manage/all", {
            credentials: "include",
          });
          if (!res.ok) return null;
          const html = await res.text();
          const m = html.match(/"screen_name"\s*:\s*"([^"]{1,60})"/)
            || html.match(/"nickname"\s*:\s*"([^"]{1,60})"/);
          return m ? m[1] : null;
        }
      default:
        return null;
    }
  } catch (err) {
    console.warn("昵称抓取失败", platformId, err);
    return null;
  }
}
