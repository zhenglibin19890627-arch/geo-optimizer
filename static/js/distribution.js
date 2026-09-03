/* ============================================================
   GEO 优化系统 - 内容分发页（监测缺口 → 简报 → 生成 → 审阅 → 官网直发）
   闭环：发布成功后，报告页信源排行会把这些域名标为「已布点」，
   下一轮简报也会把已布点域名从缺口中剔除。
   ============================================================ */

initNav("distribution");

let currentBrief = null;   // 当前简报（生成文章时随请求回传，保证所见即所得）
let selectedTopicIdx = -1; // 简报中选中的主题（生成时作为首选）
let overviewCache = null;  // 首屏数据（平台清单/扩展在线状态）

/* ---------------- 首屏：配置 + 平台清单（状态展示已迁移至稿件库独立页） ---------------- */

function loadOverview() {
  geoApi("/api/distribution/overview").then(function (d) {
    overviewCache = d;
  }).catch(function () {});
  loadConfig();
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
    loadOverview();
  }).catch(function (m) {
    btn.disabled = false;
    document.getElementById("gen-hint").classList.add("hidden");
    showToast(m || "生成失败，请稍后再试", "error");
  });
}

/* ---------------- 编辑器（生成/审阅共用） ---------------- */

/* 收集编辑器当前内容；分发/发布/建议/优化前都先保存，保证发出去的就是看到的 */
function collectEditorDraft() {
  return {
    title: document.getElementById("ed-title").value.slice(0, 30),
    body_md: document.getElementById("ed-body").value,
    summary: document.getElementById("ed-summary").value,
    tags: document.getElementById("ed-tags").value,
  };
}

function saveEditorDraft(draftId) {
  if (!document.getElementById("ed-title")) return Promise.resolve();
  return geoApi("/api/distribution/drafts/" + draftId,
                { method: "PUT", body: collectEditorDraft() });
}

function renderEditor(draft, showPublish) {
  const area = document.getElementById("gen-editor-area");
  area.innerHTML =
    '<div class="field"><label class="field-label">标题（30 字内）</label>' +
    '<input class="input" id="ed-title" maxlength="30" value="' + esc(draft.title) + '"></div>' +
    '<div class="field"><label class="field-label">正文（Markdown，可编辑）</label>' +
    '<textarea class="textarea" id="ed-body" style="min-height:280px">' + esc(draft.body_md) + "</textarea></div>" +
    '<div class="field"><label class="field-label">摘要（150 字内，发布用）</label>' +
    '<textarea class="textarea" id="ed-summary" style="min-height:52px">' + esc(draft.summary) + "</textarea></div>" +
    '<div class="field"><label class="field-label">标签（逗号分隔）</label>' +
    '<input class="input" id="ed-tags" value="' + esc(draft.tags) + '"></div>' +
    '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
    '<button class="btn btn-primary" id="ed-save">保存修改</button>' +
    '<button class="btn" id="ed-advice">GEO 优化建议</button>' +
    (showPublish ? '<button class="btn btn-primary" id="ed-publish">发布到官网</button>' : "") +
    '<span class="saved-hint hidden" id="ed-hint"></span></div>' +
    '<div class="small-note mt-8" id="ed-msg"></div>' +
    '<div class="mt-8 hidden" id="advice-box" style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 14px"></div>' +
    '<div class="mt-8" id="mp-box"></div>';
  renderChannelBox(draft);

  document.getElementById("ed-advice").addEventListener("click", function () {
    const btn = this, box = document.getElementById("advice-box");
    btn.disabled = true; btn.textContent = "分析中…（约 30 秒）";
    // 先保存最新编辑，保证建议针对当前内容
    saveEditorDraft(draft.id).then(function () {
      return geoApi("/api/distribution/drafts/" + draft.id + "/advice",
                    { method: "POST", body: { allow_links: linksAllowedForDraft() } });
    }).then(function (res) {
      btn.disabled = false; btn.textContent = "GEO 优化建议";
      box.classList.remove("hidden");
      const sc = res.score || {};
      const noLink = !linksAllowedForDraft();
      let html = '<div style="font-weight:700;margin-bottom:6px">GEO 友好度：'
        + esc(String(sc.score != null ? sc.score : "-")) + " / 100</div>";
      if (noLink) {
        html += '<div class="small-note">已按「目标平台不允许外链」口径评分：'
          + '无外链得满分，稿子里有外链会被提示发布前移除。</div>';
      }
      (res.suggestions || []).forEach(function (s) {
        const pc = s.priority === "高" ? "#DC2626" : s.priority === "低" ? "#6B7280" : "#F59E0B";
        html += '<div style="margin:8px 0"><span style="color:' + pc + ';font-weight:700">['
          + esc(s.priority) + ']</span> <b>' + esc(s.title) + "</b><br>"
          + '<span class="small-note">' + esc(s.detail) + "</span></div>";
      });
      if (!(res.suggestions || []).length) {
        html += '<div class="small-note">AI 这次没能给出建议，请稍后再试。</div>';
      }
      html += '<div class="mt-8"><button class="btn btn-primary" id="ed-optimize">按建议一键优化（重写全文）</button> '
        + '<span class="small-note">重写后回到编辑器，确认满意再点「保存修改」</span></div>'
        + '<div class="mt-8 hidden" id="opt-result"></div>';
      box.innerHTML = html;
      bindOptimize(draft);
    }).catch(function (m) {
      btn.disabled = false; btn.textContent = "GEO 优化建议";
      showToast(m || "分析失败", "error");
    });
  });

  document.getElementById("ed-save").addEventListener("click", function () {
    saveEditorDraft(draft.id).then(function () {
      showToast("稿件已保存", "success");
      loadOverview();
    }).catch(function (m) { showToast(m || "保存失败", "error"); });
  });

  const pub = document.getElementById("ed-publish");
  if (pub) {
    pub.addEventListener("click", function () {
      pub.disabled = true;
      /* 发布前先保存最新编辑内容，保证发出去的就是看到的 */
      saveEditorDraft(draft.id).then(function () {
        return geoApi("/api/distribution/drafts/" + draft.id + "/publish", { method: "POST", body: {} });
      }).then(function (res) {
        document.getElementById("ed-msg").textContent =
          "已发布到官网" + (res.url ? "：" + res.url : "") + "。报告页信源排行将把它标注为「已布点」。";
        showToast("已发布到官网", "success");
        loadOverview();
        loadConfig();
      }).catch(function (m) {
        pub.disabled = false;
        document.getElementById("ed-msg").textContent = "发布失败：" + (m || "请稍后再试") + "（稿件状态已记为失败，可修改后重试）";
        showToast(m || "发布失败", "error");
      });
    });
  }
}

