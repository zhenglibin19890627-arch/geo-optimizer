/* ============================================================
   GEO 优化系统 - 内容分发页（监测缺口 → 简报 → 生成 → 审阅 → 官网直发）
   闭环：发布成功后，报告页信源排行会把这些域名标为「已布点」，
   下一轮简报也会把已布点域名从缺口中剔除。
   ============================================================ */

initNav("distribution");

let currentBrief = null;   // 当前简报（生成文章时随请求回传，保证所见即所得）
let selectedTopicIdx = -1; // 简报中选中的主题（生成时作为首选）
let overviewCache = null;  // 首屏数据（平台清单/扩展在线状态）

/* ---------------- 首屏：配置 + 稿件统计 ---------------- */

function loadOverview() {
  geoApi("/api/distribution/overview").then(function (d) {
    overviewCache = d;
    renderDraftStats(d.stats);
    renderChannelLine(d);
  }).catch(function () {});
  loadConfig();
}

function renderChannelLine(d) {
  const el = document.getElementById("channel-line");
  if (!el) return;
  const cs = d.channel_stats || {};
  const online = d.agent_online;
  el.innerHTML =
    '<span class="tag ' + (online ? "tag-green" : "tag-gray") + '">'
    + (online ? "分发桥在线" : "分发桥离线") + "</span> "
    + "多平台任务：待发 " + (cs.pending || 0) + " ｜分发中 " + (cs.dispatching || 0)
    + " ｜已发 " + (cs.published || 0) + " ｜失败 " + (cs.failed || 0);
}

function loadConfig() {
  geoApi("/api/distribution/config").then(function (d) {
    document.getElementById("cfg-base").value = d.base_url || "";
    document.getElementById("cfg-path").value = d.publish_path || "";
    document.getElementById("cfg-category").value = d.category || "";
    document.getElementById("cfg-author").value = d.author || "";
    document.getElementById("cfg-token-masked").textContent =
      d.configured ? ("当前 Token：" + d.token_masked) : "尚未配置 Token，发布前请先填写";
  }).catch(function () {});
}

function renderDraftStats(stats) {
  const el = document.getElementById("draft-stats");
  if (!el) return;
  el.textContent = "共 " + stats.total + " 篇｜待发 " + stats.draft
    + " ｜已发 " + stats.published + " ｜失败 " + stats.failed;
}

/* ---------------- 简报 ---------------- */

function generateBrief() {
  const btn = document.getElementById("brief-btn");
  btn.disabled = true;
  const note = document.getElementById("brief-note");
  note.textContent = "正在分析监测缺口并规划选题…（约 10~30 秒）";
  document.getElementById("brief-area").innerHTML = "";
  geoApi("/api/distribution/brief", { method: "POST", body: {} }).then(function (brief) {
    currentBrief = brief;
    btn.disabled = false;
    note.textContent = brief.mode === "llm"
      ? "简报已生成（分析模型）。点击主题可选中，生成文章时优先采用。"
      : "已生成规则简报（无分析钥匙）。到「设置」页填写分析钥匙可获得智能选题。";
    renderBrief(brief);
  }).catch(function (m) {
    btn.disabled = false;
    note.textContent = m || "简报生成失败，请稍后再试";
  });
}

function renderBrief(brief) {
  const area = document.getElementById("brief-area");
  if (brief.summary) {
    area.innerHTML = '<div class="score-sub" style="margin-bottom:8px">' + esc(brief.summary) + "</div>";
  } else {
    area.innerHTML = "";
  }
  const topics = brief.topics || [];
  if (!topics.length) {
    area.innerHTML += '<div class="empty">没有规划出主题，请先完成几轮监测。</div>';
    return;
  }
  topics.forEach(function (t, i) {
    const div = document.createElement("div");
    div.className = "brief-topic";
    div.style.cssText = "border:1px solid var(--border,#e5e7eb);border-radius:8px;padding:10px 12px;margin-bottom:8px;cursor:pointer";
    div.innerHTML =
      '<div style="font-weight:600">' + esc(t.title) + "</div>" +
      (t.angle ? '<div class="score-sub">' + esc(t.angle) + "</div>" : "") +
      '<div class="small-note">' +
      (t.distillation_words && t.distillation_words.length ? "关键词：" + esc(t.distillation_words.join("、")) + "　" : "") +
      (t.target_domains && t.target_domains.length ? "目标信源：" + esc(t.target_domains.join("、")) : "") +
      "</div>";
    div.addEventListener("click", function () {
      selectedTopicIdx = i;
      Array.prototype.forEach.call(area.children, function (c) {
        c.style.background = "";
      });
      div.style.background = "rgba(63,81,181,.08)";
      document.getElementById("gen-card").classList.remove("hidden");
    });
    area.appendChild(div);
  });
  document.getElementById("gen-card").classList.remove("hidden");
}

