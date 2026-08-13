/* ============================================================
   报告图表页（UI 文档 4.4）：ECharts 趋势三 Tab + 竞品对比 + 信源排行 + 引擎明细
   ============================================================ */

let repMetric = "score";
let repRoundId = null;
let repRoundCount = 0;
let trendChart = null;
let deepTrendChart = null;
let deepAnalysisTimer = null;
let deepAnalysisRoundId = null;
let repRoundsAll = [];
let deepSelfName = "";
let deepRoundTime = "";

const FEE_NOTE_KEY = "geo_deep_fee_note_closed";

const DEEP_SPEC_NOTE = "（系统推测，仅供参考，不是查到的真实链接）";

const YUANBAO_NOTE =
  "说明：元宝的数据来自它的同门底座「混元」官方服务，与元宝 App 上的实际回答可能有差异。";

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

/* ---------------- 轮次下拉 ---------------- */

function loadRoundsSelect(selectId) {
  geoApi("/api/monitor/rounds?page=1").then(function (data) {
    const sel = document.getElementById("report-round-select");
    const items = data.items || [];
    repRoundsAll = items;
    sel.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = "最近 30 轮（趋势图范围）";
    sel.appendChild(allOpt);

    items.forEach(function (r) {
      const o = document.createElement("option");
      o.value = String(r.id);
      const taskStatus = r.task_status || "";
      let suffix = "";
      if (taskStatus === "cancelled") {
        suffix = "（已停止）";
        o.style.color = "#9CA3AF";
      } else if (taskStatus === "failed") {
        suffix = "（未成功）";
        o.style.color = "#DC2626";
      }
      o.innerHTML = esc((r.created_at || "").slice(0, 16) + " · " + r.overall_score + " 分" + suffix);
      if (r.mode === "web") {
        o.innerHTML += ' <span style="color:#16A34A">（联网）</span>';
      }
      sel.appendChild(o);
    });

    sel.addEventListener("change", function () {
      repRoundId = sel.value ? parseInt(sel.value, 10) : null;
      repShowRoundHint();
      loadSources();
      loadCompareCard();
      loadDeepCard();
    });

    if (selectId) {
      sel.value = String(selectId);
      repRoundId = parseInt(selectId, 10);
      repShowRoundHint();
      loadSources();
      loadCompareCard();
      loadDeepCard();
    } else if (items.length) {
      repRoundId = items[0].id;
      repShowRoundHint();
      loadSources();
      loadCompareCard();
      loadDeepCard();
    } else {
      loadCompareCard();
      loadDeepCard();
    }
  }).catch(function () {});
}

/* 当前轮次提示（07z 任务3）：手动选轮次后回填选中的那一轮；选「最近30轮」显示总览提示 */
function repShowRoundHint() {
  const el = document.getElementById("report-round-time");
  if (!el) return;
  if (!repRoundId) {
    el.textContent = "查看全部轮次总览";
    return;
  }
  let r = null;
  for (let i = 0; i < repRoundsAll.length; i++) {
    if (repRoundsAll[i].id === repRoundId) { r = repRoundsAll[i]; break; }
  }
  el.textContent = "当前轮次：" + (r ? (r.created_at || "") : "");
}

/* ---------------- 趋势图 ---------------- */

function emptyChart(container, text) {
  container.innerHTML = "";
  const empty = emptyState("", text, "", null);
  empty.querySelector(".empty-icon").style.display = "none";
  container.appendChild(empty);
}

function fmtTrendDate(str) {
  if (!str) return "";
  const m = String(str).match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})/);
  if (!m) return str;
  return parseInt(m[2], 10) + "/" + parseInt(m[3], 10);
}

/* 趋势图 X 轴日期（07z 任务1）：分页取正常完成（task_status 空或 done）的常规/联网轮次，
   各取最近 count 轮，按轮次升序；与 M18 的 mode 过滤口径一致（已停止/未成功不计入） */
