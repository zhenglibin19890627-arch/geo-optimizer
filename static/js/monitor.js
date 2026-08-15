/* ============================================================
   监测中心页（UI 文档 4.3）：问题/引擎勾选 + 进度三要素 + 手动粘贴 + 轮次列表
   ============================================================ */

let monQuestions = [];
let monEngines = [];
let monPolling = null;
let monTaskId = null;
let monTaskMeta = null; /* {questionTexts, engineCodes, startTotal} 用于明细展示 */
let monMode = "base"; /* base=常规提问 / web=联网提问（03b 6.1） */

/* 任务恢复 localStorage 按品牌隔离（B2）：geo_task_id_{brandId} / geo_task_meta_{brandId}，
   切换品牌互不干扰，切回原品牌时恢复（03b 3.3） */
function monTaskKeys() {
  const b = getBrandId();
  return {
    idKey: "geo_task_id_" + b,
    metaKey: "geo_task_meta_" + b,
  };
}

/* ---------------- 初始化 ---------------- */

function monInit() {
  Promise.all([
    geoApi("/api/questions?enabled=true"),
    geoApi("/api/settings/keys"),
  ]).then(function (res) {
    monQuestions = res[0] || [];
    monEngines = (res[1] || []).filter(function (k) { return k.engine !== "analysis"; });
    renderQList();
    renderEList();
    updateEstimate();
    initPasteEngine();
  }).catch(function () {});

  document.querySelectorAll("#mode-select .mode-card").forEach(function (card) {
    card.addEventListener("click", function () {
      document.querySelectorAll("#mode-select .mode-card").forEach(function (c) {
        c.classList.remove("active");
      });
      card.classList.add("active");
      monMode = card.getAttribute("data-mode");
      renderEList();
      updateEstimate();
    });
  });

  document.getElementById("q-select-all").addEventListener("change", function () {
    const boxes = document.querySelectorAll("#q-list input[type=checkbox]");
    boxes.forEach(function (b) { b.checked = this.checked; }, this);
    updateEstimate();
  });
  document.getElementById("e-select-all").addEventListener("change", function () {
    const on = this.checked;
    document.querySelectorAll("#e-list input[type=checkbox]:not(.model-check)")
      .forEach(function (b) { b.checked = on; });
    document.querySelectorAll("#e-list .model-check").forEach(function (b) {
      b.checked = on ? b.getAttribute("data-default") === "1" : false;
      b.disabled = !on;
    });
    updateEstimate();
  });

  document.getElementById("mon-start").addEventListener("click", monStart);
  document.getElementById("mon-stop").addEventListener("click", monStop);
  document.getElementById("paste-toggle").addEventListener("click", function () {
    document.getElementById("paste-body").classList.toggle("show");
    this.textContent = document.getElementById("paste-body").classList.contains("show")
      ? "▾ 手动粘贴备用通道" : "▸ 手动粘贴备用通道";
  });
  document.getElementById("paste-submit").addEventListener("click", pasteSubmit);

  loadRounds(1, true);
  resumeRunningTask();
}

/* ---------------- 问题勾选 ---------------- */

function renderQList() {
  const area = document.getElementById("q-list");
  area.innerHTML = "";
  if (!monQuestions.length) {
    area.appendChild(emptyState(
      "问题库还是空的",
      "先去问题库页扩展或添加几个问题吧",
      "去问题库",
      function () { location.href = "/static/questions.html"; }
    ));
    document.getElementById("q-count-text").textContent = "已选 0/0 个问题";
    return;
  }
  monQuestions.forEach(function (q) {
    const row = document.createElement("label");
    row.className = "checkbox-row";
    row.innerHTML =
      '<input type="checkbox" data-qid="' + q.id + '" checked>' +
      '<span class="label-text">' + esc(q.text) + "</span>" +
      '<span class="tag tag-gray">' + sourceText(q.source) + "</span>";
    row.querySelector("input").addEventListener("change", updateEstimate);
    area.appendChild(row);
  });
  updateQCount();
}

function selectedQuestions() {
  return Array.from(document.querySelectorAll("#q-list input[type=checkbox]:checked"))
    .map(function (b) { return parseInt(b.getAttribute("data-qid"), 10); });
}

function updateQCount() {
  const total = monQuestions.length;
  const sel = selectedQuestions().length;
  document.getElementById("q-count-text").textContent = "已选 " + sel + "/" + total + " 个问题";
}