/* ---------------- 生成文章 ---------------- */

function generateArticle() {
  if (!currentBrief) return;
  const btn = document.getElementById("gen-btn");
  btn.disabled = true;
  document.getElementById("gen-hint").classList.remove("hidden");
  const payload = {
    user_instruction: document.getElementById("gen-instruction").value,
    brief: currentBrief,
  };
  if (selectedTopicIdx >= 0 && (currentBrief.topics || [])[selectedTopicIdx]) {
    payload.brief = Object.assign({}, currentBrief, {
      topics: [currentBrief.topics[selectedTopicIdx]],
    });
  }
  geoApi("/api/distribution/generate", { method: "POST", body: payload }).then(function (draft) {
    btn.disabled = false;
    document.getElementById("gen-hint").classList.add("hidden");
    renderEditor(draft, true);
    loadDrafts();
  }).catch(function (m) {
    btn.disabled = false;
    document.getElementById("gen-hint").classList.add("hidden");
    showToast(m || "生成失败，请稍后再试", "error");
  });
}

/* ---------------- 编辑器（生成/审阅共用） ---------------- */

function renderEditor(draft, showPublish) {
  const area = document.getElementById("gen-editor-area");
  area.innerHTML =
    '<div class="field"><label class="field-label">标题</label>' +
    '<input class="input" id="ed-title" value="' + esc(draft.title) + '"></div>' +
    '<div class="field"><label class="field-label">正文（Markdown，可编辑）</label>' +
    '<textarea class="textarea" id="ed-body" style="min-height:280px">' + esc(draft.body_md) + "</textarea></div>" +
    '<div class="field"><label class="field-label">摘要（150 字内，发布用）</label>' +
    '<textarea class="textarea" id="ed-summary" style="min-height:52px">' + esc(draft.summary) + "</textarea></div>" +
    '<div class="field"><label class="field-label">标签（逗号分隔）</label>' +
    '<input class="input" id="ed-tags" value="' + esc(draft.tags) + '"></div>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
    '<button class="btn btn-primary" id="ed-save">保存修改</button>' +
    (showPublish ? '<button class="btn btn-primary" id="ed-publish">发布到官网</button>' : "") +
    '<span class="saved-hint hidden" id="ed-hint"></span></div>' +
    '<div class="small-note mt-8" id="ed-msg"></div>' +
    '<div class="mt-8" id="mp-box"></div>';
  renderChannelBox(draft);

  document.getElementById("ed-save").addEventListener("click", function () {
    geoApi("/api/distribution/drafts/" + draft.id, {
      method: "PUT",
      body: {
        title: document.getElementById("ed-title").value,
        body_md: document.getElementById("ed-body").value,
        summary: document.getElementById("ed-summary").value,
        tags: document.getElementById("ed-tags").value,
      },
    }).then(function () {
      showToast("稿件已保存", "success");
      loadDrafts();
    }).catch(function (m) { showToast(m || "保存失败", "error"); });
  });

  const pub = document.getElementById("ed-publish");
  if (pub) {
    pub.addEventListener("click", function () {
      pub.disabled = true;
      /* 发布前先保存最新编辑内容，保证发出去的就是看到的 */
      geoApi("/api/distribution/drafts/" + draft.id, {
        method: "PUT",
        body: {
          title: document.getElementById("ed-title").value,
          body_md: document.getElementById("ed-body").value,
          summary: document.getElementById("ed-summary").value,
          tags: document.getElementById("ed-tags").value,
        },
      }).then(function () {
        return geoApi("/api/distribution/drafts/" + draft.id + "/publish", { method: "POST", body: {} });
      }).then(function (res) {
        document.getElementById("ed-msg").textContent =
          "已发布到官网" + (res.url ? "：" + res.url : "") + "。报告页信源排行将把它标注为「已布点」。";
        showToast("已发布到官网", "success");
        loadDrafts();
        loadConfig();
      }).catch(function (m) {
        pub.disabled = false;
        document.getElementById("ed-msg").textContent = "发布失败：" + (m || "请稍后再试") + "（稿件状态已记为失败，可修改后重试）";
        showToast(m || "发布失败", "error");
      });
    });
  }
}