/* ---------------- 一键 GEO 优化（打完分之后的针对性重写） ---------------- */

/* 勾了不允许外链的平台（如搜狐/头条）→ 评分与优化按「无外链」口径；
   没勾或只勾官网/知乎 → 维持原口径（外链是加分项）。政策来自 overview 接口。 */
function linksAllowedForDraft() {
  const box = document.getElementById("mp-checks");
  if (!box) return true;
  const checked = Array.prototype.slice.call(box.querySelectorAll("input:checked"))
    .map(function (i) { return i.value; })
    .filter(function (v) { return v !== "site"; });
  if (!checked.length) return true;
  const byId = {};
  ((overviewCache && overviewCache.platforms) || []).forEach(function (p) {
    byId[p.id] = p;
  });
  return !checked.some(function (id) {
    return byId[id] && byId[id].allow_links === false;
  });
}

/* 按建议重写全文：新稿回填编辑器但不自动保存，人工确认后才生效。 */
function bindOptimize(draft) {
  const btn = document.getElementById("ed-optimize");
  if (!btn) return;
  const origLabel = btn.textContent;
  btn.addEventListener("click", function () {
    const box = document.getElementById("opt-result");
    const prev = {
      title: document.getElementById("ed-title").value,
      body: document.getElementById("ed-body").value,
    };
    btn.disabled = true; btn.textContent = "优化中…（约 2~3 分钟，请勿关闭页面）";
    // 先保存最新编辑，保证优化针对当前内容（与「优化建议」按钮同口径）
    saveEditorDraft(draft.id).then(function () {
      return geoApi("/api/distribution/drafts/" + draft.id + "/optimize",
                    { method: "POST", body: { allow_links: linksAllowedForDraft() } });
    }).then(function (res) {
      document.getElementById("ed-title").value = res.title || "";
      document.getElementById("ed-body").value = res.body_md || "";
      btn.disabled = false; btn.textContent = "再优化一轮";
      box.classList.remove("hidden");
      box.innerHTML = renderOptResult(res, prev);
      const undo = document.getElementById("opt-undo");
      if (undo) undo.addEventListener("click", function () {
        document.getElementById("ed-title").value = prev.title;
        document.getElementById("ed-body").value = prev.body;
        box.classList.add("hidden");
        box.innerHTML = "";
        btn.textContent = origLabel;
        showToast("已撤销本次优化", "success");
      });
    }).catch(function (m) {
      btn.disabled = false; btn.textContent = origLabel;
      showToast(m || "优化失败，请稍后再试", "error");
    });
  });
}

