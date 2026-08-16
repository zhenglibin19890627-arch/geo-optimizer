/* ============================================================
   报告页（拆分 2/3）：竞品深度分析（费用说明/统计表/趋势/分析生成/提及弹窗）
   拆分自原 report.js，全局函数名与状态名保持不变。
   ============================================================ */

function loadDeepCard() {
  var card = document.getElementById("deep-card");
  var base = repSelectedRound() || repLatestNormalRound();
  if (!base && repRoundId) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  initDeepFeeNote();
  if (!repRoundId) {
    /* 最近 30 轮汇总：统计表聚合 30 轮；LLM 深度分析按 30 轮数据汇总分析（2026-08-15） */
    loadDeepStat(null);
    loadDeepAnalysis(null);
    return;
  }
  loadDeepStat(base.id);
  loadDeepAnalysis(base.id);
}

let feeNoteBound = false;

function initDeepFeeNote() {
  var note = document.getElementById("deep-fee-note");
  if (!note) return;
  if (localStorage.getItem(FEE_NOTE_KEY)) {
    note.classList.add("hidden");
    return;
  }
  note.classList.remove("hidden");
  if (feeNoteBound) return;
  feeNoteBound = true;
  document.getElementById("deep-fee-close").addEventListener("click", function () {
    localStorage.setItem(FEE_NOTE_KEY, "1");
    note.classList.add("hidden");
  });
}

/* ① 本轮对比统计（N11） */
function loadDeepStat(roundId) {
  var sec = document.getElementById("deep-stat-section");
  var guide = document.getElementById("deep-guide");
  var anaSec = document.getElementById("deep-analysis-section");
  var isRange = !roundId;  // 最近 30 轮汇总模式
  var url = roundId
    ? "/api/report/competitor/detail?round_id=" + roundId
    : "/api/report/competitor/detail";
  geoApi(url).then(function (data) {
    deepRoundTime = data.round_time || "";
    var items = data.items || [];
    if (!items.length) {
      /* 本轮回答里没有提取到其他品牌：整卡替换为空状态（2026-08-15 竞品全部自动提取） */
      guide.classList.remove("hidden");
      guide.innerHTML = "";
      guide.appendChild(emptyState(
        "",
        "本轮 AI 回答中未提取到其他品牌（竞品），暂无可对比数据。",
        "",
        null
      ));
      sec.innerHTML = "";
      anaSec.classList.add("hidden");
      return;
    }
    guide.classList.add("hidden");
    anaSec.classList.remove("hidden");
    renderDeepTable(items, roundId, isRange);
  }).catch(function () {
    sec.innerHTML = "";
    sec.appendChild(emptyState("", "未收集到有效回答，无统计数据。", "", null));
  });
}

function renderDeepTable(items, roundId, isRange) {
  /* 数据缺失（轮次无回答）→ 空状态；否则正常表格 */
  var allZero = items.every(function (it) {
    return !(it.mention_count || 0) && !(it.mentioned_answers || 0);
  });
  if (allZero) {
    if (isRange) {
      buildDeepTable(items, roundId, isRange);
      return;
    }
    geoApi("/api/monitor/rounds/" + roundId).then(function (d) {
      var answered = (d.results || []).filter(function (r) { return r.answer_text; }).length;
      if (answered === 0) {
        var sec = document.getElementById("deep-stat-section");
        sec.innerHTML = "";
        sec.appendChild(emptyState("", "本轮未收集到有效回答，无统计数据。", "", null));
      } else {
        buildDeepTable(items, roundId, isRange);
      }
    }).catch(function () {
      buildDeepTable(items, roundId, isRange);
    });
    return;
  }
  buildDeepTable(items, roundId, isRange);
}

