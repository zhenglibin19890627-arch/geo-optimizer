async function send(action) {
  const res = await chrome.runtime.sendMessage({ channel: "geo-ext", action });
  if (!res || !res.ok) throw new Error(res ? res.error : "无响应");
  return res.data;
}

(async () => {
  const list = document.getElementById("list");
  try {
    const platforms = await send("overview");
    list.textContent = "";
    for (const p of platforms) {
      const row = document.createElement("div");
      row.className = "row";
      const enabled = p.accounts.some((a) => a.enabled);
      const on = p.loggedIn && enabled;
      row.innerHTML = `<span class="dot ${on ? "on" : ""}"></span>
        <span class="name">${p.name}</span>
        <span>${p.loggedIn ? (enabled ? "就绪" : "未启用账号") : "未登录"}</span>`;
      list.appendChild(row);
    }
  } catch (err) {
    list.textContent = "加载失败：" + err.message;
  }
})();
