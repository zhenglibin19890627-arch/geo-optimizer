// 账号管理页：检测各平台登录态、添加/启用/删除账号、设置默认发布账号

function send(action, extra) {
  return chrome.runtime.sendMessage({ channel: "geo-ext", action, ...extra });
}

async function render() {
  const platforms = await send("overview");
  const root = document.getElementById("platforms");
  root.textContent = "";
  const tpl = document.getElementById("account-row");

  for (const p of platforms) {
    const card = document.createElement("section");
    card.className = "card";
    const head = document.createElement("div");
    head.className = "card-head";
    head.innerHTML = `<h2>${p.name}</h2>
      <span class="badge ${p.loggedIn ? "ok" : "no"}">${p.loggedIn ? "已登录" : "未登录"}</span>
      <button class="primary check">检测登录</button>`;
    card.appendChild(head);

    const list = document.createElement("ul");
    list.className = "accounts";
    if (!p.accounts.length) {
      const tip = document.createElement("li");
      tip.className = "empty";
      tip.textContent = p.loggedIn
        ? "检测到登录态，点「添加账号」纳入发布管理。"
        : "尚未登录：请先在该平台官网登录，再点「检测登录」。";
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
    card.appendChild(list);

    head.querySelector(".check").addEventListener("click", async (e) => {
      e.target.disabled = true;
      const after = await send("overview");
      const me = after.find((x) => x.id === p.id);
      e.target.textContent = me.loggedIn ? "已登录 ✓" : "未登录";
      render();
    });

    if (p.loggedIn) {
      const add = document.createElement("button");
      add.textContent = p.accounts.length ? "再添加一个账号" : "添加账号";
      add.addEventListener("click", async () => {
        const nickname = prompt("给这个账号起个备注名（可留空）：", p.name + " 账号");
        await send("addAccount", { platform: p.id, nickname: nickname || undefined });
        render();
      });
      head.appendChild(add);
    }

    root.appendChild(card);
  }
}

render();