function buildDeepTable(items, roundId, isRange) {
  var sec = document.getElementById("deep-stat-section");
  var selfName = "";
  items.forEach(function (it) { if (it.is_self) selfName = it.name; });
  deepSelfName = selfName;
  var rangeTitle = isRange ? "近 30 轮累计对比统计" : "本轮对比统计";

  /* 排序：自己品牌第一，竞品按被提到次数降序 */
  var sorted = items.slice().sort(function (a, b) {
    if (a.is_self) return -1;
    if (b.is_self) return 1;
    return (b.mention_count || 0) - (a.mention_count || 0);
  });
  /* 展示：自己品牌 + 前三竞品；其余竞品折叠（展开可见） */
  var shown = sorted.filter(function (it) { return it.is_self; })
    .concat(sorted.filter(function (it) { return !it.is_self; }).slice(0, 3));
  var rest = sorted.filter(function (it) { return !it.is_self; }).slice(3);

  function rowHtml(it) {
    var rowCls = it.is_self ? ' class="deep-self-row"' : "";
    var nameCell = it.is_self
      ? '<span class="deep-self-name">你（' + esc(it.name) + "）</span>"
      : esc(it.name);
    var countCell;
    var answersCell;
    var posCell;
    if (it.is_self) {
      countCell = (it.mention_count || 0) + " 次";
      answersCell = it.mentioned_answers || 0;
      posCell = it.avg_first_position !== null && it.avg_first_position !== undefined
        ? "第 " + it.avg_first_position + " 位" : "—";
    } else if (it.mention_count || it.mentioned_answers) {
      countCell = (it.mention_count || 0) + " 次";
      answersCell = it.mentioned_answers || 0;
      posCell = it.avg_first_position !== null && it.avg_first_position !== undefined
        ? "第 " + it.avg_first_position + " 位" : "—";
    } else {
      countCell = '<span class="deep-dim">本轮未提及</span>';
      answersCell = "—";
      posCell = "—";
    }
    var linkCell;
    if (isRange) {
      /* 30 轮汇总模式下明细是跨轮累计，看单条明细请选具体轮次 */
      linkCell = '<span class="small-note">明细见单轮</span>';
    } else {
      linkCell = it.is_self
        ? '<a class="btn-text deep-link" data-name="' + esc(it.name) + '" data-self="1">看明细</a>'
        : '<a class="btn-text deep-link" data-name="' + esc(it.name) + '">看明细</a>';
    }
    return "<tr" + rowCls + ">" +
      "<td>" + nameCell + "</td>" +
      "<td>" + countCell + "</td>" +
      "<td>" + answersCell + "</td>" +
      "<td>" + posCell + "</td>" +
      "<td>" + linkCell + "</td>" +
      "</tr>";
  }

  var html = "";
  html += '<div class="deep-sec-title" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
    "<span>" + esc(rangeTitle) + "</span>" +
    (rest.length
      ? '<button type="button" class="btn-text deep-more-toggle" data-act="deep-more">' +
        "展开查看全部 " + rest.length + " 个竞品 ↓</button>"
      : "") +
    "</div>" +
    /* 单表双 tbody：主表 + 折叠行共用同一列宽，展开后列保持对齐 */
    '<table class="table deep-table"><thead><tr>' +
    "<th>品牌/竞品</th><th>提及次数</th><th>提及的回答数</th>" +
    "<th>平均首次提及顺位</th><th></th></tr></thead>" +
    "<tbody>" + shown.map(rowHtml).join("") + "</tbody>" +
    (rest.length
      ? '<tbody class="deep-more hidden">' + rest.map(rowHtml).join("") + "</tbody>"
      : "") +
    "</table>";

  sec.innerHTML = html;
  sec.querySelectorAll(".deep-link").forEach(function (a) {
    a.addEventListener("click", function () {
      openMentionModal(roundId, a.getAttribute("data-name"), a.getAttribute("data-self") === "1");
    });
  });
  var moreBtn = sec.querySelector('[data-act="deep-more"]');
  if (moreBtn) {
    moreBtn.addEventListener("click", function () {
      var box = sec.querySelector(".deep-more");
      var open = box.classList.toggle("hidden");
      moreBtn.textContent = open
        ? "收起额外竞品 ↑"
        : "展开查看全部 " + rest.length + " 个竞品 ↓";
    });
  }
}

/* ② 近 30 轮提及趋势已合并进「历史趋势」卡的「竞品提及」Tab（2026-08-15），
   绘制逻辑见 report-trend.js loadTrendCompetitor */

/* ③④⑤ 分析状态分区渲染（N14，每 10 秒轮询） */
function loadDeepAnalysis(roundId) {
  if (deepAnalysisTimer) { clearTimeout(deepAnalysisTimer); deepAnalysisTimer = null; }
  deepAnalysisRoundId = roundId;
  fetchDeepAnalysis(roundId);
}

