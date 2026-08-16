/* ============================================================
   报告页（拆分 1/3）：共享状态 + 轮次下拉 + 趋势图 + 信源排行
   拆分自原 report.js（1100 行单文件），全局函数名与状态名保持不变。
   加载顺序：report-trend.js -> report-deep.js -> report.js
   ============================================================ */

/* ============================================================
   报告图表页（UI 文档 4.4）：ECharts 趋势三 Tab + 竞品对比 + 信源排行 + 引擎明细
   ============================================================ */

let repMetric = "score";
let repRoundId = null;
let repRoundCount = 0;
let trendChart = null;
let deepAnalysisTimer = null;
let deepAnalysisRoundId = null;
let repRoundsAll = [];
let deepSelfName = "";
let deepRoundTime = "";

const FEE_NOTE_KEY = "geo_deep_fee_note_closed";

const DEEP_SPEC_NOTE = "（系统推测，仅供参考，不是查到的真实链接）";

const YUANBAO_NOTE =
  "说明：元宝的数据来自它的同门底座「混元」官方服务，与元宝 App 上的实际回答可能有差异。";

function loadRoundsSelect(selectId) {
  geoApi("/api/monitor/rounds?page=1").then(function (data) {
    const sel = document.getElementById("report-round-select");
    const items = data.items || [];
    repRoundsAll = items;
    sel.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = "最近 30 轮";
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
      /* 默认选最新一轮：下拉框回填该轮（此前只设 repRoundId 不设 sel.value，
         导致 repSelectedRound() 读不到选中轮、常规轮的信源卡不隐藏） */
      sel.value = String(items[0].id);
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
  if (repMetric === "competitor") {
    /* 竞品提及 Tab：合并自竞品深度分析卡片（2026-08-15），口径不变：近 30 轮 */
    loadTrendCompetitor(container);
    return;
  }
  const modeNote = document.getElementById("trend-mode-note");
  const compNote = document.getElementById("trend-comp-note");
  if (modeNote) modeNote.classList.remove("hidden");
  if (compNote) compNote.classList.add("hidden");
  if (repRoundCount < 1) {
    /* 无数据：空状态引导（03b 3.4） */
    container.innerHTML = "";
    container.appendChild(emptyState(
      "该品牌暂无数据",
      "请先在监测中心发起一轮监测",
      "去监测中心",
      function () { location.href = "/static/monitor.html"; }
    ));
    return;
  }
  /* M18 调两次：常规 + 联网，各画一条线（07z 任务1）；有 1 轮也正常画点 */
  const url = "/api/report/trend?metric=" + repMetric + "&rounds=30&mode=";
  Promise.all([
    geoApi(url + "normal").catch(function () { return { values: [] }; }),
    geoApi(url + "web").catch(function () { return { values: [] }; }),
  ]).then(function (res) {
    const nValues = (res[0].values) || [];
    const wValues = (res[1].values) || [];
    if (!nValues.length && !wValues.length) {
      emptyChart(container, "暂无趋势数据，请先在监测中心发起一轮监测");
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


/* ---------------- 竞品提及 Tab（合并自竞品深度分析卡片，2026-08-15） ---------------- */

function loadTrendCompetitor(container) {
  if (!container) return;
  const modeNote = document.getElementById("trend-mode-note");
  if (modeNote) modeNote.classList.add("hidden");
  geoApi("/api/report/competitor/trend?rounds=30").then(function (data) {
    const labels = data.labels || [];
    const series = data.series || [];
    const note = document.getElementById("trend-comp-note");
    if (note) {
      if (data.truncated) {
        note.textContent = "近 30 轮共提取 " + (data.total || "") +
          " 家竞品，趋势图仅展示累计提及次数最多的前 3 家。";
        note.classList.remove("hidden");
      } else {
        note.classList.add("hidden");
      }
    }
    if (!labels.length || !series.length) {
      if (trendChart) { trendChart.dispose(); trendChart = null; }
      emptyChart(container, "监测数据不足，请完成更多轮监测后再查看趋势。");
      return;
    }
    drawTrendCompetitor(container, labels, series, null);
  }).catch(function () {
    if (trendChart) { trendChart.dispose(); trendChart = null; }
    emptyChart(container, "监测数据不足，请完成更多轮监测后再查看趋势。");
  });
}

function drawTrendCompetitor(container, labels, series, selected) {
  if (trendChart) trendChart.dispose();
  trendChart = echarts.init(container);
  const selfName = series.length ? series[0].name : "";
  const names = series.map(function (s) { return s.name; });
  const option = {
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
      const isSelf = s.name === selfName;
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
  trendChart.setOption(option);
  trendChart.on("legendselectchanged", function (params) {
    const sel = params.selected || {};
    const keep = [];
    series.forEach(function (s) {
      if (s.name === selfName) return;
      if (sel[s.name] !== false) keep.push(s.name);
    });
    const q = keep.length ? "&competitors=" + encodeURIComponent(keep.join(",")) : "";
    geoApi("/api/report/competitor/trend?rounds=30" + q).then(function (d) {
      drawTrendCompetitor(container, d.labels || [], d.series || [], sel);
    }).catch(function () {});
  });
}

/* ---------------- 引用信源 ---------------- */

function loadSources() {
  const card = document.getElementById("sources-card");
  const sel = repSelectedRound();
  if (repRoundId && sel && sel.mode === "normal") {
    /* 常规档回答不带引用信源：选中常规轮时整卡隐藏（2026-08-15 用户要求） */
    if (card) card.classList.add("hidden");
    return;
  }
  if (card) card.classList.remove("hidden");
  const area = document.getElementById("sources-area");
  if (repRoundCount < 2) {
    area.innerHTML = "";
    area.appendChild(emptyState(
      "",
      "监测轮次不足，暂无法统计引用信源。",
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
        "暂无引用信源",
        repRoundId
          ? "本轮 AI 回答中未出现可识别的网站引用（目前各家 AI 的联网接口均不返回引用链接，属平台限制）"
          : "近 30 轮 AI 回答中未出现可识别的网站引用（目前各家 AI 的联网接口均不返回引用链接，属平台限制）",
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
