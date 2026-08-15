/* ============================================================
   报告页（拆分 3/3）：初始化 repInit + 常规vs联网对比 + 本轮引擎明细
   拆分自原 report.js（1100 行单文件），全局函数名与状态名保持不变。
   依赖先加载的 report-trend.js / report-deep.js。
   ============================================================ */

function repInit() {
  const param = new URLSearchParams(location.search);
  const initRound = param.get("round");

  document.querySelectorAll(".tabs .tab-item").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".tabs .tab-item").forEach(function (t) {
        t.classList.remove("active");
      });
      tab.classList.add("active");
      repMetric = tab.getAttribute("data-metric");
      loadTrend();
    });
  });

  document.getElementById("round-detail-toggle").addEventListener("click", function () {
    const body = document.getElementById("round-detail-body");
    body.classList.toggle("show");
    this.textContent = body.classList.contains("show")
      ? "▾ 查看本轮引擎明细" : "▸ 查看本轮引擎明细";
    if (body.classList.contains("show") && repRoundId) {
      loadRoundDetail(repRoundId);
    }
  });

  geoApi("/api/overview").then(function (data) {
    repRoundCount = data.round_count || 0;
    loadTrend();
    loadRoundsSelect(initRound);
  }).catch(function () {
    loadTrend();
    loadRoundsSelect(initRound);
  });
}

/* ---------------- 常规 vs 联网 对比（固定近 30 轮汇总口径） ---------------- */

function loadCompareCard() {
  /* 固定口径：只显示近 30 轮汇总，不随所选轮次切换 */
  loadCompareRange30();
}

/* 近 30 轮汇总对比（各模式平均提及率 + 结论；不显示提及次数明细） */
function loadCompareRange30() {
  var card = document.getElementById("compare-card");
  var body = document.getElementById("compare-body");
  repTrendRounds(30, function (normal, web) {
    if (!normal.length && !web.length) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    body.innerHTML = "";
    var nRate = 0, wRate = 0;
    normal.forEach(function (r) { nRate += (r.mention_rate || 0); });
    web.forEach(function (r) { wRate += (r.mention_rate || 0); });
    if (normal.length) nRate = nRate / normal.length;
    if (web.length) wRate = wRate / web.length;
    var conclusion = "近 30 轮内，联网提问" +
      (web.length ? "平均提及率 " + repPct(wRate) : "还没有数据") +
      "，" + (normal.length ? "常规提问平均提及率 " + repPct(nRate) : "常规还没有数据") +
      "。" + ((wRate || 0) > (nRate || 0)
        ? "联网下 AI 更容易提到你：新内容能被搜到，建议继续做内容积累。"
        : "两者差不多：优化目前主要停留在「被记住」层面，建议多在官网、行业媒体发内容。");

    body.innerHTML =
      '<div class="compare-grid">' +
      '<div class="cmp-box"><div class="cmp-title">常规提问（近 30 轮平均）</div>' +
      '<div class="cmp-num" style="color:#2563EB">' + (normal.length ? repPct(nRate) : "—") + "</div>" +
      '<div class="cmp-desc">共 ' + normal.length + " 轮 · AI 凭记忆回答时提到你的平均比例</div></div>" +
      '<div class="cmp-box"><div class="cmp-title">联网提问（近 30 轮平均）</div>' +
      '<div class="cmp-num" style="color:#16A34A">' + (web.length ? repPct(wRate) : "—") + "</div>" +
      '<div class="cmp-desc">共 ' + web.length + " 轮 · AI 先上网搜再回答时提到你的平均比例</div></div>" +
      "</div>" +
      '<div class="cmp-conclusion">' + esc(conclusion) + "</div>";
  });
}

function repPct(rate) {
  if (rate === null || rate === undefined) return "—";
  return Math.round(rate * 100) + "%";
}

/* ---------------- 卡片B：竞品深度分析（03b 7.2，N11-N14） ---------------- */

