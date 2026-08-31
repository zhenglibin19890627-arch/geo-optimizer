/* ============================================================
   稿件库独立页：统计概览 + 搜索/状态/时间筛选 + 平台明细 + 查看全文
   ============================================================ */

initNav("drafts");

var allDrafts = [];      // 全量稿件（接口已按创建时间倒序）
var historyGroups = [];  // 平台历史文章（按标题聚合后的组，最新在前）
var channelCache = {};   // draft_id -> 渠道任务列表

function el(id) { return document.getElementById(id); }

function fmtDate(d) {
  var p = function (n) { return (n < 10 ? "0" : "") + n; };
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

function timeRange() {
  var mode = el("d-time").value;
  if (mode === "all") return null;
  var today = new Date();
  if (mode === "week") return { start: fmtDate(new Date(today.getTime() - 7 * 86400000)), end: fmtDate(today) };
  if (mode === "month") return { start: fmtDate(new Date(today.getTime() - 30 * 86400000)), end: fmtDate(today) };
  var s = el("d-date-start").value, e = el("d-date-end").value;
  if (!s && !e) return null;
  return { start: s, end: e };
}

function renderStats() {
  el("st-all").textContent = allDrafts.length;
  el("st-published").textContent = allDrafts.filter(function (d) { return d.status === "published"; }).length;
  el("st-draft").textContent = allDrafts.filter(function (d) { return d.status === "draft"; }).length;
  el("st-failed").textContent = allDrafts.filter(function (d) { return d.status === "failed"; }).length;
}

function renderList() {
  var kw = el("d-search").value.trim().toLowerCase();
  var status = el("d-status").value;
  var range = timeRange();
  var shown = allDrafts.filter(function (d) {
    if (status && d.status !== status) return false;
    if (kw && (d.title || "").toLowerCase().indexOf(kw) < 0
      && (d.summary || "").toLowerCase().indexOf(kw) < 0) return false;
    if (range) {
      var day = (d.created_at || "").slice(0, 10);
      if (range.start && day && day < range.start) return false;
      if (range.end && day && day > range.end) return false;
    }
    return true;
  });
  // 统一时间线：稿件卡 + 平台历史卡合并，按时间倒序混排
  var kwClean = kw.replace(/\s+/g, "");
  var items = [];
  shown.forEach(function (d) {
    items.push({ date: (d.created_at || "").slice(0, 16), draft: d });
  });
  historyGroups.forEach(function (g) {
    if (status && status !== "published") return; // 历史文章都是已发布
    const day = String(g[0].publish_time || "").slice(0, 10);
    if (kwClean && normTitle(g[0].title).indexOf(kwClean) < 0) return;
    if (range) {
      if (range.start && day && day < range.start) return;
      if (range.end && day && day > range.end) return;
    }
    items.push({ date: String(g[0].publish_time || ""), hist: g });
  });
  items.sort(function (a, b) { return String(b.date || "").localeCompare(String(a.date || "")); });
  el("d-count").textContent = "共 " + items.length
    + " 篇（本系统 " + shown.length + " + 平台历史 " + historyGroups.length + "）";
  var list = el("draft-list");
  if (!items.length) {
    list.innerHTML = '<div class="card" style="text-align:center;padding:30px;color:#9CA3AF">'
      + (allDrafts.length || historyGroups.length ? "没有符合条件的内容。" : "还没有稿件——去「内容分发」生成第一篇，或在下方同步平台历史文章。") + "</div>";
    return;
  }
  list.innerHTML = "";
  items.forEach(function (it) {
    if (!it.draft) {
      const g = it.hist;
      const chips = g.map(function (a) {
        const cn = PLAT_CN[a.platform] || a.platform;
        return '<a class="p-chip" href="' + esc(a.url) + '" target="_blank" rel="noopener" style="text-decoration:none">'
          + '<span class="p-dot ok"></span>' + esc(cn)
          + (a.publish_time ? " · " + esc(a.publish_time) : "") + " · 打开 ↗</a>";
      }).join("");
      const platLine = g.map(function (a) { return PLAT_CN[a.platform] || a.platform; }).join(" / ");
      const hcard = document.createElement("div");
      hcard.className = "draft-card st-published";
      hcard.innerHTML =
        '<div class="draft-head"><div style="flex:1;min-width:0">'
        + '<div class="draft-title-main"><a href="' + esc(g[0].url) + '" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">' + esc(g[0].title) + "</a></div>"
        + '<div class="draft-meta"><span>' + esc((g[0].publish_time || "").slice(0, 16)) + "</span>"
        + '<span class="src-badge">平台历史</span>'
        + "<span>渠道：" + esc(platLine) + "</span></div>"
        + "</div></div>"
        + '<div class="platform-chips">' + chips + "</div>";
      list.appendChild(hcard);
      return;
    }
    var d = it.draft;
    var card = document.createElement("div");
    card.className = "draft-card st-" + d.status;
    var srcHtml = "";
    if (d.source_round_id) {
      srcHtml = '<a href="/monitor.html?round_id=' + encodeURIComponent(d.source_round_id)
        + '" target="_blank" rel="noopener" class="src-badge" style="text-decoration:none">简报溯源 · 第 '
        + esc(d.source_round_id) + " 轮监测</a>";
    } else if (d.brief) {
      srcHtml = '<span class="src-badge">来自创作简报</span>';
    } else {
      srcHtml = '<span class="src-badge">知识库生成</span>';
    }
    var pubHtml = d.published_url
      ? '<a href="' + esc(d.published_url) + '" target="_blank" rel="noopener" style="color:var(--success,#16A34A);text-decoration:none">已发布内容 ↗</a>'
      : "";
    card.innerHTML =
      '<div class="draft-head">'
      + '<div style="flex:1;min-width:0">'
      + '<div class="draft-title-main" data-view="' + d.id + '">' + esc(d.title) + "</div>"
      + '<div class="draft-meta">'
      + '<span>' + esc((d.created_at || "").slice(0, 16)) + "</span>"
      + srcHtml
      + pubHtml
      + "</div></div>"
      + '<div class="draft-actions">'
      + '<button class="btn btn-xs" data-view="' + d.id + '">查看</button>'
      + '<button class="btn btn-xs" data-edit="' + d.id + '">去编辑</button>'
      + '<button class="btn btn-xs" data-del="' + d.id + '">删除</button>'
      + "</div></div>"
      + '<div class="platform-chips" data-chips="' + d.id + '"><span class="small-note" style="color:#bbb">平台明细加载中…</span></div>';
    list.appendChild(card);

    loadChips(d, card.querySelector('[data-chips="' + d.id + '"]'));
  });

  list.querySelectorAll("[data-view]").forEach(function (n) {
    n.addEventListener("click", function () { openView(n.getAttribute("data-view")); });
  });
  list.querySelectorAll("[data-edit]").forEach(function (n) {
    n.addEventListener("click", function () { location.href = "/static/distribution.html"; });
  });
  list.querySelectorAll("[data-del]").forEach(function (n) {
    n.addEventListener("click", function () {
      confirmDialog("删除这篇稿件？该操作不可恢复。", function () {
        geoApi("/api/distribution/drafts/" + n.getAttribute("data-del"), { method: "DELETE", body: {} })
          .then(function () { loadAll(); showToast("已删除", "success"); })
          .catch(function (m) { showToast(m || "删除失败", "error"); });
      }, { danger: true });
    });
  });
}

var PLAT_CN = { zhihu: "知乎", sohu: "搜狐", toutiao: "头条", site: "官网" };
function loadChips(d, box) {
  geoApi("/api/distribution/drafts/" + d.id + "/channels").then(function (ch) {
    channelCache[d.id] = ch;
    if (!ch.length) { box.innerHTML = '<span class="small-note" style="color:#bbb">尚未分发——去「内容分发」勾选平台</span>'; return; }
    box.innerHTML = ch.map(function (t) {
      var cls = t.status === "published" ? "ok" : t.status === "failed" ? "bad"
        : (t.status === "dispatching" || t.status === "pending") ? "run" : "idle";
      var lb = { published: "已发布", failed: "失败", dispatching: "分发中", pending: "待分发" }[t.status] || t.status;
      var html = '<span class="p-chip"><span class="p-dot ' + cls + '"></span>'
        + esc(PLAT_CN[t.platform] || t.platform) + " · " + lb;
      if (t.platform_url) html += ' · <a href="' + esc(t.platform_url) + '" target="_blank" rel="noopener">打开 ↗</a>';
      if (t.status === "failed") {
        html += ' · <a href="javascript:;" data-retry="' + t.id + '" style="color:var(--warn,#F59E0B)">重试</a>';
        html += ' · <a href="javascript:;" data-manual="' + t.id + '" style="color:var(--success,#16A34A)">手动已发</a>';
      }
      return html + "</span>";
    }).join("");
    box.querySelectorAll("[data-retry]").forEach(function (a) {
      a.addEventListener("click", function () {
        geoApi("/api/distribution/channels/" + a.getAttribute("data-retry") + "/retry", { method: "POST", body: {} })
          .then(function () { loadAll(); showToast("已重新排队", "success"); })
          .catch(function (m) { showToast(m || "重试失败", "error"); });
      });
    });
    box.querySelectorAll("[data-manual]").forEach(function (a) {
      a.addEventListener("click", function () {
        var url = prompt("你已在平台上手动发布成功——可粘贴发布后的文章链接（不知道就留空）：", "");
        if (url === null) return;
        geoApi("/api/distribution/channels/" + a.getAttribute("data-manual") + "/manual",
               { method: "POST", body: { platform_url: (url || "").trim() } })
          .then(function () { loadAll(); showToast("已记录手动发布成功", "success"); })
          .catch(function (m) { showToast(m || "记录失败", "error"); });
      });
    });
  }).catch(function () { box.innerHTML = ""; });
}

function openView(id) {
  geoApi("/api/distribution/drafts/" + id).then(function (d) {
    el("view-title").textContent = d.title;
    var ch = channelCache[id] || [];
    var platLine = ch.length
      ? " · 渠道：" + ch.map(function (t) {
          var mark = t.status === "published" ? "✅" : t.status === "failed" ? "❌" : "⏳";
          return mark + (PLAT_CN[t.platform] || t.platform);
        }).join(" ")
      : "";
    el("view-meta").textContent = "创建于 " + (d.created_at || "").slice(0, 16)
      + " · 状态：" + ({ published: "已发布", draft: "待审阅", failed: "发布失败" }[d.status] || d.status)
      + platLine;
    el("view-body").textContent = d.body_md || "";
    el("view-mask").classList.add("show");
  }).catch(function (m) { showToast(m || "读取失败", "error"); });
}

function loadAll() {
  geoApi("/api/distribution/drafts").then(function (items) {
    allDrafts = items || [];
    renderStats();
    renderList();
  }).catch(function () {
    el("draft-list").innerHTML = '<div class="card" style="text-align:center;padding:30px;color:#DC2626">稿件库读取失败，请确认服务已启动。</div>';
  });
  geoApi("/api/distribution/overview").then(function (d) {
    const cs = d.channel_stats || {};
    el("d-bridge").innerHTML = '<span class="tag ' + (d.agent_online ? "tag-green" : "tag-gray") + '">'
      + (d.agent_online ? "分发桥在线" : "分发桥离线") + "</span>";
    el("d-chan-stats").textContent = "多平台任务：待发 " + (cs.pending || 0)
      + " ｜分发中 " + (cs.dispatching || 0) + " ｜已发 " + (cs.published || 0) + " ｜失败 " + (cs.failed || 0);
  }).catch(function () { el("d-bridge").textContent = ""; });
}

/* ---------------- 事件绑定 ---------------- */

el("d-search").addEventListener("input", renderList);
el("d-status").addEventListener("change", renderList);
el("d-time").addEventListener("change", function () {
  var custom = el("d-custom-range");
  if (this.value === "custom") {
    custom.classList.remove("hidden");
    if (!el("d-date-start").value) el("d-date-start").value = fmtDate(new Date(Date.now() - 30 * 86400000));
    if (!el("d-date-end").value) el("d-date-end").value = fmtDate(new Date());
  } else {
    custom.classList.add("hidden");
  }
  renderList();
});
el("d-date-start").addEventListener("change", renderList);
el("d-date-end").addEventListener("change", renderList);
el("d-refresh").addEventListener("click", loadAll);
document.querySelectorAll(".stat-tile").forEach(function (tile) {
  tile.addEventListener("click", function () {
    document.querySelectorAll(".stat-tile").forEach(function (t) { t.classList.remove("active"); });
    tile.classList.add("active");
    el("d-status").value = tile.getAttribute("data-st");
    renderList();
  });
});
el("view-close").addEventListener("click", function () { el("view-mask").classList.remove("show"); });
el("view-mask").addEventListener("click", function (e) {
  if (e.target === el("view-mask")) el("view-mask").classList.remove("show");
});

function loadPlatformArticles() {
  // 已发布稿件卡的链接集合：历史文章里相同的链接不重复展示
  const publishedSet = geoApi("/api/distribution/published-urls").then(function (d) {
    return new Set((d.urls || []).map(function (u) {
      return String(u || "").trim().replace(/\/+$/, "");
    }));
  }).catch(function () { return new Set(); });
  Promise.all([geoApi("/api/distribution/platform-articles"), publishedSet]).then(function (res) {
    const arts = res[0].articles || [];
    const pubSet = res[1];
    const box = document.getElementById("pa-cards");
    const syncBtn = document.getElementById("pa-sync");
    if (res[0].last_sync) document.getElementById("pa-last").textContent = "上次同步：" + res[0].last_sync;
    if (syncBtn) {
      syncBtn.disabled = false;
      syncBtn.addEventListener("click", function () {
        const platform = document.getElementById("pa-platform").value;
        syncBtn.disabled = true; syncBtn.textContent = "同步中…";
        geoApi("/api/distribution/platform-articles/sync", { method: "POST", body: { platform: platform } })
          .then(function (r) { showToast(r.message || "已发起同步", "success"); })
          .catch(function (m) { showToast(m || "发起失败", "error"); });
        setTimeout(function () { location.reload(); }, 12000);
      });
    }
    historyGroups = [];  // 无历史数据
    // 按标题聚合：同一篇文章发在多个平台（如搜狐+头条）合并进同一卡片；
    // 已由稿件卡展示的链接（发布渠道回写过的）剔除，避免重复；
    // 标题与现有稿件相同的（系统发布过的）也不再重复展示
    const norm = function (u) { return String(u || "").trim().replace(/\/+$/, ""); };
    const normTitle = function (t) { return String(t || "").replace(/\s+/g, "").toLowerCase(); };
    const existingTitles = new Set(allDrafts.map(function (x) { return normTitle(x.title); }));
    const byTitle = {};
    const groups = [];
    arts.forEach(function (a) {
      if (pubSet.has(norm(a.url))) return;
      if (existingTitles.has(normTitle(a.title))) return;
      const key = normTitle(a.title);
      if (!byTitle[key]) { byTitle[key] = []; groups.push(byTitle[key]); }
      byTitle[key].push(a);
    });
    // 按发布时间排序：离得近的（最新的）放前面；无时间的排最后
    groups.sort(function (g1, g2) {
      const t1 = String(g1[0].publish_time || "");
      const t2 = String(g2[0].publish_time || "");
      if (!t1 && !t2) return 0;
      if (!t1) return 1;
      if (!t2) return -1;
      return t2.localeCompare(t1);
    });
    historyGroups = groups;
    renderDrafts();  // 合并进统一时间线
  }).catch(function (m) {
    // 失败可见化：恢复按钮并提示（不打断稿件列表）
    showToast("平台历史文章加载失败：" + String(m || "未知错误"), "error");
    const sb2 = document.getElementById("pa-sync");
    if (sb2) sb2.disabled = false;
  });
}

loadAll();
loadPlatformArticles();