function repTrendRounds(count, cb) {
  const normal = [];
  const web = [];
  function page(n) {
    geoApi("/api/monitor/rounds?page=" + n).then(function (data) {
      (data.items || []).forEach(function (r) {
        const st = r.task_status || "";
        if (!(st === "" || st === "done")) return;
        if (!r.mode || r.mode === "normal") normal.push(r);
        else if (r.mode === "web") web.push(r);
      });
      const psize = data.page_size || 20;
      const hasMore = (data.items || []).length >= psize;
      const enough = normal.length >= count && web.length >= count;
      if (enough || !hasMore) {
        normal.sort(function (a, b) { return a.id - b.id; });
        web.sort(function (a, b) { return a.id - b.id; });
        cb(normal.slice(-count), web.slice(-count));
      } else {
        page(n + 1);
      }
    }).catch(function () { cb([], []); });
  }
  page(1);
}

function loadTrend() {
  const container = document.getElementById("trend-chart");
  if (repRoundCount < 2) {
    if (repRoundCount === 0) {
      /* 新品牌无数据：空状态引导（03b 3.4） */
      container.innerHTML = "";
      container.appendChild(emptyState(
        "这个品牌还没有数据",
        "去监测中心跑一轮吧",
        "去监测中心",
        function () { location.href = "/static/monitor.html"; }
      ));
      return;
    }
    emptyChart(container, "再监测 " + (2 - repRoundCount) + " 轮后，这里会画出 AI 对你的态度变化曲线");
    return;
  }
  /* M18 调两次：常规 + 联网，各画一条线（07z 任务1） */
  const url = "/api/report/trend?metric=" + repMetric + "&rounds=30&mode=";
  Promise.all([
    geoApi(url + "normal").catch(function () { return { values: [] }; }),
    geoApi(url + "web").catch(function () { return { values: [] }; }),
  ]).then(function (res) {
    const nValues = (res[0].values) || [];
    const wValues = (res[1].values) || [];
    if (!nValues.length && !wValues.length) {
      emptyChart(container, "再监测 " + (2 - repRoundCount) + " 轮后，这里会画出 AI 对你的态度变化曲线");
      return;
    }
    repTrendRounds(30, function (normalRounds, webRounds) {
      /* 两条线的日期合并去重后统一升序（某天只有一条线 → 只有一个点） */
      const dateMap = {};
      function addDate(d, ts) {
        if (!d) return;
        if (dateMap[d] === undefined || ts < dateMap[d]) dateMap[d] = ts;
      }
      normalRounds.forEach(function (r) { addDate(fmtTrendDate(r.created_at), r.created_at || ""); });
      webRounds.forEach(function (r) { addDate(fmtTrendDate(r.created_at), r.created_at || ""); });
      const axis = Object.keys(dateMap).sort(function (a, b) {
        return dateMap[a] < dateMap[b] ? -1 : (dateMap[a] > dateMap[b] ? 1 : 0);
      });
      const idxByDate = {};
      axis.forEach(function (d, i) { idxByDate[d] = i; });

      let axisLabels = axis;
      let nAligned;
      let wAligned;
      if (axis.length) {
        nAligned = alignTrendValues(nValues, normalRounds, axis.length, idxByDate);
        wAligned = alignTrendValues(wValues, webRounds, axis.length, idxByDate);
      } else {
        /* 轮次接口异常兜底：退回「第N轮」，两条线按各自轮数右对齐 */
        const total = Math.max(nValues.length, wValues.length);
        axisLabels = [];
        for (let i = 1; i <= total; i++) axisLabels.push("第" + i + "轮");
        nAligned = new Array(total).fill(null);
        wAligned = new Array(total).fill(null);
        nValues.forEach(function (v, i) {
          if (v !== null && v !== undefined) nAligned[total - nValues.length + i] = v;
        });
        wValues.forEach(function (v, i) {
          if (v !== null && v !== undefined) wAligned[total - wValues.length + i] = v;
        });
      }
      renderTrendChart(container, axisLabels, nAligned, wAligned);
    });
  }).catch(function () {});
}