function loadRoundDetail(roundId) {
  const body = document.getElementById("round-detail-body");
  geoApi("/api/monitor/rounds/" + roundId).then(function (data) {
    const results = data.results || [];
    const notes = data.notes || [];

    const byEngine = {};
    results.forEach(function (r) {
      const key = r.engine_code;
      if (!byEngine[key]) {
        byEngine[key] = {
          name: r.display_name || key,
          answered: 0,
          mentioned: 0,
          pos: 0, neu: 0, neg: 0,
          firstPos: null,
          hasYuanbao: key === "yuanbao",
          models: {},
          answers: [],
          fails: [],
        };
      }
      const e = byEngine[key];
      if (r.model) e.models[r.model] = true;
      if (r.answer_text) {
        e.answered += 1;
        if (r.is_mentioned) {
          e.mentioned += 1;
          if (r.mention_position && (e.firstPos === null || r.mention_position < e.firstPos)) {
            e.firstPos = r.mention_position;
          }
        }
        if (r.sentiment === "positive") e.pos += 1;
        else if (r.sentiment === "negative") e.neg += 1;
        else e.neu += 1;
        e.answers.push(r);
      } else if (r.error_msg) {
        /* 调用失败（如模型不存在/钥匙失效）：记录原因，报告页明示 */
        e.fails.push(r);
      }
    });

    let html = "";
    Object.keys(byEngine).forEach(function (key) {
      const e = byEngine[key];
      const modelCount = Object.keys(e.models).length;
      html +=
        '<div class="detail-item">' +
        '<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">' +
        '<span style="font-weight:600">' + esc(e.name) + "</span>" +
        '<span class="num">回答 ' + e.answered + " 条 · 提到你 " + e.mentioned + " 次" +
        " · 首提位置 " + (e.firstPos ? "第 " + e.firstPos + " 位" : "—") +
        (modelCount > 1 ? " · " + modelCount + " 个模型" : "") + "</span>" +
        "</div>" +
        '<div class="mt-8">情感分布：' +
        '<span class="tag tag-green">正面 ' + e.pos + "</span> " +
        '<span class="tag tag-gray">中性 ' + e.neu + "</span> " +
        '<span class="tag tag-red">负面 ' + e.neg + "</span>" +
        "</div>" +
        (e.hasYuanbao
          ? '<div class="small-note mt-8">' + esc(YUANBAO_NOTE) + "</div>"
          : "") +
        /* 逐题回答原文（含未提及的回答，便于排查"为什么没提到你"） */
        (e.answers.length
          ? '<div class="mt-8">' + e.answers.map(function (r, i) {
              const mentionedTag = r.is_mentioned
                ? '<span class="tag tag-green">提到你</span>'
                : '<span class="tag tag-gray">未提及</span>';
            const sentiTag = r.sentiment === "positive"
                ? '<span class="tag tag-green">正面</span>'
                : r.sentiment === "negative"
                  ? '<span class="tag tag-red">负面</span>'
                  : '<span class="tag tag-gray">中性</span>';
              const modelTag = r.model
                ? '<span class="tag tag-blue">' + esc(r.model) + "</span>"
                : "";
              const text = String(r.answer_text || "").trim();
              const excerpt = text.length > 100 ? text.slice(0, 100) + "…" : text;
              const fullHtml = text.length > 100
                ? '<button type="button" class="btn-text deep-full-toggle mt-4" data-act="full-toggle">展开完整回答 ↓</button>' +
                  '<div class="hit-full hidden md-body">' + mdToHtml(text) + "</div>"
                : '<div class="hit-full md-body">' + mdToHtml(text) + "</div>";
              return '<div class="hit-item">' +
                '<div class="hit-head">第 ' + (i + 1) + " 问 · " + mentionedTag + " " + sentiTag + " " + modelTag + "</div>" +
                '<div class="hit-question">问：' + esc(r.question_text || "") + "</div>" +
                (text.length > 100
                  ? '<div class="hit-excerpt">' + esc(excerpt) + "</div>"
                  : "") +
                fullHtml +
                "</div>";
            }).join("") + "</div>"
          : "") +
        /* 调用失败记录（模型不存在/钥匙失效等）：报告页明示原因，不再静默 */
        (e.fails.length
          ? '<div class="mt-8">' + e.fails.map(function (r) {
              return '<div class="hit-item">' +
                '<div class="hit-head"><span class="tag tag-red">调用失败</span></div>' +
                '<div class="hit-question">问：' + esc(r.question_text || "") + "</div>" +
                '<div class="small-note" style="color:var(--alert-text);line-height:1.8">' +
                esc(r.error_msg || "这家 AI 没回答") + "</div>" +
                "</div>";
            }).join("") + "</div>"
          : "") +
        "</div>";
    });

    if (notes && notes.length) {
      html += '<div class="small-note mt-8">' + esc(notes.join(" ")) + "</div>";
    }
    if (!results.length) {
      html = '<div class="score-sub">这轮还没有可展示的明细</div>';
    }
    body.innerHTML = html;
    bindFullToggles(body);
  }).catch(function () {
    body.innerHTML = '<div class="score-sub">明细加载失败，请稍后再试</div>';
  });
}

initNav("report");
bindConcepts(document);
repInit();
window.addEventListener("resize", function () {
  if (trendChart) trendChart.resize();
  if (deepTrendChart) deepTrendChart.resize();
});