/* ---------------- 引擎勾选 ---------------- */

function renderEList() {
  const area = document.getElementById("e-list");
  area.innerHTML = "";
  const webMode = monMode === "web";
  const anyConfigured = monEngines.some(function (k) { return k.configured; });
  document.getElementById("mon-no-key-banner").classList.toggle("hidden", anyConfigured);
  /* 联网模式下：不支持联网的引擎（如 opencode）行置灰「暂不支持联网」+ 横幅明示（03b 6.2） */
  const webUnsupported = monEngines.some(function (k) { return !k.supports_web_search; });
  const banner = document.getElementById("mon-web-banner");
  if (banner) banner.classList.toggle("hidden", !(webMode && webUnsupported));

  monEngines.forEach(function (k) {
    const webDisabled = webMode && !k.supports_web_search;
    const row = document.createElement("div");
    row.className = "checkbox-row" + ((!k.configured || webDisabled) ? " disabled" : "");
    row.style.justifyContent = "space-between";
    if (!k.configured || webDisabled) row.classList.add("disabled");

    const left = document.createElement("label");
    left.className = "checkbox-row";
    left.style.padding = "0";
    left.innerHTML =
      '<input type="checkbox" data-ecode="' + esc(k.engine) + '" ' + ((k.configured && !webDisabled) ? "checked" : "") + ((!k.configured || webDisabled) ? " disabled" : "") + ">" +
      '<span class="label-text">' + esc(k.display_name) + "</span>";
    left.querySelector("input").addEventListener("change", function () {
      syncModelGroup(k.engine, this.checked);
      updateEstimate();
    });
    if (!k.configured && !webDisabled) {
      left.addEventListener("click", function (e) {
        if (e.target.tagName !== "INPUT") {
          showToast("这把钥匙（API Key）还没填，去设置页填写后就能用了", "error");
        }
      });
    }

    const right = document.createElement("span");
    right.style.fontSize = "13px";
    if (webDisabled) {
      right.innerHTML = '<span class="tag tag-gray">暂不支持联网</span>';
    } else if (k.configured) {
      right.innerHTML = '<span class="tag tag-green">✔ 钥匙已填</span>';
    } else {
      right.innerHTML = '<span class="tag tag-orange">⚠ 钥匙未填</span> ' +
        '<a class="btn-text" href="/static/settings.html#keys">去设置页填</a>';
    }

    row.appendChild(left);
    row.appendChild(right);
    area.appendChild(row);

    /* 同 key 多模型（常规/联网档均支持）：型号一律列出（哪怕只有一档），
       多档位可勾选多个；联网档优先用 web_model_options（平台联网白名单） */
    const groupOpts = (webMode && k.web_model_options && k.web_model_options.length)
      ? k.web_model_options
      : k.model_options;
    if (groupOpts && groupOpts.length >= 1) {
      const mg = document.createElement("div");
      mg.className = "model-group";
      mg.style.cssText = "margin:0 0 10px 28px;display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center";
      const label = document.createElement("span");
      label.className = "small-note";
      label.style.cssText = "font-size:12px;flex:none";
      label.textContent = "模型：";
      mg.appendChild(label);
      groupOpts.forEach(function (opt) {
        /* 默认勾选：常规档=当前档；联网档=联网档模型（接口下发 web_model） */
        const defaultModel = webMode ? (k.web_model || k.model) : k.model;
        const isDefault = opt.name === defaultModel;
        const lb = document.createElement("label");
        lb.className = "checkbox-row";
        lb.style.padding = "2px 4px";
        lb.innerHTML =
          '<input type="checkbox" class="model-check" data-ecode="' + esc(k.engine) +
          '" data-model="' + esc(opt.name) + '" data-default="' + (isDefault ? "1" : "0") +
          '"' + (isDefault ? " checked" : "") +
          ((!k.configured || webDisabled) ? " disabled" : "") + ">" +
          '<span class="label-text" style="font-size:12px">' + esc(opt.desc || opt.name) + "</span>";
        lb.querySelector("input").addEventListener("change", updateEstimate);
        mg.appendChild(lb);
      });
      area.appendChild(mg);
    }
  });
  updateECount();
}

/* 引擎勾选联动：其模型组跟随启用/禁用 */
function syncModelGroup(code, engineChecked) {
  document.querySelectorAll('#e-list .model-check[data-ecode="' + code + '"]').forEach(function (b) {
    b.disabled = !engineChecked;
  });
}