/* 按日期把某一模式的值对齐到合并后的 X 轴；该天无该模式数据 → null */
function alignTrendValues(values, rounds, axisCount, idxByDate) {
  const arr = new Array(axisCount);
  for (let i = 0; i < axisCount; i++) arr[i] = null;
  rounds.forEach(function (r, i) {
    const idx = idxByDate[fmtTrendDate(r.created_at)];
    if (idx === undefined || idx === null) return;
    if (values[i] === null || values[i] === undefined) return;
    arr[idx] = values[i];
  });
  return arr;
}

function renderTrendChart(container, labels, nValues, wValues) {
  if (trendChart) trendChart.dispose();
  trendChart = echarts.init(container);
  const isScore = repMetric === "score";
  const isSentiment = repMetric === "sentiment";
  const hasWeb = (wValues || []).some(function (v) { return v !== null && v !== undefined; });

  function buildPoints(values, defaultColor) {
    return values.map(function (v) {
      if (v === null || v === undefined) return null;
      return { value: v, itemStyle: { color: isScore ? scoreColor(v) : defaultColor } };
    });
  }
  const normalPoints = buildPoints(nValues, "#2563EB");
  const webPoints = buildPoints(wValues, "#16A34A");

  /* 每条线的最后一个有值点：放大 + 标数值 */
  function markLast(points) {
    for (let i = points.length - 1; i >= 0; i--) {
      const p = points[i];
      if (p) {
        p.symbolSize = 10;
        p.label = {
          show: true,
          position: "top",
          color: isScore ? scoreColor(p.value) : p.itemStyle.color,
          fontWeight: 700,
          formatter: p.value + (isScore ? "" : "%"),
        };
        break;
      }
    }
  }
  markLast(normalPoints);
  if (hasWeb) markLast(webPoints);

  /* 常规轮蓝实线；联网轮绿虚线；无联网轮时只画蓝线、图例不出现绿线（07z 任务1） */
  const series = [{
    name: "常规提问",
    type: "line",
    data: normalPoints,
    symbol: "circle",
    symbolSize: 7,
    lineStyle: { width: 2.5, color: "#2563EB" },
    connectNulls: true,
  }];
  if (hasWeb) {
    series.push({
      name: "联网提问",
      type: "line",
      data: webPoints,
      symbol: "circle",
      symbolSize: 7,
      lineStyle: { width: 2, color: "#16A34A", type: "dashed" },
      connectNulls: true,
    });
  }

  const option = {
    tooltip: {
      trigger: "axis",
      formatter: function (params) {
        const lines = [params[0].axisValue];
        params.forEach(function (p) {
          if (!p || !p.data) return;
          const v = p.data.value;
          if (v === null || v === undefined) return;
          lines.push(p.seriesName + "：" + v + (isScore ? " 分" : "%"));
        });
        return lines.join("<br>");
      },
    },
    grid: { left: 50, right: 40, top: 40, bottom: 40 },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: "#E2E8F0" } },
      axisLabel: { color: "#6B7280" },
    },
    yAxis: {
      type: "value",
      min: isSentiment ? -100 : 0,
      max: 100,
      axisLabel: { color: "#6B7280", formatter: "{value}" + (isScore ? "" : "%") },
      splitLine: { lineStyle: { color: "#F1F5F9" } },
    },
    legend: hasWeb ? { data: ["常规提问", "联网提问"], top: 0 } : undefined,
    series: series,
  };
  trendChart.setOption(option);
}


/* ---------------- 引用信源 ---------------- */

