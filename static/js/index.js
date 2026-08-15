/* ============================================================
   首页（UI 文档 4.1）：评分卡 + 预警 + 最近一轮 + 下次定时 + 快捷入口
   ============================================================ */

function fmtNextRun(str) {
  if (!str) return "";
  const m = String(str).match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/);
  if (!m) return str;
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const that = new Date(+m[1], +m[2] - 1, +m[3]);
  const diff = Math.round((that - today) / 86400000);
  const hm = m[4] + ":" + m[5];
  if (diff === 1) return "明天 " + hm;
  if (diff === 0) return "今天 " + hm;
  return (+m[2]) + "月" + (+m[3]) + "日 " + hm;
}

function fmtClock(str) {
  if (!str) return "";
  const m = String(str).match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/);
  if (!m) return str;
  return (+m[2]) + "月" + (+m[3]) + "日 " + m[4] + ":" + m[5];
}

/* 分数怎么算出来的面板：分项直接读后端 /api/overview 的 score_breakdown（读快照 breakdown，与总分同源），不再前端重算 */
function renderScorePanel(area, score, breakdown) {
  const color = scoreColor(score);
  const band = scoreBand(score);
  const bd = breakdown || {};

  /* 后端 breakdown 键 → 展示行（文案按 03b 设计基线一字不动） */
  const rows = [
    ["mention_cover", "提及覆盖分（40 分）：AI 回答里提到你的比例"],
    ["sentiment", "情感分（20 分）：夸你的回答减去说坏话的回答"],
    ["position", "顺位分（20 分）：你被提到时排得越靠前越好"],
    ["engine_cover", "引擎覆盖分（10 分）：几家 AI 都认识你"],
    ["depth", "提及深度分（10 分）：每次提到你的详细程度"],
  ];
  const rowHtml = rows.map(function (r) {
    const item = bd[r[0]];
    const val = item && item.score !== undefined ? item.score : "—";
    return '<div class="breakdown-row"><span class="bd-left">' + r[1] + '</span><span class="bd-right num">得 ' + esc(val) + " 分</span></div>";
  }).join("");

  area.innerHTML =
    '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
    '<span class="score-number" style="color:' + color + '">' + esc(score) + "</span>" +
    '<span class="score-sub">满分 100 ' + conceptBubble("0-100 分，代表 AI 回答里“认识你、提到你、夸你”的整体程度。分越高，AI 越容易主动提到你的品牌。") + "</span>" +
    '<span class="badge-score" style="color:' + color + ';background:' + color + "1A" + '">' + esc(band.text) + "</span>" +
    "</div>" +
    '<div class="score-sub mt-8">' + esc(scoreSentence(score)) + "</div>" +
    '<div class="expand-panel">' +
    '<a class="btn-text" id="score-expand-toggle">分数怎么算出来的 ▾</a>' +
    '<div class="expand-body" id="score-expand-body">' +
    rowHtml +
    '<div class="score-sub mt-8">满分 100 = 提到你 40 + 夸你 20 + 位置靠前 20 + 几家 AI 都认识你 10 + 经常提起你 10</div>' +
    "</div></div>";

  document.getElementById("score-expand-toggle").addEventListener("click", function () {
    const body = document.getElementById("score-expand-body");
    const show = !body.classList.contains("show");
    body.classList.toggle("show", show);
    this.textContent = show ? "分数怎么算出来的 ▴" : "分数怎么算出来的 ▾";
  });
}

function renderScoreEmpty(area) {
  area.appendChild(emptyState(
    "这个品牌还没有监测过",
    "去监测中心跑第一轮吧，跑完这里会显示它的评分和趋势。",
    "去监测中心",
    function () { location.href = "/static/monitor.html"; }
  ));
}

/* 预警区 */
function renderAlerts(area, overview) {
  const roundCount = overview.round_count || 0;
  const alerts = overview.unread_alerts || [];

  if (roundCount < 3) {
    area.innerHTML = '<div class="score-sub">数据还在积累中——再监测 ' + (3 - roundCount) + " 轮后，系统就能帮你盯着数据变化了</div>";
    return;
  }
  if (!alerts.length) {
    area.innerHTML = '<div class="score-sub">一切正常，AI 提到你的情况没有明显变化</div>';
    return;
  }
  area.innerHTML = "";
  alerts.forEach(function (a) {
    const banner = document.createElement("div");
    banner.className = "alert-banner";
    banner.innerHTML =
      '<div class="alert-text">' + esc(a.message) + "</div>" +
      '<button class="btn-text" data-id="' + a.id + '">知道了</button>';
    banner.querySelector("button").addEventListener("click", function () {
      apiPost("/api/alerts/" + a.id + "/read", {}).then(function () {
        banner.remove();
        const dot = document.getElementById("nav-dot");
        if (dot) dot.classList.remove("show");
        const rest = area.querySelectorAll(".alert-banner");
        if (!rest.length) {
          area.innerHTML = '<div class="score-sub">一切正常，AI 提到你的情况没有明显变化</div>';
        }
      }).catch(function () {});
    });
    area.appendChild(banner);
  });
}

