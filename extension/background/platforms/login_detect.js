// 平台登录检测的统一入口
// 搜狐：cookie 域名过滤读不到其会话，改走后台 register-info API 验证（成功即在线，顺带取昵称）
// 其余平台：按注册表声明的 cookie 探测

import { checkLogin } from "./registry.js";

async function sohuRegisterInfo() {
  const res = await fetch("https://mp.sohu.com/mpbp/bp/account/register-info", {
    credentials: "include",
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(8000), // 8 秒超时，防挂起卡死渲染
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
  // 昵称提取：data 与 result 两层都看（含 account 嵌套），与实际响应结构尽力匹配
  const result = data.result && typeof data.result === "object" ? data.result : {};
  const candidates = [
    payload.nickName, payload.name, payload.nickname,
    payload.account && payload.account.nickName,
    result.nickName, result.name, result.nickname,
  ];
  const nickname = candidates.map((x) => (x == null ? "" : String(x).trim())).find(Boolean) || null;
  if (nickname) return { ok: true, nickname };
  // 取不到昵称：返回响应片段供页面显示，便于联调定位字段
  let rawSample = "";
  try {
    rawSample = JSON.stringify(payload).slice(0, 260);
  } catch { /* 忽略序列化失败 */ }
  return { ok: true, nickname: null, rawSample };
}

// 返回 { loggedIn, nickname, rawSample? }
export async function detectLogin(platformId) {
  if (platformId === "sohu") {
    try {
      const r = await sohuRegisterInfo();
      return {
        loggedIn: r.ok,
        nickname: r.ok ? r.nickname : null,
        ...(r.rawSample ? { rawSample: r.rawSample } : {}),
      };
    } catch (err) {
      console.warn("sohu 登录检测失败", err);
      return { loggedIn: false, nickname: null };
    }
  }
  const loggedIn = await checkLogin(platformId);
  return { loggedIn, nickname: null };
}
