// 平台登录检测的统一入口
// 搜狐：cookie 域名过滤读不到其会话，改走后台 register-info API 验证（成功即在线，顺带取昵称）
// 其余平台：按注册表声明的 cookie 探测

import { checkLogin } from "./registry.js";

async function sohuRegisterInfo() {
  const res = await fetch("https://mp.sohu.com/mpbp/bp/account/register-info", {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) return { ok: false };
  const data = await res.json().catch(() => null);
  if (!data) return { ok: false };
  const code = Number(data.code ?? data.status ?? data.statusCode);
  const payload = data.data || data.result || {};
  const success = code === 2000000 || code === 0 || code === 200 ||
    data.success === true ||
    (payload && typeof payload === "object" && Object.keys(payload).length > 0);
  if (!success) return { ok: false };
  const nickname = payload.nickName || payload.name || payload.nickname || null;
  return { ok: true, nickname: nickname ? String(nickname) : null };
}

// 返回 { loggedIn, nickname }
export async function detectLogin(platformId) {
  if (platformId === "sohu") {
    try {
      const r = await sohuRegisterInfo();
      return { loggedIn: r.ok, nickname: r.ok ? r.nickname : null };
    } catch (err) {
      console.warn("sohu 登录检测失败", err);
      return { loggedIn: false, nickname: null };
    }
  }
  const loggedIn = await checkLogin(platformId);
  return { loggedIn, nickname: null };
}