function loadSources() {
  const area = document.getElementById("sources-area");
  if (repRoundCount < 2) {
    area.innerHTML = "";
    area.appendChild(emptyState(
      "",
      "再监测 " + (2 - repRoundCount) + " 轮后，这里会画出 AI 对你的态度变化曲线",
      "",
      null
    ));
    return;
  }
  const url = repRoundId ? "/api/report/sources?round_id=" + repRoundId : "/api/report/sources";
  geoApi(url).then(function (items) {
    area.innerHTML = "";
    if (!items || !items.length) {
      area.appendChild(emptyState(
        "还没有引用信源",
        repRoundId
          ? "这一轮 AI 回答里没有出现可识别的网站引用（豆包等部分引擎联网回答不返回引用链接，属平台限制）"
          : "近 30 轮 AI 回答里没有出现可识别的网站引用（豆包等部分引擎联网回答不返回引用链接，属平台限制）",
        "",
        null
      ));
      return;
    }
    const list = document.createElement("div");
    items.slice(0, 10).forEach(function (s, i) {
      const row = document.createElement("div");
      row.className = "list-row";
      const siteName = s.site_name || s.domain || s.url || "未知网站";
      const domain = s.domain ? s.url || "" : "";
      const engTags = (s.engines || []).map(function (e) {
        return '<span class="tag tag-gray" style="flex:none">' +
          esc(e.display_name || e.engine_code || "") + " ×" + (e.count || 0) + "</span>";
      }).join("");
      row.innerHTML =
        '<span class="num" style="color:var(--text-placeholder);width:24px;flex:none">' + (i + 1) + ".</span>" +
        '<div class="row-main" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
        esc(siteName) +
        (s.domain && s.site_name && s.site_name !== s.domain
          ? ' <span class="small-note">(' + esc(s.domain) + ")</span>"
          : "") +
        "</div>" +
        '<span class="num" style="color:var(--text-sub);flex:none">' + (s.count || 0) + " 次引用</span>" +
        engTags;
      list.appendChild(row);
    });
    area.appendChild(list);
  }).catch(function () {});
}

/* ---------------- 卡片A：常规 vs 联网 效果对比（03b 7.1，M15 + N11×2 前端组合） ---------------- */

function repNormalRounds() {
  /* 复用后端 round_is_normal 口径：任务缺失或 done 视为正常，cancelled/failed 排除 */
  return (repRoundsAll || []).filter(function (r) {
    var st = r.task_status || "";
    return st === "" || st === "done";
  });
}

function repLatestNormalRound() {
  var list = repNormalRounds();
  return list.length ? list[0] : null;
}

function repSelectedRound() {
  var sel = document.getElementById("report-round-select");
  if (!sel || !sel.value) return null;
  var id = parseInt(sel.value, 10);
  var all = repRoundsAll || [];
  for (var i = 0; i < all.length; i++) {
    if (all[i].id === id) return all[i];
  }
  return null;
}

/* 两模式各取最新正常轮；下拉选中了某模式的正常轮时，以该轮为准（03b 7.0） */
function repCompareSides() {
  var sel = repSelectedRound();
  var selOk = sel && ((sel.task_status || "") === "" || (sel.task_status || "") === "done");
  var normal = null;
  var web = null;
  if (selOk) {
    if ((sel.mode || "normal") === "normal") normal = sel;
    else web = sel;
  }
  repNormalRounds().forEach(function (r) {
    if (!normal && (r.mode || "normal") === "normal") normal = r;
    if (!web && r.mode === "web") web = r;
  });
  return { normal: normal, web: web };
}

function loadCompareCard() {
  var card = document.getElementById("compare-card");
  var body = document.getElementById("compare-body");
  if (!repRoundId) {
    loadCompareRange30();
    return;
  }
  var sides = repCompareSides();
  if (!sides.normal && !sides.web) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  body.innerHTML = "";
  if (sides.normal && !sides.web) {
    body.appendChild(emptyState(
      "",
      "还没有联网提问的监测。去监测中心发起一轮「联网提问」，这里就能对比了。",
      "去发起监测",
      function () { location.href = "/static/monitor.html"; }
    ));
    return;
  }
  if (!sides.normal && sides.web) {
    body.appendChild(emptyState(
      "",
      "还没有常规提问的监测。去监测中心发起一轮「常规提问」吧。",
      "去发起监测",
      function () { location.href = "/static/monitor.html"; }
    ));
    return;
  }
  renderCompareView(sides.normal, sides.web);
}

