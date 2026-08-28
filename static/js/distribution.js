/* ============================================================
   GEO 优化系统 - 内容分发页（监测缺口 → 简报 → 生成 → 审阅 → 官网直发）
   闭环：发布成功后，报告页信源排行会把这些域名标为「已布点」，
   下一轮简报也会把已布点域名从缺口中剔除。
   ============================================================ */

initNav("distribution");

let currentBrief = null;   // 当前简报（生成文章时随请求回传，保证所见即所得）
let selectedTopicIdx = -1; // 简报中选中的主题（生成时作为首选）

/* ---------------- 首屏：配置 + 稿件统计 ---------------- */

function loadOverview() {
  geoApi("/api/distribution/overview").then(function (d) {
    renderDraftStats(d.stats);
  }).catch(function () {});
  loadConfig();
}

function loadConfig() {
  geoApi("/api/distribution/config").then(function (d) {
    document.getElementById("cfg-base").value = d.base_url || "";
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
    '<div class="small-note mt-8" id="ed-msg"></div>';

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

/* ---------------- 稿件库 ---------------- */

function loadDrafts() {
  geoApi("/api/distribution/drafts").then(function (items) {
    const list = document.getElementById("draft-list");
    if (!items.length) {
      list.innerHTML = '<div class="empty">还没有稿件，先在左侧生成简报和文章。</div>';
      return;
    }
    list.innerHTML = "";
    items.forEach(function (d) {
      const cls = d.status === "published" ? "tag-green"
        : d.status === "failed" ? "tag-red" : "tag-gray";
      const label = d.status === "published" ? "已发布"
        : d.status === "failed" ? "发布失败" : "待审阅";
      const div = document.createElement("div");
      div.style.cssText = "display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border,#f0f0f0)";
      div.innerHTML =
        '<span class="tag ' + cls + '">' + label + "</span>" +
        '<span style="flex:1;cursor:pointer" class="draft-title">' + esc(d.title) + "</span>" +
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
    });
  }).catch(function () {
    document.getElementById("draft-list").innerHTML = '<div class="empty">稿件库读取失败。</div>';
  });
}

/* ---------------- 绑定 ---------------- */

document.getElementById("brief-btn").addEventListener("click", generateBrief);
document.getElementById("gen-btn").addEventListener("click", generateArticle);
document.getElementById("cfg-save").addEventListener("click", function () {
  geoApi("/api/distribution/config", {
    method: "POST",
    body: {
      base_url: document.getElementById("cfg-base").value,
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