/* 已选模型：{engine: [model,...]}；未勾任何模型的引擎 → 空数组（后端用当前档） */
function selectedModels() {
  const map = {};
  document.querySelectorAll("#e-list input[type=checkbox]:not(.model-check):checked").forEach(function (b) {
    const code = b.getAttribute("data-ecode");
    map[code] = Array.from(document.querySelectorAll(
      '#e-list .model-check[data-ecode="' + code + '"]:checked'))
      .map(function (m) { return m.getAttribute("data-model"); });
  });
  return map;
}

function selectedModelsTotal() {
  const map = selectedModels();
  let n = 0;
  document.querySelectorAll("#e-list input[type=checkbox]:not(.model-check):checked").forEach(function (b) {
    const code = b.getAttribute("data-ecode");
    n += (map[code] || []).length || 1;  // 无模型组或未勾模型 → 按 1 个（当前档）
  });
  return n;
}

function selectedEngines() {
  return Array.from(document.querySelectorAll("#e-list input[type=checkbox]:checked"))
    .map(function (b) { return b.getAttribute("data-ecode"); });
}

function updateECount() {
  const total = monEngines.filter(function (k) {
    return k.configured && !(monMode === "web" && !k.supports_web_search);
  }).length;
  const sel = selectedEngines().length;
  const selModels = selectedModelsTotal();
  document.getElementById("e-count-text").textContent =
    "已选 " + sel + "/" + total + " 家 · " + selModels + " 个模型";
}

/* ---------------- 预估 ---------------- */

function updateEstimate() {
  updateQCount();
  updateECount();
  const qs = selectedQuestions().length;
  const es = selectedModelsTotal();
  const calls = qs * es;
  const minutesLow = Math.max(Math.round(calls / 50 * 10), 1);
  const minutesHigh = Math.max(Math.round(calls / 50 * 15), minutesLow + 1);
  const totalHigh = monMode === "web" ? minutesHigh + 5 : minutesHigh;
  const extra = calls > 50 ? "（勾选越多，耗时越长）" : "";
  document.getElementById("mon-estimate").innerHTML =
    "预估：本轮约 " + minutesLow + "-" + totalHigh + " 分钟" + extra;
}

/* ---------------- 发起监测 ---------------- */

function monStart() {
  const qids = selectedQuestions();
  const ecodes = selectedEngines();
  const errEl = document.getElementById("mon-start-error");

  if (!qids.length) {
    errEl.textContent = "请至少勾选 1 个问题";
    return;
  }
  if (!ecodes.length) {
    errEl.textContent = "请至少勾选 1 家已填钥匙的 AI";
    return;
  }
  errEl.textContent = "";

  const btn = document.getElementById("mon-start");
  btn.disabled = true;
  btn.textContent = "监测中…";

  const body = { question_ids: qids, engine_codes: ecodes };
  if (monMode === "web") body.mode = "web";
  body.models = selectedModels();

  apiPost("/api/monitor/start", body)
    .then(function (data) {
      monTaskId = data.task_id;
      monTaskMeta = {
        questionTexts: monQuestions.filter(function (q) { return qids.indexOf(q.id) >= 0; }).map(function (q) { return q.text; }),
        engineCodes: ecodes,
        engineNames: {},
        startTotal: data.total_calls,
      };
      monEngines.forEach(function (k) { monTaskMeta.engineNames[k.engine] = k.display_name; });
      const keys = monTaskKeys();
      localStorage.setItem(keys.idKey, String(monTaskId));
      localStorage.setItem(keys.metaKey, JSON.stringify(monTaskMeta));

      showProgressCard(data.total_calls);
      pollTask(monTaskId);
    })
    .catch(function () {
      btn.disabled = false;
      btn.textContent = "开始监测";
    });
}

function monStop() {
  confirmDialog(
    "停止后本轮已做的部分会保存，未做的不会再做。确定停止吗？",
    function () {
      if (monTaskId === null) return;
      const btn = document.getElementById("mon-stop");
      btn.disabled = true;
      apiPost("/api/monitor/tasks/" + monTaskId + "/cancel", {})
        .then(function () {
          btn.disabled = false;
          showToast("已停止本轮监测，已问到的回答已保存", "success");
        })
        .catch(function () {
          btn.disabled = false;
        });
    },
    { title: "停止本轮监测", okText: "确定停止", danger: true }
  );
}