/* 最近 30 轮模式：常规 vs 联网 汇总对比（平均提及率 + 30 轮累计提到列表） */
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
      '<div class="cmp-conclusion">' + esc(conclusion) + "</div>" +
      '<div class="compare-grid">' +
      '<div class="cmp-list"><div class="cmp-list-title">常规提问近 30 轮累计提到：</div>' +
      '<div class="cmp-list-line" id="cmp-list-normal"><span class="score-sub">加载中…</span></div></div>' +
      '<div class="cmp-list"><div class="cmp-list-title">联网提问近 30 轮累计提到：</div>' +
      '<div class="cmp-list-line" id="cmp-list-web"><span class="score-sub">加载中…</span></div></div>' +
      "</div>";

    geoApi("/api/report/competitor/detail?mode=normal").then(function (d) {
      document.getElementById("cmp-list-normal").innerHTML = competitorListHtml(d.items || []);
    }).catch(function () {
      document.getElementById("cmp-list-normal").innerHTML = '<span class="score-sub">—</span>';
    });
    geoApi("/api/report/competitor/detail?mode=web").then(function (d) {
      document.getElementById("cmp-list-web").innerHTML = competitorListHtml(d.items || []);
    }).catch(function () {
      document.getElementById("cmp-list-web").innerHTML = '<span class="score-sub">—</span>';
    });
  });
}

function repPct(rate) {
  if (rate === null || rate === undefined) return "—";
  return Math.round(rate * 100) + "%";
}

function competitorListHtml(items) {
  var list = (items || []).filter(function (it) { return (it.mention_count || 0) > 0; });
  if (!list.length) return '<span class="score-sub">—</span>';
  return list.map(function (it) {
    var cls = it.is_self ? "cmp-me" : "cmp-them";
    return '<span class="' + cls + '">' + esc(it.name) + " " + (it.mention_count || 0) + " 次</span>";
  }).join('<span class="cmp-sep"> · </span>');
}

function renderCompareView(normalRound, webRound) {
  var body = document.getElementById("compare-body");
  var nRate = normalRound.mention_rate;
  var wRate = webRound.mention_rate;
  var conclusion = (wRate || 0) > (nRate || 0)
    ? "联网提问下 AI 更容易提到你：你新发的内容已经能被搜到，但 AI 记住的还不深，建议继续长期做内容积累。"
    : "两边差不多：优化目前主要停留在「被记住」的层面。想让用户在 AI 里搜到你，可以多在官网、行业媒体发内容。";

  body.innerHTML =
    '<div class="compare-grid">' +
    '<div class="cmp-box"><div class="cmp-title">常规提问</div>' +
    '<div class="cmp-num" style="color:#2563EB">' + repPct(nRate) + "</div>" +
    '<div class="cmp-desc">AI 凭记忆回答时提到你的比例</div></div>' +
    '<div class="cmp-box"><div class="cmp-title">联网提问</div>' +
    '<div class="cmp-num" style="color:#16A34A">' + repPct(wRate) + "</div>" +
    '<div class="cmp-desc">AI 先上网搜再回答时提到你的比例</div></div>' +
    "</div>" +
    '<div class="cmp-conclusion">' + esc(conclusion) + "</div>" +
    '<div class="compare-grid">' +
    '<div class="cmp-list"><div class="cmp-list-title">常规提问下 AI 提到：</div>' +
    '<div class="cmp-list-line" id="cmp-list-normal"><span class="score-sub">加载中…</span></div></div>' +
    '<div class="cmp-list"><div class="cmp-list-title">联网提问下 AI 提到：</div>' +
    '<div class="cmp-list-line" id="cmp-list-web"><span class="score-sub">加载中…</span></div></div>' +
    "</div>";

  geoApi("/api/report/competitor/detail?round_id=" + normalRound.id).then(function (d) {
    document.getElementById("cmp-list-normal").innerHTML = competitorListHtml(d.items || []);
  }).catch(function () {
    document.getElementById("cmp-list-normal").innerHTML = '<span class="score-sub">—</span>';
  });
  geoApi("/api/report/competitor/detail?round_id=" + webRound.id).then(function (d) {
    document.getElementById("cmp-list-web").innerHTML = competitorListHtml(d.items || []);
  }).catch(function () {
    document.getElementById("cmp-list-web").innerHTML = '<span class="score-sub">—</span>';
  });
}