/* ---------------- 多平台分发（二期） ---------------- */

function renderChannelBox(draft) {
  const box = document.getElementById("mp-box");
  if (!box) return;
  const platforms = (overviewCache && overviewCache.platforms) || [];
  if (!platforms.length) {
    geoApi("/api/distribution/overview").then(function (d) {
      overviewCache = d;
      renderChannelBox(draft);
    }).catch(function () {});
    return;
  }
  const online = overviewCache && overviewCache.agent_online;
  const agentAcc = {};
  ((overviewCache && overviewCache.agent && overviewCache.agent.accounts) || [])
    .forEach(function (a) { agentAcc[a.platform] = a; });
  const accNote = (overviewCache && overviewCache.agent && overviewCache.agent.accounts
                   && overviewCache.agent.accounts.length)
    ? "" : '<div class="small-note">暂无平台账号快照（重启浏览器让宿主换代后，下一次心跳会带来）。</div>';
  box.innerHTML =
    '<div class="card-title">多平台分发</div>' +
    '<div class="small-note">审阅后勾选平台，本机分发桥会把稿件交给浏览器里的发布扩展执行'
    + '（需扩展已安装并登录对应平台）。'
    + (online ? '<span class="tag tag-green">分发桥在线</span>'
              : '<span class="tag tag-gray">分发桥离线——请确认宿主已注册且扩展已启动</span>')
    + "</div>" + accNote +
    '<div id="mp-checks" style="display:flex;gap:12px;flex-wrap:wrap;margin:6px 0">' +
    platforms.map(function (p) {
      const a = agentAcc[p.id];
      let chip = "";
      if (a) {
        const nick = a.nickname ? '（' + esc(a.nickname) + '）' : "";
        const tip = a.is_default ? '已设默认发布' + nick : '回退启用账号' + nick;
        if (a.enabled !== false) {
          chip = '<span class="tag tag-green" title="' + tip + '">就绪</span> ';
        } else {
          chip = '<span class="tag tag-red">已停用</span> ';
        }
      }
      return '<label style="cursor:pointer"><input type="checkbox" value="' + esc(p.id)
        + '"> ' + esc(p.name) + "</label>" + chip;
    }).join("") + "</div>" +
    '<button class="btn" id="mp-dispatch">分发到勾选平台</button>' +
    '<button class="btn" id="mp-refresh" style="margin-left:6px">刷新状态</button>' +
    '<div id="mp-list" class="mt-8"></div>';
  document.getElementById("mp-dispatch").addEventListener("click", function () {
    const checked = Array.prototype.slice.call(
      box.querySelectorAll("#mp-checks input:checked")).map(function (i) { return i.value; });
    if (!checked.length) { showToast("请先勾选平台", "error"); return; }
    geoApi("/api/distribution/drafts/" + draft.id + "/channels", {
      method: "POST", body: { platforms: checked },
    }).then(function (res) {
      showToast(res.message || "已排队", "success");
      loadChannels(draft.id);
      loadOverview();
    }).catch(function (m) { showToast(m || "排队失败", "error"); });
  });
  document.getElementById("mp-refresh").addEventListener("click", function () {
    loadChannels(draft.id);
    loadOverview();
  });
  loadChannels(draft.id);
}

function loadChannels(draftId) {
  geoApi("/api/distribution/drafts/" + draftId + "/channels").then(function (items) {
    const list = document.getElementById("mp-list");
    if (!list) return;
    if (!items.length) { list.innerHTML = ""; return; }
    const names = {};
    ((overviewCache && overviewCache.platforms) || []).forEach(function (p) {
      names[p.id] = p.name;
    });
    list.innerHTML = items.map(function (t) {
      const cls = t.status === "published" ? "tag-green"
        : t.status === "failed" ? "tag-red"
        : t.status === "dispatching" ? "tag-orange" : "tag-gray";
      const label = { published: "已发布", failed: "失败", dispatching: "分发中",
                      pending: "待分发" }[t.status] || t.status;
      let html = '<div style="display:flex;align-items:center;gap:8px;padding:4px 0">'
        + '<span class="tag ' + cls + '">' + label + "</span>"
        + "<span>" + esc(names[t.platform] || t.platform) + "</span>";
      if (t.platform_url) {
        html += '<a href="' + esc(t.platform_url) + '" target="_blank" rel="noopener" '
          + 'style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
          + esc(t.platform_url) + "</a>";
      } else {
        html += "<span style='flex:1'></span>";
      }
      if (t.status === "failed") {
        html += '<span class="small-note">' + esc(t.error_msg || "") + "</span>"
          + '<button class="btn" style="padding:2px 8px" data-retry="' + t.id + '">重试</button>';
      }
      return html + "</div>";
    }).join("");
    list.querySelectorAll("[data-retry]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        geoApi("/api/distribution/channels/" + btn.getAttribute("data-retry") + "/retry",
               { method: "POST", body: {} })
          .then(function () { loadChannels(draftId); loadOverview(); })
          .catch(function (m) { showToast(m || "重试失败", "error"); });
      });
    });
  }).catch(function () {});
}

