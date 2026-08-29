// 账号管理页：检测各平台登录态（显示真实昵称）、未登录可一键去登录、添加/启用/删除账号

function send(action, extra) {
  return chrome.runtime.sendMessage({ channel: "geo-ext", action, ...extra }).then((res) => {
    if (!res || !res.ok) throw new Error(res ? res.error : "扩展后台无响应（service worker 未运行？）");
    return res.data;
  });
}

async function render() {
  let platforms;
  try {
    [platforms] = await Promise.all([send("overview")]);
  } catch (err) {
    const root = document.getElementById("platforms");
    root.innerHTML = '<div class="card">加载失败：' + (err.message || err) + "</div>";
    return;
  }
  const root = document.getElementById("platforms");
  root.textContent = "";
  const tpl = document.getElementById("account-row");

  for (const p of platforms) {
    const card = document.createElement("section");
    card.className = "card";
    const head = document.createElement("div");
    head.className = "card-head";
    card.appendChild(head);

    const list = document.createElement("ul");
    list.className = "accounts";
    card.appendChild(list);

    const hint = document.createElement("div");
    hint.className = "hint";
    card.appendChild(hint);

    async function refresh() {
      head.textContent = "";
      hint.textContent = "";
      let det = { loggedIn: p.loggedIn, nickname: null, loginUrl: "" };
      try {
        det = await send("detectLogin", { platform: p.id });
      } catch (err) { /* 保底用 overview 的登录态 */ }

      head.innerHTML = `<h2>${p.name}</h2>
        <span class="badge ${det.loggedIn ? "ok" : "no"}">${
          det.loggedIn ? "已登录" + (det.nickname ? " · " + det.nickname : "") : "未登录"
        }</span>`;

      const check = document.createElement("button");
      check.className = "primary";
      check.textContent = "检测登录";
      check.addEventListener("click", async () => {
        check.disabled = true;
        check.textContent = "检测中…";
        await refresh();
      });
      head.appendChild(check);

      if (det.loggedIn) {
        const add = document.createElement("button");
        add.textContent = p.accounts.length ? "再添加一个账号" : "添加账号";
        add.addEventListener("click", async () => {
          const nickname = prompt("账号备注名（可留空）：", det.nickname || p.name + " 账号");
          await send("addAccount", { platform: p.id, nickname: nickname || undefined });
          render();
        });
        head.appendChild(add);
      } else {
        const go = document.createElement("button");
        go.textContent = "去登录";
        go.addEventListener("click", () => {
          chrome.tabs.create({ url: det.loginUrl });
          hint.textContent = "已打开登录页：登录完成后回到本页点「检测登录」。";
        });
        head.appendChild(go);
        // 调试探针：列出该平台根域实际可见的 cookie，便于定位登录态识别问题
        try {
          const cookies = await send("probeCookies", { platform: p.id });
          hint.textContent = cookies.length
            ? "该域可见 cookie（" + cookies.length + " 个）："
              + cookies.map((c) => c.name).slice(0, 10).join(", ")
              + (cookies.length > 10 ? " …" : "")
            : "该域下没有看到任何 cookie——你可能还没有访问过该平台，或登录的是其他站点。";
        } catch (err) {
          hint.textContent = "调试探针失败：" + (err.message || err) + "（扩展代码可能是旧版，请重载扩展并刷新本页）";
        }
      }

      list.textContent = "";
      if (!p.accounts.length) {
        const tip = document.createElement("li");
        tip.className = "empty";
        tip.textContent = det.loggedIn ? "检测到登录态，点「添加账号」纳入发布管理。" : "暂无账号。";
        list.appendChild(tip);
      }
      for (const account of p.accounts) {
        const node = tpl.content.cloneNode(true);
        node.querySelector(".nick").textContent = account.nickname;
        const enabled = node.querySelector(".enabled");
        enabled.checked = account.enabled;
        enabled.addEventListener("change", () =>
          send("updateAccount", { accountId: account.id, patch: { enabled: enabled.checked } }));
        const def = node.querySelector(".default");
        def.name = "default-" + p.id;
        def.checked = account.isDefault;
        def.addEventListener("change", () =>
          send("setDefaultPublish", { platform: p.id, accountId: account.id }));
        node.querySelector(".del").addEventListener("click", async () => {
          await send("removeAccount", { accountId: account.id });
          render();
        });
        list.appendChild(node);
      }
    }

    await refresh();
    root.appendChild(card);
  }
}

render();