function fetchDeepAnalysis(roundId) {
  if (deepAnalysisRoundId !== roundId) return;
  const url = roundId
    ? "/api/report/competitor/analysis?round_id=" + roundId
    : "/api/report/competitor/analysis";
  geoApi(url).then(function (res) {
    if (deepAnalysisRoundId !== roundId) return;
    var status = res.status || "unavailable";
    var sec = document.getElementById("deep-analysis-section");
    if (status === "pending" || status === "running") {
      sec.innerHTML = '<div class="deep-placeholder">分析进行中，请稍候。</div>';
      deepAnalysisTimer = setTimeout(function () { fetchDeepAnalysis(roundId); }, 10000);
      return;
    }
    if (status === "failed") {
      var msg = "分析失败：本次分析未成功，请稍后重试。统计内容不受影响。";
      if (res.error_msg) msg += "（" + res.error_msg + "）";
      sec.innerHTML = '<div class="deep-placeholder">' + esc(msg) + "</div>";
      return;
    }
    if (status === "unavailable") {
      sec.innerHTML =
        '<div class="deep-placeholder">暂时无法分析：尚未填写分析用 API 钥匙。请在设置页填写，之后的新一轮监测会自动生成分析。' +
        ' <a class="deep-link-go" href="/static/settings.html#keys">去设置页</a></div>';
      return;
    }
    if (status === "none") {
      sec.innerHTML =
        '<div class="deep-placeholder">' + (roundId ? "这一轮" : "近 30 轮") +
        " AI 回答中未提及其他品牌（竞品），无可分析内容。统计与趋势不受影响。</div>";
      return;
    }
    renderDeepAnalysisDone(sec, res.data || {});
  }).catch(function () {
    if (deepAnalysisRoundId !== roundId) return;
    var sec = document.getElementById("deep-analysis-section");
    sec.innerHTML =
      '<div class="deep-placeholder">分析失败：本次分析未成功，请稍后重试。统计内容不受影响。</div>';
  });
}

function renderDeepAnalysisDone(sec, data) {
  var competitors = data.competitors || [];
  var advice = data.advice || [];
  var html = "";

  if (data.range === "30") {
    html += '<div class="deep-sec-title" style="margin-bottom:8px">近 ' +
      (data.rounds || 30) + " 轮汇总分析</div>";
  }
  if (data.truncated) {
    html += '<div class="small-note" style="margin-bottom:8px">' +
      (data.range === "30" ? "近 " + (data.rounds || 30) + " 轮" : "本轮") + "共提取 " +
      (data.total || competitors.length) +
      " 家竞品，深度分析聚焦提及次数最多的前 5 家；全部竞品见上方统计表。</div>";
  }

  competitors.forEach(function (c) {
    var name = c.name || "";
    if (c.mentioned) {
      html += '<div class="reason-card">' +
        '<div class="reason-title">被提及原因——「' + esc(name) + "」</div>" +
        '<div class="reason-text">' + esc(c.summary || "") + "</div>";
      var st = c.source_types || [];
      if (st.length) {
        html += '<div class="spec-row mt-8">推测信源类型：' +
          st.map(function (t) { return '<span class="spec-chip">' + esc(t) + "</span>"; }).join("") +
          "</div>" +
          '<div class="small-note mt-4">' + esc(DEEP_SPEC_NOTE) + "</div>";
      }
      var ev = c.evidence || [];
      html += '<a class="btn-text deep-ev-toggle mt-8" data-count="' + ev.length + '">看证据（' + ev.length + " 条）</a>";
      if (ev.length) {
        html += '<div class="evidence-body hidden">' + ev.map(function (e) {
          var full = e.answer_full ? String(e.answer_full).trim() : "";
          return '<div class="hit-item">' +
            '<div class="hit-head">' +
            (e.round_label ? esc(e.round_label) + " · " : "") +
            esc(e.engine_code || "") + "</div>" +
            '<div class="hit-question">' + esc(e.question_text || "") + "</div>" +
            '<div class="hit-excerpt">' + highlightWord(e.excerpt || e.answer_excerpt || "", name) + "</div>" +
            (full
              ? '<button type="button" class="btn-text deep-full-toggle mt-4" data-act="full-toggle">展开完整回答 ↓</button>' +
                '<div class="hit-full hidden">' + highlightWord(full, name) + "</div>"
              : "") +
            "</div>";
        }).join("") + "</div>";
      }
      html += "</div>";
    } else {
      html += '<div class="reason-card reason-dim">「' + esc(name) + "」" +
        (data.range === "30" ? "近 30 轮 AI 回答中未提及" : "本轮 AI 回答中未提及") + "</div>";
    }
  });

  if (advice.length) {
    html += '<div class="deep-advice-head mt-16">下一步优化方向</div>' +
      '<div class="advice-stack">' +
      advice.map(function (a) {
        return '<div class="advice-card">' +
          '<div class="advice-line"><b>差距分析：</b>' + esc(a.gap || "") + "</div>" +
          '<div class="advice-line"><b>优化渠道：</b>' + esc(a.where || "") + "</div>" +
          '<div class="advice-line"><b>具体动作：</b>' + esc(a.what || "") + "</div>" +
          "</div>";
      }).join("") +
      "</div>";
  }

  sec.innerHTML = html;
  bindFullToggles(sec);
  sec.querySelectorAll(".deep-ev-toggle").forEach(function (a) {
    a.addEventListener("click", function () {
      var body = a.nextElementSibling;
      if (body && body.classList.contains("evidence-body")) {
        body.classList.toggle("hidden");
      }
    });
  });
}