/* ---------------- 稿件库 ---------------- */

function draftTimeRange() {
  const mode = (document.getElementById("draft-filter-time") || {}).value || "all";
  if (mode === "all") return null;
  const today = new Date();
  if (mode === "week") {
    const from = new Date(today.getTime() - 7 * 86400000);
    return { start: fmtDate(from), end: fmtDate(today) };
  }
  if (mode === "month") {
    const from = new Date(today.getTime() - 30 * 86400000);
    return { start: fmtDate(from), end: fmtDate(today) };
  }
  // 自定义：读取日历输入（起止可只填一端）
  const s = (document.getElementById("draft-date-start") || {}).value || "";
  const e = (document.getElementById("draft-date-end") || {}).value || "";
  if (!s && !e) return null;
  return { start: s, end: e };
}

function fmtDate(d) {
  const p = function (n) { return (n < 10 ? "0" : "") + n; };
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

function loadDrafts() {
  const statusFilter = (document.getElementById("draft-filter-status") || {}).value || "";
  const range = draftTimeRange();
  geoApi("/api/distribution/drafts").then(function (items) {
    const list = document.getElementById("draft-list");
    // 数据增长策略：状态筛选 + 时间范围（创建时间，闭区间）+ 时间倒序
    let shown = items.filter(function (d) {
      if (statusFilter && d.status !== statusFilter) return false;
      if (range) {
        const day = (d.created_at || "").slice(0, 10);
        if (range.start && day && day < range.start) return false;
        if (range.end && day && day > range.end) return false;
      }
      return true;
    });
    const total = shown.length;
    const stats = document.getElementById("draft-stats");
    if (stats) {
      let rangeText = "";
      if (range) rangeText = "，" + (range.start || "…") + " ~ " + (range.end || "今天");
      stats.textContent = "共 " + items.length + " 篇稿件，符合条件 " + total + " 篇" + rangeText;
    }
    if (!shown.length) {
      list.innerHTML = '<div class="empty">没有符合条件的稿件。</div>';
      return;
    }
    list.innerHTML = "";
    shown.forEach(function (d) {
      const cls = d.status === "published" ? "tag-green"
        : d.status === "failed" ? "tag-red" : "tag-gray";
      const label = d.status === "published" ? "已发布"
        : d.status === "failed" ? "发布失败" : "待审阅";
      const div = document.createElement("div");
      div.style.cssText = "display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border,#f0f0f0);flex-wrap:wrap";
      let meta = "";
      if (d.published_url) {
        meta += '<a href="' + esc(d.published_url) + '" target="_blank" rel="noopener" '
          + 'class="tag tag-green" style="text-decoration:none">查看发布内容 ↗</a>';
      }
      if (d.source_round_id) {
        meta += '<a href="/monitor.html?round_id=' + encodeURIComponent(d.source_round_id)
          + '" target="_blank" rel="noopener" class="small-note" style="text-decoration:none">简报溯源：第 '
          + esc(d.source_round_id) + " 轮监测 ↗</a>";
      } else if (d.brief) {
        const hint = (d.brief.angle || "").toString().slice(0, 30);
        meta += '<span class="small-note">来自创作简报' + (hint ? "：" + esc(hint) : "") + "</span>";
      }
      div.innerHTML =
        '<span class="tag ' + cls + '">' + label + "</span>" +
        '<span style="flex:1;min-width:120px;cursor:pointer" class="draft-title">' + esc(d.title) + "</span>" +
        '<span class="small-note">' + esc((d.created_at || "").slice(0, 16)) + "</span>" +
        meta +
        '<button class="btn" style="padding:2px 8px" data-act="del">删除</button>';
      div.querySelector(".draft-title").addEventListener("click", function () {
        geoApi("/api/distribution/drafts/" + d.id).then(function (full) {
          document.getElementById("gen-card").classList.remove("hidden");
          renderEditor(full, full.status !== "published");
          window.scrollTo({ top: 0, behavior: "smooth" });
        }).catch(function (m) { showToast(m || "读取失败", "error"); });
      });
      div.querySelector('[data-act="del"]').addEventListener("click", function () {
        confirmDialog("删除这篇稿件？该操作不可恢复。", function () {
          geoApi("/api/distribution/drafts/" + d.id, { method: "DELETE", body: {} }).then(function () {
            loadDrafts();
            showToast("已删除", "success");
          }).catch(function (m) { showToast(m || "删除失败", "error"); });
        }, { danger: true });
      });
      list.appendChild(div);
      // 平台明细子行：每篇稿件在各平台的发布状态与链接（懒加载，逐条填充）
      const sub = document.createElement("div");
      sub.style.cssText = "padding:0 0 6px 28px;border-bottom:1px solid var(--border,#f0f0f0)";
      sub.textContent = "";
      sub.innerHTML = '<div class="small-note" style="color:#999">平台明细加载中…</div>';
      list.appendChild(sub);
      geoApi("/api/distribution/drafts/" + d.id + "/channels").then(function (ch) {
        if (!ch.length) { sub.innerHTML = ""; return; }
        sub.innerHTML = ch.map(function (t) {
          const c = t.status === "published" ? "tag-green"
            : t.status === "failed" ? "tag-red"
            : t.status === "dispatching" ? "tag-orange" : "tag-gray";
          const lb = { published: "已发布", failed: "失败", dispatching: "分发中", pending: "待分发" }[t.status] || t.status;
          let html = '<div style="display:flex;align-items:center;gap:8px;padding:2px 0;flex-wrap:wrap">'
            + '<span class="tag ' + c + '" style="font-size:11px">' + lb + "</span>"
            + "<span class='small-note'>" + esc(t.platform) + "</span>";
          if (t.platform_url) {
            html += '<a href="' + esc(t.platform_url) + '" target="_blank" rel="noopener" '
              + 'class="small-note" style="text-decoration:none">打开 ↗</a>';
          }
          if (t.status === "failed" || t.status === "pending" || t.status === "dispatching") {
            if (t.status === "failed") {
              html += '<span class="small-note" style="color:#c0392b">' + esc((t.error_msg || "").slice(0, 80)) + "</span>"
                + '<button class="btn" style="padding:1px 6px;font-size:12px" data-retry="' + t.id + '">重试</button>';
            }
            html += '<button class="btn" style="padding:1px 6px;font-size:12px" data-manual="' + t.id + '">手动已发</button>';
          }
          return html + "</div>";
        }).join("");
        sub.querySelectorAll("[data-retry]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            geoApi("/api/distribution/channels/" + btn.getAttribute("data-retry") + "/retry",
                   { method: "POST", body: {} })
              .then(function () { loadDrafts(); loadOverview(); })
              .catch(function (m) { showToast(m || "重试失败", "error"); });
          });
        });
        sub.querySelectorAll("[data-manual]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            const url = prompt("你已在平台上手动发布成功——可粘贴发布后的文章链接（不知道就留空）：", "");
            if (url === null) return;
            geoApi("/api/distribution/channels/" + btn.getAttribute("data-manual") + "/manual",
                   { method: "POST", body: { platform_url: (url || "").trim() } })
              .then(function () { loadDrafts(); loadOverview(); showToast("已记录手动发布成功", "success"); })
              .catch(function (m) { showToast(m || "记录失败", "error"); });
          });
        });
      }).catch(function () { sub.innerHTML = ""; });
    });
  }).catch(function () {
    document.getElementById("draft-list").innerHTML = '<div class="empty">稿件库读取失败。</div>';
  });
}