/* ---------------- 进度 ---------------- */

function showProgressCard(totalCalls) {
  const card = document.getElementById("mon-progress-card");
  card.classList.remove("hidden");
  card.classList.remove("done-card");
  card.classList.remove("failed-card");
  document.getElementById("mon-progress-sub").textContent = "";
  document.getElementById("mon-progress-count").textContent = "已完成 0/" + (totalCalls || 0);
  document.getElementById("mon-progress-title").textContent = "正在准备问题……";
  card.scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderTaskDetail(done, total, currentDesc) {
  const area = document.getElementById("mon-question-detail");
  const meta = monTaskMeta;
  if (!meta || !meta.questionTexts || !meta.engineCodes) {
    area.innerHTML = "";
    return;
  }
  const qs = meta.questionTexts;
  const es = meta.engineCodes;
  const names = meta.engineNames || {};
  const rows = [];
  let idx = 0;
  for (let ei = 0; ei < es.length; ei++) {
    for (let qi = 0; qi < qs.length; qi++) {
      const label = es[ei] === "manual" ? "手动" : (names[es[ei]] || es[ei]);
      let text;
      if (idx < done) {
        text = '<span class="tag tag-green">✔ 回答已收到</span>';
      } else if (idx === done && done < total) {
        text = '<span class="tag tag-primary">正在问…</span>';
      } else {
        text = '<span class="tag tag-gray">等待中…</span>';
      }
      rows.push(
        '<div class="detail-item"><span style="color:var(--text-sub)">' + esc(label) + " · </span>" +
        esc(qs[qi]) + " " + text + "</div>"
      );
      idx++;
    }
  }
  area.innerHTML = rows.join("");
}

function pollTask(taskId) {
  monPolling = startPolling(
    function () { return geoApi("/api/monitor/tasks/" + taskId + "/progress"); },
    function (data) {
      if (data.status === "done") {
        if (monPolling) monPolling.stop();  // 结束轮询，完成提醒只弹一次
        onTaskDone(taskId, data);
        return;
      }
      if (data.status === "failed") {
        if (monPolling) monPolling.stop();
        onTaskFailed(data);
        return;
      }
      if (data.status === "cancelled") {
        if (monPolling) monPolling.stop();
        onTaskStopped({ error_msg: "本轮监测已停止" });
        return;
      }
      const card = document.getElementById("mon-progress-card");
      card.classList.remove("done-card");
      card.classList.remove("failed-card");
      document.getElementById("mon-progress-title").textContent = data.current_desc || "正在问…";
      document.getElementById("mon-progress").querySelector(".bar").style.width =
        (data.progress || 0) + "%";
      const remain = data.remain_seconds !== null && data.remain_seconds !== undefined
        ? "，预计还需 " + fmtDuration(data.remain_seconds) : "";
      document.getElementById("mon-progress-count").textContent =
        "已完成 " + data.done_calls + "/" + data.total_calls + "（" + (data.progress || 0) + "%）" + remain;
      renderTaskDetail(data.done_calls, data.total_calls, data.current_desc);
    },
    function () {
      monTaskId = null;
    }
  );
}

function onTaskDone(taskId, data) {
  document.getElementById("mon-progress-title").textContent = "本轮监测完成 ✓";
  document.getElementById("mon-progress").querySelector(".bar").style.width = "100%";
  document.getElementById("mon-progress-count").textContent =
    "共问 " + data.total_calls + " 个问题";
  document.getElementById("mon-progress-sub").textContent = "";
  const card = document.getElementById("mon-progress-card");
  card.classList.add("done-card");
  renderTaskDetail(data.total_calls, data.total_calls, "");

  const keys = monTaskKeys();
  localStorage.removeItem(keys.idKey);
  localStorage.removeItem(keys.metaKey);
  monTaskId = null;
  const btn = document.getElementById("mon-start");
  btn.disabled = false;
  btn.textContent = "开始监测";

  geoApi("/api/monitor/rounds?page=1").then(function (list) {
    const rounds = list.items || [];
    const r = rounds.find(function (x) { return String(x.task_id) === String(taskId); }) || rounds[0];
    if (r) {
      /* U3：完成态进度卡加"去看报告"直达入口 */
      document.getElementById("mon-progress-sub").innerHTML =
        '<a class="btn btn-primary" href="/static/report.html?round=' + r.id + '">去看报告 →</a>';
      const summary = r.summary || {};
      const mentioned = summary.mentioned_answers || 0;
      const answers = summary.total_answers || 0;
      if (mentioned > 0) {
        showToast("监测完成，AI 提到了你 " + mentioned + " 次", "success");
      } else {
        showToast("本轮监测完成，共收到 " + answers + " 个回答", "success");
      }
    } else {
      showToast("本轮监测完成 ✓", "success");
    }
    loadRounds(1, true);
  }).catch(function () {
    showToast("本轮监测完成 ✓", "success");
    loadRounds(1, true);
  });
}

function onTaskStopped(data) {
  document.getElementById("mon-progress-title").textContent = "本轮监测已停止";
  document.getElementById("mon-progress-count").textContent =
    (data.error_msg || "本轮监测已停止") + "（已完成的部分已保存）";
  document.getElementById("mon-progress-sub").textContent = "可以在上面重新发起一轮";
  const card = document.getElementById("mon-progress-card");
  card.classList.add("failed-card");
  const keys = monTaskKeys();
  localStorage.removeItem(keys.idKey);
  localStorage.removeItem(keys.metaKey);
  monTaskId = null;
  const btn = document.getElementById("mon-start");
  btn.disabled = false;
  btn.textContent = "开始监测";
}

function onTaskFailed(data) {
  document.getElementById("mon-progress-title").textContent = "本轮监测没成功";
  document.getElementById("mon-progress-count").textContent =
    (data.error_msg || "监测中途出了点意外") + "（已完成的部分已保存）";
  document.getElementById("mon-progress-sub").textContent = "可以在上面重新发起一轮";
  const card = document.getElementById("mon-progress-card");
  card.classList.add("failed-card");
  const keys = monTaskKeys();
  localStorage.removeItem(keys.idKey);
  localStorage.removeItem(keys.metaKey);
  monTaskId = null;
  const btn = document.getElementById("mon-start");
  btn.disabled = false;
  btn.textContent = "开始监测";
}

/* 离开页面再回来：恢复进行中的监测（仅当前品牌，B2 品牌维度隔离） */
function resumeRunningTask() {
  const keys = monTaskKeys();
  const taskId = localStorage.getItem(keys.idKey);
  if (!taskId) return;
  monTaskId = parseInt(taskId, 10);
  const metaRaw = localStorage.getItem(keys.metaKey);
  if (metaRaw) {
    try { monTaskMeta = JSON.parse(metaRaw); } catch (e) { monTaskMeta = null; }
  }
  geoApi("/api/monitor/tasks/" + monTaskId + "/progress").then(function (data) {
    if (data.status === "done") {
      showProgressCard(data.total_calls);
      onTaskDone(monTaskId, data);
      return;
    }
    if (data.status === "failed") {
      showProgressCard(data.total_calls);
      onTaskFailed(data);
      return;
    }
    if (data.status === "cancelled") {
      showProgressCard(data.total_calls);
      onTaskStopped(data);
      return;
    }
    const btn = document.getElementById("mon-start");
    btn.disabled = true;
    btn.textContent = "监测中…";
    showProgressCard(data.total_calls);
    document.getElementById("mon-progress-title").textContent = data.current_desc || "正在问…";
    document.getElementById("mon-progress").querySelector(".bar").style.width =
      (data.progress || 0) + "%";
    document.getElementById("mon-progress-count").textContent =
      "已完成 " + data.done_calls + "/" + data.total_calls + "（" + (data.progress || 0) + "%）";
    renderTaskDetail(data.done_calls, data.total_calls, data.current_desc);
    pollTask(monTaskId);
  }).catch(function () {
    localStorage.removeItem(keys.idKey);
    localStorage.removeItem(keys.metaKey);
    monTaskId = null;
  });
}

/* ---------------- 手动粘贴 ---------------- */

function pasteSubmit() {
  const engine = document.getElementById("paste-engine").value;
  const question = document.getElementById("paste-question").value.trim();
  const answer = document.getElementById("paste-answer").value.trim();
  const btn = document.getElementById("paste-submit");

  if (!question) {
    showToast("请填一下你问 AI 的问题（问题内容）", "error");
    return;
  }
  if (!answer) {
    showToast("请把 AI 的回答粘贴进来（回答内容）", "error");
    return;
  }

  btn.disabled = true;
  btn.textContent = "分析中…";
  apiPost("/api/monitor/paste", { engine_code: engine, question_text: question, answer_text: answer })
    .then(function (data) {
      const r = data.result || {};
      const mentioned = r.is_mentioned
        ? "提到你 " + (r.mention_count || 0) + " 次"
        : "没有提到你";
      document.getElementById("paste-result").innerHTML =
        '<div class="alert-banner" style="border-left-color:var(--success)">' +
        '<div class="alert-text">已记录：情感：' + sentimentText(r.sentiment) +
        " · " + mentioned + " · 引用信源 " + ((r.sources || []).length) + " 个</div></div>";
      btn.disabled = false;
      btn.textContent = "立即分析";
    })
    .catch(function () {
      btn.disabled = false;
      btn.textContent = "立即分析";
    });
}

function initPasteEngine() {
  const sel = document.getElementById("paste-engine");
  sel.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = "manual";
  opt.textContent = "手动";
  sel.appendChild(opt);
  monEngines.forEach(function (k) {
    const o = document.createElement("option");
    o.value = k.engine;
    o.textContent = k.display_name;
    sel.appendChild(o);
  });
}

/* ---------------- 轮次列表 ---------------- */

let roundPage = 1;

function loadRounds(page, fresh) {
  geoApi("/api/monitor/rounds?page=" + page).then(function (data) {
    const area = document.getElementById("round-list-area");
    const more = document.getElementById("round-list-more");
    if (fresh) {
      roundPage = 1;
      area.innerHTML = "";
    }
    roundPage = page;
    const items = data.items || [];

    if (!items.length && fresh) {
      area.appendChild(emptyState(
        "还没有监测记录",
        "系统会自动把每次监测存下来，方便你回看 AI 每次怎么评价你",
        "发起第一次监测",
        function () {
          document.getElementById("mon-start").scrollIntoView({ behavior: "smooth", block: "center" });
        }
      ));
      more.innerHTML = "";
      return;
    }

    items.forEach(function (r) {
      const taskStatus = r.task_status || "";
      const isAbnormal = taskStatus === "cancelled" || taskStatus === "failed";
      const mentionRate = Math.round((r.mention_rate || 0) * 100);
      const netSentiment = r.net_sentiment;
      let sText = "中性";
      if (netSentiment > 0.01) sText = "正向";
      if (netSentiment < -0.01) sText = "负面";
      const sCls = netSentiment > 0.01 ? "tag-green" : (netSentiment < -0.01 ? "tag-red" : "tag-gray");
      const summary = r.summary || {};
      const engineCount = Object.keys(summary.per_engine || {}).length || 0;
      const score = r.overall_score;

      const row = document.createElement("div");
      row.className = "round-row" + (isAbnormal ? " round-row-dim" : "");
      const modeTag = r.mode === "web"
        ? ' <span class="tag tag-green">联网提问</span>'
        : ' <span class="tag tag-primary">常规提问</span>';
      row.innerHTML =
        '<div class="rr-main">' +
        '<div class="row-time">' + esc(r.created_at || "") +
        modeTag +
        (r.task_type === "scheduled" ? ' <span class="tag tag-gray">定时</span>' : "") +
        (isAbnormal ? " " + statusTag(taskStatus) : "") + "</div>" +
        '<div class="mt-8">提及率 <span class="num" style="font-weight:600">' + mentionRate + "%</span>" +
        ' · 情感 <span class="tag ' + sCls + '">' + sText + "</span>" +
        " · " + engineCount + " 家 AI</div>" +
        "</div>" +
        '<span class="badge-score" style="color:' + scoreColor(score) + ";background:" + scoreColor(score) + "1A" + '">' + esc(score) + " 分</span>" +
        '<a class="btn btn-secondary" href="/static/report.html?round=' + r.id + '">看报告</a>';
      area.appendChild(row);
    });

    more.innerHTML = "";
    if (data.total > page * data.page_size) {
      const btn = document.createElement("button");
      btn.className = "btn btn-secondary";
      btn.textContent = "加载更多";
      btn.addEventListener("click", function () { loadRounds(page + 1, false); });
      more.appendChild(btn);
    }
  }).catch(function () {});
}

/* ---------------- 启动 ---------------- */

initNav("monitor");
bindConcepts(document);
monInit();
