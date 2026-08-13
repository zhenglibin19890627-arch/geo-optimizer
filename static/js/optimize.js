/* ============================================================
   内容优化页（UI 文档 4.2）：链接/文字双 Tab + 进度 + 建议 + 历史
   ============================================================ */

const OPT_LIMIT = 50000;
let optCurrentTab = "url";
let optPolling = null;

function optInit() {
  document.querySelectorAll(".tabs .tab-item").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".tabs .tab-item").forEach(function (t) {
        t.classList.remove("active");
      });
      tab.classList.add("active");
      optCurrentTab = tab.getAttribute("data-tab");
      document.getElementById("url-field").classList.toggle("hidden", optCurrentTab !== "url");
      document.getElementById("text-field").classList.toggle("hidden", optCurrentTab !== "text");
      hideOptResult();
      hideOptProgress();
    });
  });

  document.getElementById("opt-text").addEventListener("input", function () {
    const n = this.value.length;
    document.getElementById("text-count").textContent = n + "/50000 字";
  });

  document.getElementById("opt-start").addEventListener("click", optStart);

  loadOptHistory(1, true);
  resumeRunningOpt();
}

/* ---------------- 发起分析 ---------------- */

function optStart() {
  const btn = document.getElementById("opt-start");
  const url = document.getElementById("opt-url").value.trim();
  const content = document.getElementById("opt-text").value.trim();

  if (optCurrentTab === "url") {
    if (!url) {
      showToast("请填一下网页链接（以 http:// 或 https:// 开头）", "error");
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      showToast("链接格式不对，请检查是否以 http:// 或 https:// 开头", "error");
      return;
    }
  } else {
    if (!content) {
      showToast("请把要分析的文字粘贴进来", "error");
      return;
    }
    if (content.length > OPT_LIMIT) {
      showToast("内容太长啦，最多 5 万字，可以删掉一部分再试", "error");
      return;
    }
    if (content.length < 20) {
      showToast("内容太短了（至少 20 个字），分析出来没有意义", "error");
      return;
    }
  }

  const body = optCurrentTab === "url"
    ? { type: "url", url: url }
    : { type: "text", content: content };

  btn.disabled = true;
  btn.textContent = "分析中…";
  showOptProgress(optCurrentTab === "url" ? "正在读取网页内容…" : "正在请 AI 帮你找问题…");

  apiPost("/api/optimize", body).then(function (data) {
    pollOptProgress(data.record_id);
  }).catch(function () {
    btn.disabled = false;
    btn.textContent = "开始分析";
    hideOptProgress();
  });
}

function showOptProgress(text) {
  const area = document.getElementById("opt-progress-area");
  area.classList.remove("hidden");
  document.getElementById("opt-progress-bar").style.width = "0%";
  document.getElementById("opt-progress-text").textContent = text;
}

function hideOptProgress() {
  document.getElementById("opt-progress-area").classList.add("hidden");
}

function pollOptProgress(recordId) {
  optPolling = startPolling(
    function () { return geoApi("/api/optimize/" + recordId); },
    function (data) {
      const bar = document.getElementById("opt-progress-bar");
      const text = document.getElementById("opt-progress-text");

      if (data.status === "done") {
        bar.style.width = "100%";
        text.textContent = "分析完成";
        finishOpt(recordId, data);
        return;
      }
      if (data.status === "failed") {
        bar.style.width = "100%";
        text.textContent = "没成功";
        failOpt(data.error_msg || "分析没成功，请稍后再试一次");
        return;
      }
      bar.style.width = Math.min(data.status === "pending" ? 10 : 45, 80) + "%";
      text.textContent = "正在请 AI 帮你找问题…（预计 1-3 分钟）";
    },
    function () {
      resetOptButton();
      hideOptProgress();
    }
  );
}

function resetOptButton() {
  const btn = document.getElementById("opt-start");
  btn.disabled = false;
  btn.textContent = "开始分析";
}

function finishOpt(recordId, data) {
  resetOptButton();
  hideOptProgress();
  renderOptResult(data);
  loadOptHistory(1, true);
}

function failOpt(message) {
  resetOptButton();
  hideOptProgress();
  hideOptResult();
  showToast("分析没成功：" + message, "error");
}

/* ---------------- 结果渲染 ---------------- */

function geoScoreSentence(score) {
  if (score >= 90) return "非常不错！内容很容易被 AI 引用";
  if (score >= 70) return "还不错，再打磨一下更容易被 AI 引用";
  if (score >= 50) return "一般般，按下面的建议调整后更容易被 AI 引用";
  if (score >= 30) return "偏弱，建议按下面的建议重点修改";
  return "还有很大提升空间，从下面的建议开始改吧";
}

function hideOptResult() {
  document.getElementById("opt-result-card").classList.add("hidden");
  document.getElementById("opt-result-area").innerHTML = "";
}