/* 优化结果：总分前后对比 + 分项变化 + 改动说明 + 撤销按钮。 */
function renderOptResult(res, prev) {
  const sb = res.score_before || {}, sa = res.score_after || {};
  const beforeMap = {};
  (sb.breakdown || []).forEach(function (p) { beforeMap[p.title] = p.score; });
  let html = '<div style="font-weight:700;margin-bottom:6px">GEO 友好度：'
    + esc(String(sb.score != null ? sb.score : "-")) + ' → <span style="color:#059669">'
    + esc(String(sa.score != null ? sa.score : "-")) + "</span> / 100</div>";
  html += '<div class="small-note">分项变化：</div>';
  if ((sa.breakdown || []).length === 0) {
    html += '<div class="small-note">这次没能算出分项，只看总分。</div>';
  }
  (sa.breakdown || []).forEach(function (p) {
    const had = Object.prototype.hasOwnProperty.call(beforeMap, p.title);
    const diff = had ? p.score - beforeMap[p.title] : p.score;
    const color = diff > 0 ? "#059669" : diff < 0 ? "#DC2626" : "#6B7280";
    html += '<div>· ' + esc(p.title) + "："
      + (had ? beforeMap[p.title] : 0) + " → " + p.score
      + ' <span style="color:' + color + '">' + (diff > 0 ? "+" : "") + diff + "</span>"
      + (had ? "" : "（新增）") + "</div>";
  });
  html += '<div class="mt-8" style="font-weight:700">改了什么：</div>';
  if (!(res.changes || []).length) {
    html += '<div class="small-note">模型没有说明改动点。</div>';
  }
  (res.changes || []).forEach(function (c) {
    html += '<div style="margin:6px 0"><b>' + esc(c.title) + "</b><br>"
      + '<span class="small-note">' + esc(c.detail) + "</span></div>";
  });
  html += '<div class="small-note mt-8">新稿已填进上面的编辑器（尚未保存），检查满意请点「保存修改」。</div>';
  html += '<button class="btn mt-8" id="opt-undo">撤销本次优化</button>';
  return html;
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
    }).join("")
    + '<label style="cursor:pointer"><input type="checkbox" value="site"> 官网（自有站点，勾选即直发）</label>'
    + "</div>" +
    '<button class="btn" id="mp-dispatch">分发到勾选平台</button>' +
    '<button class="btn" id="mp-refresh" style="margin-left:6px">刷新状态</button>' +
    '<div id="mp-list" class="mt-8"></div>';
  document.getElementById("mp-dispatch").addEventListener("click", function () {
    const checked = Array.prototype.slice.call(
      box.querySelectorAll("#mp-checks input:checked")).map(function (i) { return i.value; });
    if (!checked.length) { showToast("请先勾选平台", "error"); return; }
    /* 分发前先保存编辑器最新内容（含一键优化后未保存的改写稿），
       保证发出去的就是看到的——任务只存稿件号，宿主领取时读数据库 */
    saveEditorDraft(draft.id).then(function () {
      return geoApi("/api/distribution/drafts/" + draft.id + "/channels", {
        method: "POST", body: { platforms: checked },
      });
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
    const names = { site: "官网" };
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

/* 稿件库已迁移至独立页 /static/drafts.html（导航「稿件库」），此处不再重复渲染 */

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

// 从稿件库「去编辑」跳转过来（#draft-<id>）：直接把这篇稿件打开进编辑器审阅
(function () {
  const m = String(location.hash || "").match(/^#draft-(\d+)$/);
  if (!m) return;
  geoApi("/api/distribution/drafts/" + m[1]).then(function (draft) {
    document.getElementById("gen-card").classList.remove("hidden");
    renderEditor(draft, true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }).catch(function (msg) {
    showToast(msg || "没找到这篇稿件，可能已被删除", "error");
  });
})();

// ================= 链接改写（第三种创作方式） =================
(function () {
  function el(id) { return document.getElementById(id); }
  el("rw-btn").addEventListener("click", function () {
    const url = el("rw-url").value.trim();
    if (!url) { showToast("请先粘贴文章链接", "error"); return; }
    const btn = el("rw-btn"), hint = el("rw-hint");
    btn.disabled = true; hint.classList.remove("hidden");
    geoApi("/api/knowledge/rewrite", {
      method: "POST",
      body: { url: url, user_instruction: el("rw-instruction").value.trim() }
    }).then(function (draft) {
      btn.disabled = false; hint.classList.add("hidden");
      showToast("改写完成《" + (draft.title || "") + "》，请审阅后分发", "success");
      el("gen-card").classList.remove("hidden");
      renderEditor(draft, true);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }).catch(function (m) {
      btn.disabled = false; hint.classList.add("hidden");
      showToast(m || "改写失败", "error");
    });
  });
})();

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
      loadOverview();
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