document.getElementById("draft-filter-status").addEventListener("change", loadDrafts);
document.getElementById("draft-filter-time").addEventListener("change", function () {
  const custom = document.getElementById("draft-custom-range");
  if (this.value === "custom") {
    custom.classList.remove("hidden");
    // 默认预填最近一月，方便直接调整
    if (!document.getElementById("draft-date-start").value) {
      document.getElementById("draft-date-start").value = fmtDate(new Date(Date.now() - 30 * 86400000));
    }
    if (!document.getElementById("draft-date-end").value) {
      document.getElementById("draft-date-end").value = fmtDate(new Date());
    }
  } else {
    custom.classList.add("hidden");
  }
  loadDrafts();
});
document.getElementById("draft-date-start").addEventListener("change", loadDrafts);
document.getElementById("draft-date-end").addEventListener("change", loadDrafts);

/* ---------------- 绑定 ---------------- */

document.getElementById("brief-btn").addEventListener("click", generateBrief);
document.getElementById("gen-btn").addEventListener("click", generateArticle);
document.getElementById("cfg-save").addEventListener("click", function () {
  geoApi("/api/distribution/config", {
    method: "POST",
    body: {
      base_url: document.getElementById("cfg-base").value,
      publish_path: document.getElementById("cfg-path").value,
      category: document.getElementById("cfg-category").value,
      author: document.getElementById("cfg-author").value,
      token: document.getElementById("cfg-token").value,
    },
  }).then(function (d) {
    document.getElementById("cfg-token").value = "";
    document.getElementById("cfg-token-masked").textContent =
      d.configured ? ("当前 Token：" + d.token_masked) : "尚未配置 Token";
    const hint = document.getElementById("cfg-saved");
    hint.textContent = "已保存，立即生效";
    hint.classList.remove("hidden");
    setTimeout(function () { hint.classList.add("hidden"); }, 2500);
  }).catch(function (m) { showToast(m || "保存失败", "error"); });
});