function renderOptResult(data) {
  const card = document.getElementById("opt-result-card");
  card.classList.remove("hidden");
  const area = document.getElementById("opt-result-area");
  const score = data.geo_score;
  const color = scoreColor(score);

  const typeText = data.input_type === "url" ? "网页链接" : "粘贴文字";

  let html =
    '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
    '<span class="score-number" style="color:' + color + '">' + esc(score) + "</span>" +
    '<span class="score-sub">GEO 友好度 ' + conceptBubble("这个分数代表内容被 AI 引用的难易程度，满分 100") + "</span>" +
    '<span class="tag tag-gray">' + esc(typeText) + "</span>" +
    "</div>" +
    '<div class="score-sub mt-8">' + esc(geoScoreSentence(score)) + "</div>";

  const suggestions = data.suggestions || [];
  if (suggestions.length) {
    html += '<div class="card-title mt-16">优化建议</div>';
    suggestions.forEach(function (s, i) {
      html +=
        '<div class="advice-item" data-i="' + i + '">' +
        '<div class="advice-head">' + priorityTag(s.priority) +
        '<span class="advice-title">' + esc(s.title) + "</span></div>" +
        '<div class="advice-detail">' + esc(s.detail) + "</div>" +
        "</div>";
    });
  } else {
    html += '<div class="mt-8 score-sub">这次没有生成具体建议，可能是内容或钥匙状态问题，稍后再试一次。' +
      ' <a class="btn-text" href="/static/settings.html#keys">去设置页</a></div>';
  }

  area.innerHTML = html;
  area.querySelectorAll(".advice-item").forEach(function (item) {
    item.addEventListener("click", function () {
      const detail = item.querySelector(".advice-detail");
      detail.classList.toggle("full");
    });
  });
}

/* ---------------- 历史记录 ---------------- */

let optHistoryPage = 1;

function loadOptHistory(page, fresh) {
  geoApi("/api/optimize/history?page=" + page).then(function (data) {
    if (fresh) {
      optHistoryPage = 1;
      document.getElementById("opt-history-area").innerHTML = "";
    }
    optHistoryPage = page;
    const area = document.getElementById("opt-history-area");
    const items = data.items || [];

    if (!items.length && fresh) {
      area.appendChild(emptyState(
        "还没有优化记录",
        "把要优化的文章链接或文字贴进来试试吧",
        "去粘贴内容",
        function () {
          document.getElementById("opt-start").scrollIntoView({ behavior: "smooth", block: "center" });
        }
      ));
      document.getElementById("opt-history-more").innerHTML = "";
      return;
    }

    items.forEach(function (item) {
      const typeText = item.input_type === "url" ? "网页" : "文字";
      const scoreBadge = item.geo_score !== null && item.geo_score !== undefined
        ? '<span class="badge-score" style="color:' + scoreColor(item.geo_score) + ";background:" + scoreColor(item.geo_score) + "1A" + '">' + item.geo_score + " 分</span>"
        : '<span class="tag tag-gray">未完成</span>';
      const row = document.createElement("div");
      row.className = "list-row";
      row.style.cursor = "pointer";
      row.innerHTML =
        '<div class="row-main">' +
        '<div class="row-time">' + esc(item.created_at || "") + " · " + esc(typeText) + "</div>" +
        '<div class="mt-8" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-sub)">' +
        esc(item.url || "（粘贴的文字内容）") + "</div>" +
        "</div>" +
        scoreBadge;
      row.addEventListener("click", function () {
        loadOptDetail(item.id);
      });
      area.appendChild(row);
    });

    const moreBtn = document.getElementById("opt-history-more");
    moreBtn.innerHTML = "";
    if (data.total > page * data.page_size) {
      const btn = document.createElement("button");
      btn.className = "btn btn-secondary";
      btn.textContent = "查看更多 →";
      btn.addEventListener("click", function () {
        loadOptHistory(page + 1, false);
      });
      moreBtn.appendChild(btn);
    }
  }).catch(function () {});
}

function loadOptDetail(recordId) {
  geoApi("/api/optimize/" + recordId).then(function (data) {
    if (data.status === "done") {
      renderOptResult(data);
      document.getElementById("opt-result-card").scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (data.status === "failed") {
      showToast("这条记录当时没分析成功：" + (data.error_msg || "请稍后再试"), "error");
    } else {
      showOptProgress("继续上次的分析…");
      pollOptProgress(recordId);
    }
  }).catch(function () {});
}

/* 离开页面再回来：恢复进行中的分析 */
function resumeRunningOpt() {
  geoApi("/api/optimize/history?page=1").then(function (data) {
    const running = (data.items || []).find(function (i) {
      return i.status === "pending" || i.status === "running";
    });
    if (running) {
      const btn = document.getElementById("opt-start");
      btn.disabled = true;
      btn.textContent = "分析中…";
      showOptProgress("正在请 AI 帮你找问题…（预计 1-3 分钟）");
      pollOptProgress(running.id);
    }
  }).catch(function () {});
}

initNav("optimize");
bindConcepts(document);
optInit();