/* ---------------- 卡片B：竞品深度分析（03b 7.2，N11-N14） ---------------- */

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
    /* 最近 30 轮汇总：统计表聚合 30 轮；LLM 深度分析仍取最新正常轮 */
    loadDeepStat(null);
    loadDeepTrend();
    var latest = repLatestNormalRound();
    if (latest) loadDeepAnalysis(latest.id);
    return;
  }
  loadDeepStat(base.id);
  loadDeepTrend();
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
  var trendSec = document.getElementById("deep-trend-section");
  var anaSec = document.getElementById("deep-analysis-section");
  var isRange = !roundId;  // 最近 30 轮汇总模式
  var url = roundId
    ? "/api/report/competitor/detail?round_id=" + roundId
    : "/api/report/competitor/detail";
  geoApi(url).then(function (data) {
    deepRoundTime = data.round_time || "";
    var items = data.items || [];
    if (!items.length) {
      /* 竞品档案为空：整卡替换为引导（03b 7.2.10） */
      guide.classList.remove("hidden");
      guide.innerHTML = "";
      guide.appendChild(emptyState(
        "",
        "先到设置里填上你的竞争对手名单，这里才会出现竞品分析。",
        "去设置页",
        function () { location.href = "/static/settings.html#brand"; }
      ));
      sec.innerHTML = "";
      trendSec.classList.add("hidden");
      anaSec.classList.add("hidden");
      return;
    }
    guide.classList.add("hidden");
    trendSec.classList.remove("hidden");
    anaSec.classList.remove("hidden");
    renderDeepTable(items, roundId, !!data.archive_empty, isRange);
  }).catch(function () {
    sec.innerHTML = "";
    sec.appendChild(emptyState("", "没有收集到回答，没有可统计的数据。", "", null));
  });
}

function renderDeepTable(items, roundId, archiveEmpty, isRange) {
  /* 数据缺失（轮次无回答）→ 空状态；否则正常表格 */
  var allZero = items.every(function (it) {
    return !(it.mention_count || 0) && !(it.mentioned_answers || 0);
  });
  if (allZero) {
    if (isRange) {
      buildDeepTable(items, roundId, archiveEmpty, isRange);
      return;
    }
    geoApi("/api/monitor/rounds/" + roundId).then(function (d) {
      var answered = (d.results || []).filter(function (r) { return r.answer_text; }).length;
      if (answered === 0) {
        var sec = document.getElementById("deep-stat-section");
        sec.innerHTML = "";
        sec.appendChild(emptyState("", "这轮没有收集到回答，没有可统计的数据。", "", null));
      } else {
        buildDeepTable(items, roundId, archiveEmpty, isRange);
      }
    }).catch(function () {
      buildDeepTable(items, roundId, archiveEmpty, isRange);
    });
    return;
  }
  buildDeepTable(items, roundId, archiveEmpty, isRange);
}