/* 命中明细弹层（N13） */
function escRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightWord(text, word) {
  var escaped = esc(text);
  if (!word) return escaped;
  try {
    var re = new RegExp(escRegExp(word), "gi");
    return escaped.replace(re, function (m) {
      return '<span class="hit-word">' + m + "</span>";
    });
  } catch (e) {
    return escaped;
  }
}

function fmtShortTime(str) {
  if (!str) return "";
  var m = String(str).match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})/);
  if (!m) return str;
  return parseInt(m[2], 10) + "月" + parseInt(m[3], 10) + "日 " + m[4] + ":" + m[5];
}

function openMentionModal(roundId, competitorName, isSelf) {
  geoApi("/api/report/competitor/mentions?round_id=" + roundId +
    "&competitor=" + encodeURIComponent(competitorName)).then(function (items) {
    var mask = document.createElement("div");
    mask.className = "modal-mask";
    var titleName = isSelf ? "你（" + competitorName + "）" : competitorName;
    var bodyHtml = "";
    if (!items || !items.length) {
      bodyHtml = '<div class="score-sub">本轮没有提及「' + esc(titleName) + "」的回答</div>";
    } else {
      bodyHtml = items.map(function (it) {
        var full = it.answer_full ? String(it.answer_full).trim() : "";
        return '<div class="hit-item">' +
          '<div class="hit-head">' + esc(it.engine_name || it.engine_code || "") +
          " · " + fmtShortTime(deepRoundTime) + "</div>" +
          '<div class="hit-question">' + esc(it.question_text || "") + "</div>" +
          '<div class="hit-excerpt">' + highlightWord(it.answer_excerpt || "", competitorName) + "</div>" +
          (full
            ? '<button type="button" class="btn-text deep-full-toggle mt-4" data-act="full-toggle">展开完整回答 ↓</button>' +
              '<div class="hit-full hidden md-body">' + mdToHtml(full) + "</div>"
            : "") +
          "</div>";
      }).join("");
    }
    mask.innerHTML =
      '<div class="modal modal-wide">' +
      '<div class="modal-head">哪些回答提到了「' + esc(titleName) + "」</div>" +
      '<div class="modal-body">' + bodyHtml + "</div>" +
      '<div class="modal-foot modal-foot-between">' +
      '<span class="small-note">这里是 AI 回答的原文片段，不是真实网页链接。</span>' +
      '<button class="btn btn-primary" data-act="close">关闭</button>' +
      "</div></div>";
    document.body.appendChild(mask);
    bindFullToggles(mask);
    function close() { mask.remove(); }
    mask.addEventListener("click", function (e) {
      if (e.target === mask) close();
    });
    mask.querySelector('[data-act="close"]').addEventListener("click", close);
  }).catch(function () {});
}

/* 展开/收起完整回答（07z 任务5）：answer_full 为空的历史记录无按钮，前端容错 */
function bindFullToggles(root) {
  root.querySelectorAll('[data-act="full-toggle"]').forEach(function (btn) {
    btn.addEventListener("click", function () {
      var box = btn.nextElementSibling;
      if (!box || !box.classList.contains("hit-full")) return;
      var open = box.classList.toggle("hidden");
      btn.textContent = open ? "展开完整回答 ↓" : "收起完整回答 ↑";
    });
  });
}

/* 报告页 markdown 渲染（XSS 安全：回答里的 HTML 一律转义为纯文本展示） */
function mdToHtml(text) {
  if (!text) return "";
  marked.use({ renderer: { html: function (html) { return esc(html); } } });
  return marked.parse(String(text));
}

/* ---------------- 引擎明细 ---------------- */

