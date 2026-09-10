/* ============================================================
   监测中心页（UI 文档 4.3）：问题/引擎勾选 + 进度三要素 + 手动粘贴 + 轮次列表
   ============================================================ */

let monQuestions = [];
let monEngines = [];
let monSchedModels = { normal: {}, web: {} }; /* 设置页「定时用哪些模型」，监测中心默认状态跟随它（2026-09-04） */
let monPolling = null;
let monTaskIds = null; /* 一轮可含多任务（常规+联网串行，2026-08-19） */
let monTaskMeta = null; /* {questionTexts, modes:[{mode,label,engineCodes}], engineNames, startTotal} */
const MODE_LABELS = { normal: "常规提问", web: "联网提问" };

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
    geoApi("/api/schedule"),
  ]).then(function (res) {
    monQuestions = res[0] || [];
    monEngines = (res[1] || []).filter(function (k) {
      // 监测只关心 5 家自动引擎：分析/创作模型在设置页配置，与此无关
      return k.engine !== "analysis" && k.engine !== "create";
    });
    /* 模型线默认状态跟随设置页定时配置：勾选与型号以「定时用哪些模型」为底，
       本轮可临时改，不影响定时配置（改定时请去设置页保存） */
    const sched = res[2] || {};
    const models = sched.models && typeof sched.models === "object" ? sched.models : {};
    monSchedModels = {
      normal: models.normal && typeof models.normal === "object" ? models.normal : {},
      web: models.web && typeof models.web === "object" ? models.web : {},
    };
    renderQList();
    renderEList();
    updateEstimate();
    initPasteEngine();
  }).catch(function () {});

  /* 模式多选（2026-08-19）：勾/取消只影响对应模型线可用性与预估 */
  ["normal", "web"].forEach(function (mode) {
    document.getElementById("mode-check-" + mode).addEventListener("change", function () {
      if (!document.getElementById("mode-check-normal").checked &&
          !document.getElementById("mode-check-web").checked) {
        this.checked = true;
        showToast("请至少选择一种提问方式", "error");
        return;
      }
      applyModeLineStates();
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
    document.querySelectorAll("#e-list input.eng-check").forEach(function (b) {
      if (b.disabled) return;
      b.checked = on;
    });
    document.querySelectorAll("#e-list input.model-line-check").forEach(function (b) {
      if (b.disabled) return;
      b.checked = on;
    });
    applyModeLineStates();
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

/* ---------------- 引擎与模型选择（2026-09-04：模型手填 + 每条「常规/联网」线独立勾选是否参加；留空=当前档） ---------------- */

function monModesSelected() {
  const out = [];
  if (document.getElementById("mode-check-normal").checked) out.push("normal");
  if (document.getElementById("mode-check-web").checked) out.push("web");
  return out;
}

function engineDefaultModel(k, mode) {
  return mode === "web" ? (k.web_model || k.model) : k.model;
}

function renderEList() {
  const area = document.getElementById("e-list");
  area.innerHTML = "";
  const anyConfigured = monEngines.some(function (k) { return k.configured; });
  document.getElementById("mon-no-key-banner").classList.toggle("hidden", anyConfigured);
  const webUnsupported = monEngines.some(function (k) { return !k.supports_web_search; });
  const banner = document.getElementById("mon-web-banner");
  if (banner) banner.classList.toggle("hidden", !webUnsupported);

  monEngines.forEach(function (k) {
    const row = document.createElement("div");
    row.className = "engine-row";
    row.style.cssText = "flex-direction:column;align-items:stretch;gap:4px;padding:10px 12px";

    const head = document.createElement("div");
    head.style.cssText = "display:flex;align-items:center;gap:10px;flex-wrap:wrap";
    const master = document.createElement("label");
    master.className = "checkbox-row";
    master.style.padding = "0";
    master.innerHTML =
      '<input type="checkbox" class="eng-check" data-ecode="' + esc(k.engine) + '"' +
      (k.configured ? " checked" : " disabled") + ">" +
      '<span class="label-text" style="font-weight:600">' + esc(k.display_name) + "</span>";
    master.querySelector("input").addEventListener("change", function () {
      applyModeLineStates();
      updateEstimate();
    });
    if (!k.configured) {
      master.addEventListener("click", function (e) {
        if (e.target.tagName !== "INPUT") {
          showToast("该引擎的 API 钥匙尚未填写，请先到设置页填写", "error");
        }
      });
    }
    head.appendChild(master);
    const tags = [];
    if (!k.supports_web_search) tags.push('<span class="tag tag-gray">不支持联网</span>');
    tags.push(k.configured
      ? '<span class="tag tag-green">✔ 钥匙已填</span>'
      : '<span class="tag tag-orange">⚠ 钥匙未填</span> <a class="btn-text" href="/static/settings.html#keys">去设置页填</a>');
    head.insertAdjacentHTML("beforeend", tags.join(" "));
    row.appendChild(head);

    // 常规/联网两条线合成一行两列（窄屏自动换行）；每条线独立勾选=本轮是否参加
    // 默认状态跟随设置页「定时用哪些模型」：有保存选择→按清单勾选/填型号；
    // 该模式从未选择→默认全勾、型号留空（回落当前档）
    const lineRow = document.createElement("div");
    lineRow.style.cssText = "display:flex;align-items:center;gap:6px 16px;flex-wrap:wrap;margin-left:6px";
    ["normal", "web"].forEach(function (mode) {
      if (mode === "web" && !k.supports_web_search) return;
      const picked = monSchedModels[mode][k.engine];
      const hasChoice = Object.keys(monSchedModels[mode] || {}).length > 0;
      const lineChecked = hasChoice ? Array.isArray(picked) : true;
      const line = document.createElement("div");
      line.className = "model-line";
      line.setAttribute("data-mode", mode);
      line.setAttribute("data-ecode", esc(k.engine));
      line.style.cssText = "display:flex;align-items:center;gap:6px;flex:1 1 280px;min-width:0";
      const check = document.createElement("label");
      check.className = "checkbox-row";
      check.style.padding = "2px 4px";
      check.innerHTML =
        '<input type="checkbox" class="model-line-check" data-mode="' + mode +
        '" data-ecode="' + esc(k.engine) + '"' +
        (k.configured && lineChecked ? " checked" : " disabled") + ">" +
        '<span class="label-text" style="font-size:12px">' + (mode === "normal" ? "常规" : "联网") + "</span>";
      check.querySelector("input").addEventListener("change", updateEstimate);
      line.appendChild(check);
      const input = document.createElement("input");
      input.className = "input model-input";
      input.type = "text";
      input.setAttribute("data-mode", mode);
      input.setAttribute("data-ecode", esc(k.engine));
      input.value = (picked || []).join(", ");
      input.placeholder = "留空=当前档（" + engineDefaultModel(k, mode) + "），多个型号用逗号分隔";
      input.style.cssText = "flex:1;min-width:120px;padding:3px 8px;font-size:12px";
      input.addEventListener("input", updateEstimate);
      line.appendChild(input);
      lineRow.appendChild(line);
    });
    row.appendChild(lineRow);
    area.appendChild(row);
  });
  applyModeLineStates();
  updateECount();
}

/* 模型线可用性：该模式的模式勾选 + 引擎主勾选都满足，线上的勾选框/输入框才可用
   （不可用的整条线置灰）；线上勾选框再决定这条线本轮是否参加 */
function applyModeLineStates() {
  const modes = monModesSelected();
  document.querySelectorAll("#e-list .model-line").forEach(function (line) {
    const mode = line.getAttribute("data-mode");
    const code = line.getAttribute("data-ecode");
    const master = document.querySelector('#e-list input.eng-check[data-ecode="' + code + '"]');
    const usable = modes.indexOf(mode) >= 0 && master && master.checked && !master.disabled;
    line.querySelectorAll("input.model-line-check, input.model-input").forEach(function (b) { b.disabled = !usable; });
    line.style.opacity = usable ? "" : "0.45";
  });
}

/* 已选模型（自由填写口径）：{normal: {engine: [...]}, web: {...}}
   线上勾选框勾了才参加；输入框留空 = []（后端回落当前档） */
function selectedModelsPerMode() {
  const out = { normal: {}, web: {} };
  const modes = monModesSelected();
  document.querySelectorAll("#e-list .model-line").forEach(function (line) {
    const mode = line.getAttribute("data-mode");
    if (modes.indexOf(mode) < 0) return;
    const code = line.getAttribute("data-ecode");
    const master = document.querySelector('#e-list input.eng-check[data-ecode="' + code + '"]');
    const check = line.querySelector("input.model-line-check");
    if (!master || !master.checked || master.disabled) return;
    if (!check || check.disabled || !check.checked) return;
    const tokens = String(line.querySelector("input.model-input").value || "")
      .split(/[，,、;；\s]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    if (!out[mode][code]) out[mode][code] = tokens;
  });
  return out;
}

function selectedModelsTotal() {
  const map = selectedModelsPerMode();
  let n = 0;
  Object.keys(map).forEach(function (m) {
    Object.keys(map[m]).forEach(function (c) { n += Math.max(map[m][c].length, 1); });
  });
  return n;
}

/* 选中厂家 = 任一模式的线上该引擎主勾选被勾中 */
function selectedEngines() {
  const map = selectedModelsPerMode();
  const set = {};
  Object.keys(map).forEach(function (m) {
    Object.keys(map[m]).forEach(function (c) { set[c] = true; });
  });
  return Object.keys(set);
}

function updateECount() {
  const total = monEngines.filter(function (k) { return k.configured; }).length;
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
  const webSel = document.getElementById("mode-check-web").checked;
  const totalHigh = webSel ? minutesHigh + 5 : minutesHigh;
  const extra = calls > 50 ? "（勾选越多，耗时越长）" : "";
  document.getElementById("mon-estimate").innerHTML =
    "预估：本轮约 " + minutesLow + "-" + totalHigh + " 分钟" + extra;
}

/* ---------------- 发起监测 ---------------- */

function monStart() {
  const qids = selectedQuestions();
  const modes = monModesSelected();
  const models = selectedModelsPerMode();
  const errEl = document.getElementById("mon-start-error");

  if (!qids.length) {
    errEl.textContent = "请至少勾选 1 个问题";
    return;
  }
  if (!modes.length) {
    errEl.textContent = "请至少选择一种提问方式（常规提问或联网提问）";
    return;
  }
  for (let i = 0; i < modes.length; i++) {
    const m = modes[i];
    const names = Object.keys(models[m] || {});
    if (!names.length) {
      errEl.textContent = MODE_LABELS[m] + "没有可参与的引擎：请在下方至少勾选一条对应的模型线（型号留空 = 用该引擎当前档）";
      return;
    }
  }
  errEl.textContent = "";

  const btn = document.getElementById("mon-start");
  btn.disabled = true;
  btn.textContent = "监测中…";

  const body = { question_ids: qids, modes: modes, models: models };

  apiPost("/api/monitor/start", body)
    .then(function (data) {
      monTaskIds = data.task_ids || [data.task_id];
      monTaskMeta = {
        questionTexts: monQuestions.filter(function (q) { return qids.indexOf(q.id) >= 0; }).map(function (q) { return q.text; }),
        modes: (data.modes && data.modes.length ? data.modes : modes).map(function (m) {
          return { mode: m, label: MODE_LABELS[m] || m, engineCodes: Object.keys(models[m] || {}) };
        }),
        engineNames: {},
        startTotal: data.total_calls,
      };
      monEngines.forEach(function (k) { monTaskMeta.engineNames[k.engine] = k.display_name; });
      const keys = monTaskKeys();
      localStorage.setItem(keys.idKey, JSON.stringify(monTaskIds));
      localStorage.setItem(keys.metaKey, JSON.stringify(monTaskMeta));

      showProgressCard(data.total_calls);
      pollTasks(monTaskIds);
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
      const ids = monTaskIds || [];
      if (!ids.length) return;
      const btn = document.getElementById("mon-stop");
      btn.disabled = true;
      /* 一轮可能含多个串行任务（常规+联网）：全部停止 */
      Promise.all(ids.map(function (id) {
        return apiPost("/api/monitor/tasks/" + id + "/cancel", {}).catch(function () { return null; });
      })).then(function () {
        btn.disabled = false;
        showToast("已停止本轮监测，已问到的回答已保存", "success");
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

/* 明细：按模式分段（常规段、联网段）逐条展示 引擎 · 问题 · 状态；
   兼容旧 meta（无 modes 字段）退回单段 */
function taskMetaSections() {
  const meta = monTaskMeta;
  if (!meta) return [];
  if (meta.modes && meta.modes.length) return meta.modes;
  return [{ mode: "normal", label: "常规提问", engineCodes: meta.engineCodes || [] }];
}

function renderTaskDetail(doneAll, totalAll) {
  const area = document.getElementById("mon-question-detail");
  const meta = monTaskMeta;
  if (!meta || !meta.questionTexts) {
    area.innerHTML = "";
    return;
  }
  const qs = meta.questionTexts;
  const names = meta.engineNames || {};
  const rows = [];
  let idx = 0;
  taskMetaSections().forEach(function (sec) {
    rows.push('<div class="small-note" style="margin:6px 0 2px;font-weight:600">【' +
      esc(sec.label) + "】</div>");
    (sec.engineCodes || []).forEach(function (code) {
      qs.forEach(function (q) {
        const label = code === "manual" ? "手动" : (names[code] || code);
        let text;
        if (idx < doneAll) {
          text = '<span class="tag tag-green">✔ 回答已收到</span>';
        } else if (idx === doneAll && doneAll < totalAll) {
          text = '<span class="tag tag-primary">正在问…</span>';
        } else {
          text = '<span class="tag tag-gray">等待中…</span>';
        }
        rows.push(
          '<div class="detail-item"><span style="color:var(--text-sub)">' + esc(label) + " · </span>" +
          esc(q) + " " + text + "</div>"
        );
        idx++;
      });
    });
  });
  area.innerHTML = rows.join("");
}

/* 一轮多任务聚合轮询：全部任务进度求和，取正在跑的任务展示当前问句 */
function pollTasks(ids) {
  monPolling = startPolling(
    function () {
      return Promise.all(ids.map(function (id) {
        return geoApi("/api/monitor/tasks/" + id + "/progress");
      })).then(function (list) { return { ids: ids, tasks: list }; });
    },
    function (agg) { handleAggProgress(agg); },
    function () {
      monTaskIds = null;
    }
  );
}

function handleAggProgress(agg) {
  const tasks = agg.tasks || [];
  const active = tasks.filter(function (t) { return t.status === "pending" || t.status === "running"; });
  if (active.length) {
    const card = document.getElementById("mon-progress-card");
    card.classList.remove("done-card");
    card.classList.remove("failed-card");
    const running = tasks.find(function (t) { return t.status === "running"; }) || active[0];
    const runIdx = tasks.indexOf(running);
    const sections = taskMetaSections();
    const stage = sections[runIdx] ? "【" + sections[runIdx].label + "】" : "";
    const done = tasks.reduce(function (a, t) { return a + (t.done_calls || 0); }, 0);
    const total = tasks.reduce(function (a, t) { return a + (t.total_calls || 0); }, 0);
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    document.getElementById("mon-progress-title").textContent =
      stage + (running.current_desc || "正在问…");
    document.getElementById("mon-progress").querySelector(".bar").style.width = pct + "%";
    const remain = running.remain_seconds !== null && running.remain_seconds !== undefined
      ? "，预计还需 " + fmtDuration(running.remain_seconds) : "";
    document.getElementById("mon-progress-count").textContent =
      "已完成 " + done + "/" + total + "（" + pct + "%）" + remain;
    renderTaskDetail(done, total);
    return;
  }
  const cancelled = tasks.find(function (t) { return t.status === "cancelled"; });
  if (cancelled) {
    if (monPolling) monPolling.stop();
    onTaskStopped(cancelled);
    return;
  }
  const failed = tasks.find(function (t) { return t.status === "failed"; });
  if (failed) {
    if (monPolling) monPolling.stop();
    onTaskFailed(failed);
    return;
  }
  if (monPolling) monPolling.stop();
  onTasksDone(agg.ids, tasks);
}

function onTasksDone(taskIds, tasks) {
  const total = tasks.reduce(function (a, t) { return a + (t.total_calls || 0); }, 0);
  document.getElementById("mon-progress-title").textContent = "本轮监测完成 ✓";
  document.getElementById("mon-progress").querySelector(".bar").style.width = "100%";
  document.getElementById("mon-progress-count").textContent =
    "共问 " + total + " 个问题";
  document.getElementById("mon-progress-sub").textContent = "";
  const card = document.getElementById("mon-progress-card");
  card.classList.add("done-card");
  renderTaskDetail(total, total);

  const keys = monTaskKeys();
  localStorage.removeItem(keys.idKey);
  localStorage.removeItem(keys.metaKey);
  monTaskIds = null;
  const btn = document.getElementById("mon-start");
  btn.disabled = false;
  btn.textContent = "开始监测";

  geoApi("/api/monitor/rounds?page=1").then(function (list) {
    const rounds = list.items || [];
    const r = rounds.find(function (x) {
      return taskIds.some(function (id) { return String(x.task_id) === String(id); });
    }) || rounds[0];
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
  document.getElementById("mon-progress-sub").textContent = "可重新发起一轮监测";
  const card = document.getElementById("mon-progress-card");
  card.classList.add("failed-card");
  const keys = monTaskKeys();
  localStorage.removeItem(keys.idKey);
  localStorage.removeItem(keys.metaKey);
  monTaskIds = null;
  const btn = document.getElementById("mon-start");
  btn.disabled = false;
  btn.textContent = "开始监测";
}

function onTaskFailed(data) {
  document.getElementById("mon-progress-title").textContent = "本轮监测未成功";
  document.getElementById("mon-progress-count").textContent =
    (data.error_msg || "监测过程中出现异常") + "（已完成的部分已保存）";
  document.getElementById("mon-progress-sub").textContent = "可重新发起一轮监测";
  const card = document.getElementById("mon-progress-card");
  card.classList.add("failed-card");
  const keys = monTaskKeys();
  localStorage.removeItem(keys.idKey);
  localStorage.removeItem(keys.metaKey);
  monTaskIds = null;
  const btn = document.getElementById("mon-start");
  btn.disabled = false;
  btn.textContent = "开始监测";
}

/* 离开页面再回来：恢复进行中的监测（仅当前品牌，B2 品牌维度隔离）。
   idKey 存 JSON 数组（一轮多任务）；旧版存的纯数字也兼容 */
function resumeRunningTask() {
  const keys = monTaskKeys();
  const raw = localStorage.getItem(keys.idKey);
  if (!raw) return;
  let ids;
  try {
    ids = JSON.parse(raw);
  } catch (e) {
    ids = [parseInt(raw, 10)];
  }
  if (!Array.isArray(ids)) ids = [parseInt(raw, 10)];
  ids = (ids || []).filter(function (x) { return !!x; });
  if (!ids.length) return;
  const metaRaw = localStorage.getItem(keys.metaKey);
  if (metaRaw) {
    try { monTaskMeta = JSON.parse(metaRaw); } catch (e) { monTaskMeta = null; }
  }
  Promise.all(ids.map(function (id) {
    return geoApi("/api/monitor/tasks/" + id + "/progress");
  })).then(function (tasks) {
    monTaskIds = ids;
    handleAggProgress({ ids: ids, tasks: tasks });
    const anyActive = tasks.some(function (t) {
      return t.status === "pending" || t.status === "running";
    });
    if (anyActive) {
      const btn = document.getElementById("mon-start");
      btn.disabled = true;
      btn.textContent = "监测中…";
      const total = tasks.reduce(function (a, t) { return a + (t.total_calls || 0); }, 0);
      showProgressCard(total);
      pollTasks(ids);
    }
  }).catch(function () {
    localStorage.removeItem(keys.idKey);
    localStorage.removeItem(keys.metaKey);
    monTaskIds = null;
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
        "暂无监测记录",
        "系统会自动保存每次监测结果，便于回看 AI 每次的评价",
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