function buildDeepTable(items, roundId, archiveEmpty, isRange) {
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
      countCell = '<span class="deep-dim">本轮没被提到</span>';
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
  if (archiveEmpty) {
    html += '<div class="small-note" style="margin-bottom:8px">档案里还没填竞品名单，' +
      "当前竞品是系统从回答里自动提取的；" +
      '<a class="btn-text" href="/static/settings.html#brand">去设置页填竞品</a>，对比会更全。</div>';
  }
  html += '<div class="deep-sec-title" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
    "<span>" + esc(rangeTitle) + "</span>" +
    (rest.length
      ? '<button type="button" class="btn-text deep-more-toggle" data-act="deep-more">' +
        "展开查看全部 " + rest.length + " 个竞品 ↓</button>"
      : "") +
    "</div>" +
    /* 单表双 tbody：主表 + 折叠行共用同一列宽，展开后列保持对齐 */
    '<table class="table deep-table"><thead><tr>' +
    "<th>品牌/竞品</th><th>被提到次数</th><th>在几条回答里</th>" +
    "<th>平均第一次被提到时排第几</th><th></th></tr></thead>" +
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

/* ② 近 30 轮提及趋势（N12） */
function loadDeepTrend() {
  var container = document.getElementById("deep-trend-chart");
  if (!container) return;
  geoApi("/api/report/competitor/trend?rounds=30").then(function (data) {
    var labels = data.labels || [];
    var series = data.series || [];
    if (!labels.length || !series.length) {
      if (deepTrendChart) { deepTrendChart.dispose(); deepTrendChart = null; }
      emptyChart(container, "还没有足够的监测数据，跑几轮后再来看趋势。");
      return;
    }
    drawDeepTrend(container, labels, series, null);
  }).catch(function () {
    if (deepTrendChart) { deepTrendChart.dispose(); deepTrendChart = null; }
    emptyChart(container, "还没有足够的监测数据，跑几轮后再来看趋势。");
  });
}

function drawDeepTrend(container, labels, series, selected) {
  if (deepTrendChart) deepTrendChart.dispose();
  deepTrendChart = echarts.init(container);
  var selfName = deepSelfName || (series.length ? series[0].name : "");
  var names = series.map(function (s) { return s.name; });
  var option = {
    tooltip: { trigger: "axis", axisPointer: { type: "line" } },
    legend: { data: names, top: 0 },
    grid: { left: 40, right: 20, top: 36, bottom: 30 },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: "#E2E8F0" } },
      axisLabel: { color: "#6B7280" },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { color: "#6B7280" },
      splitLine: { lineStyle: { color: "#F1F5F9" } },
    },
    series: series.map(function (s) {
      var isSelf = s.name === selfName;
      return {
        name: s.name,
        type: "line",
        data: s.values,
        symbol: "circle",
        symbolSize: 5,
        connectNulls: true,
        lineStyle: { width: isSelf ? 2.5 : 2, color: isSelf ? "#2563EB" : "#94A3B8" },
        itemStyle: { color: isSelf ? "#2563EB" : "#94A3B8" },
      };
    }),
  };
  if (selected) option.legend.selected = selected;
  deepTrendChart.setOption(option);
  deepTrendChart.on("legendselectchanged", function (params) {
    var sel = params.selected || {};
    var keep = [];
    series.forEach(function (s) {
      if (s.name === selfName) return;
      if (sel[s.name] !== false) keep.push(s.name);
    });
    var q = keep.length ? "&competitors=" + encodeURIComponent(keep.join(",")) : "";
    geoApi("/api/report/competitor/trend?rounds=30" + q).then(function (d) {
      drawDeepTrend(container, d.labels || [], d.series || [], sel);
    }).catch(function () {});
  });
}

/* ③④⑤ 分析状态分区渲染（N14，每 10 秒轮询） */
function loadDeepAnalysis(roundId) {
  if (deepAnalysisTimer) { clearTimeout(deepAnalysisTimer); deepAnalysisTimer = null; }
  deepAnalysisRoundId = roundId;
  fetchDeepAnalysis(roundId);
}