loadOverview();
loadDrafts();

// ================= 知识库（参考 weiqi knowledgeDocs/蒸馏词能力，自研实现） =================
(function () {
  var editingDocId = null;
  var docsCache = [];

  function el(id) { return document.getElementById(id); }

  function loadDocs() {
    geoApi("/api/knowledge/docs").then(function (d) {
      docsCache = (d && d.docs) || [];
      renderDocs();
    }).catch(function (m) { renderDocs(); });
  }

  function renderDocs() {
    var box = el("kb-doc-list");
    if (!docsCache.length) {
      box.innerHTML = '<div class="small-note">知识库还是空的——点右上角「新增文档」，把公司资料、产品说明、过往佳作存进来。</div>';
      return;
    }
    var html = "";
    docsCache.forEach(function (doc) {
      var kws = [];
      try { kws = JSON.parse(doc.keywords || "[]"); } catch (e) { kws = []; }
      html += '<div style="display:flex;align-items:flex-start;gap:8px;padding:6px 0;border-bottom:1px solid #f0f0f0">'
        + '<input type="checkbox" class="kb-check" value="' + doc.id + '" style="margin-top:4px">'
        + '<div style="flex:1;min-width:0">'
        + '<div style="font-weight:600">' + esc(doc.title) + '</div>'
        + '<div class="small-note">' + esc((doc.content || "").slice(0, 60)) + '…</div>'
        + (kws.length ? '<div class="small-note">蒸馏词：' + esc(kws.join("、")) + '</div>' : "")
        + '</div>'
        + '<button class="btn" data-kb-kw="' + doc.id + '" style="margin-right:4px">提取关键词</button>'
        + '<button class="btn" data-kb-edit="' + doc.id + '" style="margin-right:4px">编辑</button>'
        + '<button class="btn" data-kb-del="' + doc.id + '">删除</button>'
        + '</div>';
    });
    box.innerHTML = html;
  }

  el("kb-doc-list").addEventListener("click", function (ev) {
    var t = ev.target;
    var kwId = t.getAttribute && t.getAttribute("data-kb-kw");
    var editId = t.getAttribute && t.getAttribute("data-kb-edit");
    var delId = t.getAttribute && t.getAttribute("data-kb-del");
    if (kwId) {
      t.disabled = true; t.textContent = "提取中…";
      geoApi("/api/knowledge/docs/" + kwId + "/keywords", { method: "POST", body: { count: 10 } })
        .then(function (d) { loadDocs(); showToast("已提取 " + (d.keywords || []).length + " 个关键词", "success"); })
        .catch(function (m) { t.disabled = false; t.textContent = "提取关键词"; showToast(m || "提取失败", "error"); });
    } else if (editId) {
      var doc = docsCache.find(function (x) { return String(x.id) === String(editId); });
      if (!doc) return;
      editingDocId = doc.id;
      el("kb-title").value = doc.title;
      el("kb-content").value = doc.content;
      el("kb-edit-card").classList.remove("hidden");
      el("kb-edit-card").scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (delId) {
      if (!confirm("确定删除这篇知识库文档？")) return;
      geoApi("/api/knowledge/docs/" + delId, { method: "DELETE", body: {} })
        .then(function () { loadDocs(); showToast("已删除", "success"); })
        .catch(function (m) { showToast(m || "删除失败", "error"); });
    }
  });

  el("kb-add-btn").addEventListener("click", function () {
    editingDocId = null;
    el("kb-title").value = "";
    el("kb-content").value = "";
    el("kb-edit-card").classList.remove("hidden");
    el("kb-title").focus();
  });

  el("kb-cancel-btn").addEventListener("click", function () {
    el("kb-edit-card").classList.add("hidden");
  });

  el("kb-save-btn").addEventListener("click", function () {
    var title = el("kb-title").value.trim();
    var content = el("kb-content").value;
    if (!title) { showToast("请填写文档标题", "error"); return; }
    if (!content.trim()) { showToast("请填写文档内容", "error"); return; }
    var req = editingDocId
      ? geoApi("/api/knowledge/docs/" + editingDocId, { method: "PUT", body: { title: title, content: content } })
      : geoApi("/api/knowledge/docs", { method: "POST", body: { title: title, content: content } });
    req.then(function () {
      el("kb-edit-card").classList.add("hidden");
      loadDocs();
      showToast("文档已保存", "success");
    }).catch(function (m) { showToast(m || "保存失败", "error"); });
  });

  el("kb-generate-btn").addEventListener("click", function () {
    var ids = Array.prototype.map.call(document.querySelectorAll(".kb-check:checked"), function (c) { return c.value; });
    if (!ids.length) { showToast("请先勾选至少一篇知识库文档", "error"); return; }
    var btn = el("kb-generate-btn"), hint = el("kb-gen-hint");
    btn.disabled = true; hint.classList.remove("hidden");
    geoApi("/api/knowledge/generate", {
      method: "POST",
      body: { doc_ids: ids, user_instruction: el("kb-instruction").value.trim(), generate_title: true }
    }).then(function (draft) {
      btn.disabled = false; hint.classList.add("hidden");
      showToast("稿件《" + (draft.title || "") + "》已生成，请在下方稿件库审阅后分发", "success");
      if (typeof loadDrafts === "function") loadDrafts();
    }).catch(function (m) {
      btn.disabled = false; hint.classList.add("hidden");
      showToast(m || "生成失败", "error");
    });
  });

  window.loadKnowledgeDocs = loadDocs;
  loadDocs();
})();

// —— 知识库文件上传（.md/.txt/.pdf/.docx/.xlsx，单个 ≤ 20MB）——
(function () {
  function el(id) { return document.getElementById(id); }
  var input = el("kb-file-input");
  el("kb-upload-btn").addEventListener("click", function () { input.click(); });
  input.addEventListener("change", function () {
    var files = Array.prototype.slice.call(input.files || []);
    if (!files.length) return;
    var btn = el("kb-upload-btn");
    btn.disabled = true; btn.textContent = "解析上传中…";
    var done = 0, results = [];
    var finish = function () {
      btn.disabled = false; btn.textContent = "📎 上传文档/表格";
      input.value = "";
      if (typeof window.loadKnowledgeDocs === "function") window.loadKnowledgeDocs();
      showToast(results.join("；") || "上传完成", failedCount() ? "error" : "success");
    };
    var failedCount = function () { return results.filter(function (r) { return r.indexOf("失败") >= 0; }).length; };
    files.forEach(function (f) {
      if (f.size > 20 * 1024 * 1024) { results.push(f.name + " 失败（超过 20MB）"); if (++done === files.length) finish(); return; }
      var fd = new FormData();
      fd.append("file", f);
      fetch("/api/knowledge/docs/upload", { method: "POST", body: fd })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          results.push(j.ok ? (f.name + " 已入库") : (f.name + " 失败（" + (j.message || "未知") + "）"));
          if (++done === files.length) finish();
        })
        .catch(function () {
          results.push(f.name + " 失败（网络错误）");
          if (++done === files.length) finish();
        });
    });
  });
  function loadDocsLocal() { if (typeof window.loadKnowledgeDocs === "function") window.loadKnowledgeDocs(); }
})();