/* 最近一轮摘要 */
function renderLastRound(area, lastRound) {
  if (!lastRound) {
    area.appendChild(emptyState(
      "还没有监测记录",
      "系统会自动把每次监测存下来，方便你回看 AI 每次怎么评价你",
      "去发起监测",
      function () { location.href = "/static/monitor.html"; }
    ));
    return;
  }

  const summary = lastRound.summary || {};
  const perEngine = summary.per_engine || {};
  const engineCount = Object.keys(perEngine).length || 0;
  const mentionRate = Math.round((lastRound.mention_rate || 0) * 100);
  const netSentiment = lastRound.net_sentiment;
  const taskStatus = lastRound.task_status || "";
  const isAbnormal = taskStatus === "cancelled" || taskStatus === "failed";
  let sentimentTextVal = "中性";
  if (netSentiment > 0.01) sentimentTextVal = "正向";
  if (netSentiment < -0.01) sentimentTextVal = "负面";
  const totalAnswers = lastRound.total_answers || 0;

  area.innerHTML =
    '<div class="row-time">' + esc(fmtClock(lastRound.created_at)) +
    (isAbnormal ? " " + statusTag(taskStatus) : "") +
    " · 问了 " + totalAnswers + " 个问题 · 用了 " + engineCount + " 家 AI</div>" +
    '<div class="mt-8">提及率 ' + conceptBubble("这一轮问了 100 个问题，AI 回答里提到你的占多少。比如 40% 就是 10 个回答里有 4 个提到你。") + ' <span class="num" style="font-weight:600">' + mentionRate + "%</span></div>" +
    '<div class="mt-8">情感倾向：<span class="num" style="font-weight:600">' + esc(sentimentTextVal) + "</span></div>" +
    '<div class="mt-8 score-sub" id="competitor-line">竞品对比：正在对比…</div>' +
    '<div class="mt-8"><a class="btn-text" href="/static/report.html?round=' + lastRound.id + '">查看完整报告 →</a></div>' +
    '<div class="mt-8" style="text-align:right">' +
    '<span class="score-number" style="color:' + scoreColor(lastRound.overall_score) + '">' + esc(lastRound.overall_score !== null && lastRound.overall_score !== undefined ? lastRound.overall_score : "—") + "</span>" +
    '<span class="score-sub">分</span></div>';

  geoApi("/api/report/competitor?round_id=" + lastRound.id).then(function (data) {
    const items = data.items || [];
    const mine = items.find(function (i) { return i.is_self; });
    const others = items
      .filter(function (i) { return !i.is_self && i.mention_count > 0; })
      .sort(function (a, b) { return (b.mention_count || 0) - (a.mention_count || 0); });
    const line = document.getElementById("competitor-line");
    if (!line) return;
    const shortName = function (n) {
      n = String(n || "");
      return n.length > 12 ? n.slice(0, 12) + "…" : n;
    };
    let text = "";
    if (mine) text = "你被提到 " + (mine.mention_count || 0) + " 次";
    const top = others.slice(0, 3);
    if (top.length) {
      text += (text ? "；竞品：" : "竞品：") +
        top.map(function (i) { return shortName(i.name) + " " + (i.mention_count || 0) + " 次"; }).join("、") +
        (others.length > 3 ? "，另 " + (others.length - 3) + " 家" : "");
    }
    line.textContent = "竞品对比：" + (text || "暂无可对比的数据");
  }).catch(function () {
    const line = document.getElementById("competitor-line");
    if (line) line.textContent = "竞品对比：暂无可对比的数据";
  });
}

/* 下次自动监测 */
function renderSchedule(area, schedule) {
  const enabled = !!schedule.enabled;
  const nextRun = schedule.next_run_time;
  let main = "";
  if (enabled) {
    main = '<div class="mt-8" style="font-size:20px;font-weight:700;color:var(--primary)">⏰ ' + esc(fmtNextRun(nextRun)) + "</div>" +
      '<div class="score-sub mt-8">定时监测已开启，每天自动帮你盯一次</div>';
  } else {
    main = '<div class="score-sub mt-8">还没设置定时监测，去设置页打开后，每天自动帮你盯一次</div>';
  }
  area.innerHTML = main +
    '<div class="score-sub mt-8">保持程序开着，才能每天自动监测</div>' +
    '<div class="mt-12"><a class="btn btn-secondary" href="/static/settings.html#schedule">去设置</a></div>';
}

function loadOverview() {
  geoApi("/api/overview").then(function (data) {
    const scoreArea = document.getElementById("score-area");
    const lastRoundArea = document.getElementById("last-round-area");
    const noData = data.score === null || data.score === undefined;

    if (noData) {
      renderScoreEmpty(scoreArea);
      /* 最近一轮摘要卡随评分区同源空态，不重复展示（03b 3.4） */
      lastRoundArea.innerHTML = "";
    } else {
      /* 分项与总分同源：直接展示后端 /api/overview 的 score_breakdown（读最新快照 breakdown） */
      renderScorePanel(scoreArea, data.score, data.score_breakdown);
    }

    renderAlerts(document.getElementById("alert-area"), data);
    if (!noData) renderLastRound(lastRoundArea, data.last_round);
  }).catch(function () {
    document.getElementById("score-area").innerHTML =
      '<div class="score-sub">页面加载没成功，请检查程序是否还在运行，然后刷新页面</div>';
  });

  geoApi("/api/schedule").then(function (s) {
    renderSchedule(document.getElementById("schedule-area"), s);
  }).catch(function () {
    geoApi("/api/overview").then(function (data) {
      renderSchedule(document.getElementById("schedule-area"), { enabled: !!data.next_run_time, next_run_time: data.next_run_time });
    }).catch(function () {});
  });
}

initNav("index");
bindConcepts(document);
loadOverview();
maybeShowGuide();