function fetchDeepAnalysis(roundId) {
  if (deepAnalysisRoundId !== roundId) return;
  geoApi("/api/report/competitor/analysis?round_id=" + roundId).then(function (res) {
    if (deepAnalysisRoundId !== roundId) return;
    var status = res.status || "unavailable";
    var sec = document.getElementById("deep-analysis-section");
    if (status === "pending" || status === "running") {
      sec.innerHTML = '<div class="deep-placeholder">正在分析中，稍等一会儿再来看。</div>';
      deepAnalysisTimer = setTimeout(function () { fetchDeepAnalysis(roundId); }, 10000);
      return;
    }
    if (status === "failed") {
      var msg = "暂时无法分析：这次分析没成功，请稍后再试。统计部分不受影响。";
      if (res.error_msg) msg += "（" + res.error_msg + "）";
      sec.innerHTML = '<div class="deep-placeholder">' + esc(msg) + "</div>";
      return;
    }
    if (status === "unavailable") {
      sec.innerHTML =
        '<div class="deep-placeholder">暂时无法分析：分析用的 AI 钥匙（API Key）还没填。去设置页填上，下一轮监测会自动分析。' +
        ' <a class="deep-link-go" href="/static/settings.html#keys">去设置页</a></div>';
      return;
    }
    renderDeepAnalysisDone(sec, res.data || {});
  }).catch(function () {
    if (deepAnalysisRoundId !== roundId) return;
    var sec = document.getElementById("deep-analysis-section");
    sec.innerHTML =
      '<div class="deep-placeholder">暂时无法分析：这次分析没成功，请稍后再试。统计部分不受影响。</div>';
  });
}

function renderDeepAnalysisDone(sec, data) {
  var competitors = data.competitors || [];
  var advice = data.advice || [];
  var html = "";

  competitors.forEach(function (c) {
    var name = c.name || "";
    if (c.mentioned) {
      html += '<div class="reason-card">' +
        '<div class="reason-title">为什么提到它——「' + esc(name) + "」</div>" +
        '<div class="reason-text">' + esc(c.summary || "") + "</div>";
      var st = c.source_types || [];
      if (st.length) {
        html += '<div class="spec-row mt-8">AI 大概从哪听说它：' +
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
            '<div class="hit-head">' + esc(e.engine_code || "") + "</div>" +
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
      html += '<div class="reason-card reason-dim">「' + esc(name) + "」本轮 AI 回答里没有提到它</div>";
    }
  });

  if (advice.length) {
    html += '<div class="deep-advice-head mt-16">下一步优化方向</div>' +
      '<div class="card-sub">系统根据「竞品被提到、你没有被提到」的差距生成建议。建议仅供参考，不保证做了之后 AI 就一定会提到你。</div>' +
      '<div class="advice-stack">' +
      advice.map(function (a) {
        return '<div class="advice-card">' +
          '<div class="advice-line"><b>差距在哪：</b>' + esc(a.gap || "") + "</div>" +
          '<div class="advice-line"><b>去哪做：</b>' + esc(a.where || "") + "</div>" +
          '<div class="advice-line"><b>做什么：</b>' + esc(a.what || "") + "</div>" +
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
      bodyHtml = '<div class="score-sub">这轮没有提到「' + esc(titleName) + "」的回答</div>";
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
          answers: [],
          fails: [],
        };
      }
      const e = byEngine[key];
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
      html +=
        '<div class="detail-item">' +
        '<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">' +
        '<span style="font-weight:600">' + esc(e.name) + "</span>" +
        '<span class="num">回答 ' + e.answered + " 条 · 提到你 " + e.mentioned + " 次" +
        " · 首提位置 " + (e.firstPos ? "第 " + e.firstPos + " 位" : "—") + "</span>" +
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
              const text = String(r.answer_text || "").trim();
              const excerpt = text.length > 100 ? text.slice(0, 100) + "…" : text;
              const fullHtml = text.length > 100
                ? '<button type="button" class="btn-text deep-full-toggle mt-4" data-act="full-toggle">展开完整回答 ↓</button>' +
                  '<div class="hit-full hidden md-body">' + mdToHtml(text) + "</div>"
                : '<div class="hit-full md-body">' + mdToHtml(text) + "</div>";
              return '<div class="hit-item">' +
                '<div class="hit-head">第 ' + (i + 1) + " 问 · " + mentionedTag + " " + sentiTag + "</div>" +
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
