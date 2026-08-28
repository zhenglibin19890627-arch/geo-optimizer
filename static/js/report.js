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
    loadEngineCompare();
  }).catch(function () {
    loadTrend();
    loadRoundsSelect(initRound);
    loadEngineCompare();
  });
}

/* ---------------- 各引擎厂商对比（2026-08-22，近 30 轮固定口径，表格） ---------------- */

function engineCmpPct(v) {
  return v === null || v === undefined ? "—" : Math.round(v * 100) + "%";
}

function engineCmpSenti(v) {
  if (v === null || v === undefined) return "—";
  return (v > 0 ? "+" : "") + Math.round(v * 100) + "%";
}

function loadEngineCompare() {
  const card = document.getElementById("engine-compare-card");
  const body = document.getElementById("engine-compare-body");
  if (!card || !body) return;
  geoApi("/api/report/engines?rounds=30").then(function (data) {
    const engines = data.engines || [];
    if (!engines.length) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    let html = '<div class="small-note mb-8">统计范围：近 ' + (data.rounds_used || 0) +
      " 个正常完成的轮次（已排除已停止/未成功）。提及率 = 提及你的回答数 ÷ 该厂商总回答数。</div>";
    html += '<div class="table-wrap"><table class="ec-grid">' +
      "<thead><tr>" +
      "<th>厂商</th>" +
      "<th>综合提及率</th>" +
      "<th>提及/回答</th>" +
      "<th>常规提及率</th>" +
      "<th>常规回答</th>" +
      "<th>联网提及率</th>" +
      "<th>联网回答</th>" +
      "<th>联网带信源</th>" +
      "<th>净情感</th>" +
      "<th>平均顺位</th>" +
      "</tr></thead><tbody>";
    engines.forEach(function (e) {
      const n = e.modes.normal || {};
      const w = e.modes.web || {};
      html +=
        "<tr>" +
        '<td style="font-weight:600">' + esc(e.display_name) + "</td>" +
        '<td class="num ec-hi">' + engineCmpPct(e.mention_rate) + "</td>" +
        "<td>" + e.mentioned + "/" + e.answered + "</td>" +
        "<td>" + engineCmpPct(n.mention_rate) + "</td>" +
        "<td>" + (n.answered || 0) + "</td>" +
        "<td>" + engineCmpPct(w.mention_rate) + "</td>" +
        "<td>" + (w.answered || 0) + "</td>" +
        "<td>" + (w.answered ? (w.with_sources || 0) : "—") + "</td>" +
        "<td>" + esc(engineCmpSenti(e.net_sentiment)) + "</td>" +
        "<td>" + (e.avg_position ? "第 " + e.avg_position + " 位" : "—") + "</td>" +
        "</tr>";
    });
    html += "</tbody></table></div>";
    body.innerHTML = html;
  }).catch(function () {
    card.classList.add("hidden");
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
      (web.length ? "平均提及率 " + repPct(wRate) : "暂无数据") +
      "，" + (normal.length ? "常规提问平均提及率 " + repPct(nRate) : "常规暂无数据") +
      "。" + ((wRate || 0) > (nRate || 0)
        ? "联网模式提及率更高：新内容可被检索到，建议持续积累内容。"
        : "两种模式相当：当前优化主要停留在「被记忆」层面，建议在官网、行业媒体持续发布内容。");

    body.innerHTML =
      '<div class="compare-grid">' +
      '<div class="cmp-box"><div class="cmp-title">常规提问（近 30 轮平均）</div>' +
      '<div class="cmp-num" style="color:#2563EB">' + (normal.length ? repPct(nRate) : "—") + "</div>" +
      '<div class="cmp-desc">共 ' + normal.length + " 轮 · AI 基于已有知识回答时的平均提及率</div></div>" +
      '<div class="cmp-box"><div class="cmp-title">联网提问（近 30 轮平均）</div>' +
      '<div class="cmp-num" style="color:#16A34A">' + (web.length ? repPct(wRate) : "—") + "</div>" +
      '<div class="cmp-desc">共 ' + web.length + " 轮 · AI 联网检索后回答时的平均提及率</div></div>" +
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
    const isWebRound = data.summary && data.summary.mode === "web";

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
          rows: [],
          withSources: 0,
        };
      }
      const e = byEngine[key];
      if (r.model) e.models[r.model] = true;
      if (r.answer_text) {
        e.answered += 1;
        if ((r.sources || []).length) e.withSources += 1;
        if (r.is_mentioned) {
          e.mentioned += 1;
          if (r.mention_position && (e.firstPos === null || r.mention_position < e.firstPos)) {
            e.firstPos = r.mention_position;
          }
        }
        if (r.sentiment === "positive") e.pos += 1;
        else if (r.sentiment === "negative") e.neg += 1;
        else e.neu += 1;
      }
      /* 成功与失败统一进时间线（按落库顺序），失败条目含模型与原因 */
      e.rows.push(r);
    });

    let html = "";
    Object.keys(byEngine).forEach(function (key) {
      const e = byEngine[key];
      const modelCount = Object.keys(e.models).length;
      /* 联网轮信源核查（2026-08-21）：回答成功但一条引用信源都没有 → 明示。
         元宝是已知口径（TokenHub 接口不返回来源，需配腾讯云联网搜索凭据），
         其余按「平台本轮未附带搜索引用」提示 */
      let srcWarn = "";
      if (isWebRound && e.answered > 0 && e.withSources === 0) {
        srcWarn = e.hasYuanbao
          ? "联网回答成功，但未获取到引用信源：腾讯元宝走 TokenHub 接口不返回搜索来源，" +
            "需要在 config/config.yaml 的 yuanbao 节填上腾讯云「联网搜索API」凭据" +
            "（wsa_secret_id / wsa_secret_key）后，联网档才会附带信源"
          : "联网回答成功，但本轮未返回引用信源（平台未附带搜索引用），信源统计不含这家";
      }
      html +=
        '<div class="detail-item">' +
        '<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">' +
        '<span style="font-weight:600">' + esc(e.name) + "</span>" +
        '<span class="num">回答 ' + e.answered + " 条 · 提及 " + e.mentioned + " 次" +
        " · 首次提及顺位 " + (e.firstPos ? "第 " + e.firstPos + " 位" : "—") +
        (modelCount > 1 ? " · " + modelCount + " 个模型" : "") + "</span>" +
        "</div>" +
        '<div class="mt-8">情感分布：' +
        '<span class="tag tag-green">正面 ' + e.pos + "</span> " +
        '<span class="tag tag-gray">中性 ' + e.neu + "</span> " +
        '<span class="tag tag-red">负面 ' + e.neg + "</span> " +
        (isWebRound
          ? '<span class="tag ' + (e.withSources > 0 ? "tag-green" : "tag-orange") + '">' +
            "信源 " + e.withSources + "/" + e.answered + "</span>"
          : "") +
        "</div>" +
        (srcWarn
          ? '<div class="small-note mt-8" style="color:var(--alert-text)">' +
            "⚠ " + esc(srcWarn) + "</div>"
          : "") +
        (e.hasYuanbao
          ? '<div class="small-note mt-8">' + esc(YUANBAO_NOTE) + "</div>"
          : "") +
        /* 逐条时间线（成功+失败统一，2026-08-22）：失败的与成功的同格式——
           第 N 问 · 状态标签 · 模型标签，失败原因紧跟其后 */
        (e.rows.length
          ? '<div class="mt-8">' + e.rows.map(function (r, i) {
              const no = "第 " + (i + 1) + " 问 · ";
              const modelTag = r.model
                ? '<span class="tag tag-blue">' + esc(r.model) + "</span>"
                : "";
              if (!r.answer_text) {
                return '<div class="hit-item">' +
                  '<div class="hit-head">' + no +
                  '<span class="tag tag-red">调用失败</span> ' + modelTag + "</div>" +
                  '<div class="hit-question">问：' + esc(r.question_text || "") + "</div>" +
                  '<div class="small-note" style="color:var(--alert-text);line-height:1.8">' +
                  "失败原因：" + esc(r.error_msg || "该 AI 未返回回答") + "</div>" +
                  "</div>";
              }
              const mentionedTag = r.is_mentioned
                ? '<span class="tag tag-green">提及</span>'
                : '<span class="tag tag-gray">未提及</span>';
            const sentiTag = r.sentiment === "positive"
                ? '<span class="tag tag-green">正面</span>'
                : r.sentiment === "negative"
                  ? '<span class="tag tag-red">负面</span>'
                  : '<span class="tag tag-gray">中性</span>';
              const text = String(r.answer_text || "").trim();
              const excerpt = text.length > 100 ? text.slice(0, 100) + "…" : text;
              const fullHtml = text.length > 100
                ? '<button type="button" class="btn-text deep-full-toggle mt-4" data-act="full-toggle">展开完整回答 ↓</button>' +
                  '<div class="hit-full hidden md-body">' + mdToHtml(text) + "</div>"
                : '<div class="hit-full md-body">' + mdToHtml(text) + "</div>";
              return '<div class="hit-item">' +
                '<div class="hit-head">' + no + mentionedTag + " " + sentiTag + " " + modelTag + "</div>" +
                '<div class="hit-question">问：' + esc(r.question_text || "") + "</div>" +
                (text.length > 100
                  ? '<div class="hit-excerpt">' + esc(excerpt) + "</div>"
                  : "") +
                fullHtml +
                "</div>";
            }).join("") + "</div>"
          : "") +
        "</div>";
    });

    if (notes && notes.length) {
      html += '<div class="small-note mt-8">' + esc(notes.join(" ")) + "</div>";
    }
    if (!results.length) {
      html = '<div class="score-sub">本轮暂无明细数据</div>';
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
