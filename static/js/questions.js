/* ============================================================
   问题库页（03b 第五章）：关键词扩展（direction）+ 分组管理 + 批量操作 + 行内编辑 + 参与监测开关
   数据源：M3~M7（问题库 CRUD / expand）+ N6~N10（分组 / 批量）
   ============================================================ */

let qbank = [];
let qbankGroups = [];
let qbankFilter = "__all__";
let qbankSelected = {};
let qbankExpanded = [];
let qbankEditingId = null;

function qInit() {
  document.getElementById("exp-generate").addEventListener("click", expGenerate);
  document.getElementById("exp-regenerate").addEventListener("click", expGenerate);
  document.getElementById("exp-add").addEventListener("click", expAddToBank);

  document.getElementById("qbank-add-btn").addEventListener("click", openAddModal);
  document.getElementById("add-cancel").addEventListener("click", function () {
    document.getElementById("add-mask").classList.add("hidden");
  });
  document.getElementById("add-mask").addEventListener("click", function (e) {
    if (e.target === this) this.classList.add("hidden");
  });
  document.getElementById("add-save").addEventListener("click", addToBank);

  document.getElementById("qbank-search").addEventListener("input", renderQBank);
  document.getElementById("qbank-select-all").addEventListener("change", onSelectAll);

  document.getElementById("group-enable-all").addEventListener("click", function () { groupToggleAll("enable"); });
  document.getElementById("group-disable-all").addEventListener("click", function () { groupToggleAll("disable"); });

  document.getElementById("batch-move").addEventListener("click", function (e) {
    e.stopPropagation();
    openGroupPicker(e.currentTarget, function (g) { batchMove(g); });
  });
  document.getElementById("batch-enable").addEventListener("click", function () { batchToggle("enable"); });
  document.getElementById("batch-disable").addEventListener("click", function () { batchToggle("disable"); });
  document.getElementById("batch-delete").addEventListener("click", batchDelete);
  document.getElementById("batch-clear").addEventListener("click", clearSelection);

  /* 点击弹层外关闭组菜单/组选择小菜单 */
  document.addEventListener("click", function (e) {
    const inside = (groupMenuEl && groupMenuEl.contains(e.target)) ||
      (groupPickerEl && groupPickerEl.contains(e.target));
    if (!inside) closePopups();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closePopups();
  });

  loadQBank();
  loadGroups();
}

/* ---------------- 关键词扩展 ---------------- */

function expGenerate() {
  const input = document.getElementById("exp-keywords").value.trim();
  if (!input) {
    showToast("请先填写至少一个关键词，才能扩展问法", "error");
    return;
  }
  const btn = document.getElementById("exp-generate");
  btn.disabled = true;
  btn.textContent = "生成中…";
  document.getElementById("exp-waiting").classList.remove("hidden");
  document.getElementById("exp-result").classList.add("hidden");
  document.getElementById("exp-empty-hint").classList.add("hidden");

  apiPost("/api/questions/expand", {
    keywords: input,
    direction: document.getElementById("exp-direction").value.trim(),
  })
    .then(function (data) {
      qbankExpanded = (data.questions || []).map(function (q) {
        return { text: q, checked: true };
      });
      renderExpanded();
      btn.disabled = false;
      btn.textContent = "生成问法";
    })
    .catch(function () {
      btn.disabled = false;
      btn.textContent = "生成问法";
      document.getElementById("exp-waiting").classList.add("hidden");
      document.getElementById("exp-empty-hint").classList.remove("hidden");
    });
}

function renderExpanded() {
  document.getElementById("exp-waiting").classList.add("hidden");
  const area = document.getElementById("exp-grid");
  area.innerHTML = "";
  qbankExpanded.forEach(function (item, i) {
    const label = document.createElement("label");
    label.className = "gen-item";
    label.innerHTML =
      '<input type="checkbox" data-i="' + i + '" checked>' +
      '<span>' + esc(item.text) + "</span>";
    label.querySelector("input").addEventListener("change", function () {
      item.checked = this.checked;
      updateExpAddBtn();
    });
    area.appendChild(label);
  });
  updateExpAddBtn();
  document.getElementById("exp-result").classList.remove("hidden");
}

function updateExpAddBtn() {
  const n = qbankExpanded.filter(function (x) { return x.checked; }).length;
  document.getElementById("exp-add").textContent = "加入问题库(" + n + ")";
}

function expAddToBank() {
  const texts = qbankExpanded.filter(function (x) { return x.checked; }).map(function (x) { return x.text; });
  if (!texts.length) return;
  const btn = document.getElementById("exp-add");
  btn.disabled = true;
  /* 后端问题库没有批量新增能力，逐个添加 */
  let done = 0;
  const errors = [];
  const queue = texts.slice();
  function next() {
    if (!queue.length) {
      btn.disabled = false;
      if (errors.length) {
        showToast("已加入 " + done + " 个问题到问题库（有 " + errors.length + " 个没加上：请检查内容是否为空）", "success");
      } else {
        showToast("已加入 " + done + " 个问题到问题库", "success");
      }
      loadQBank();
      loadGroups();
      return;
    }
    const text = queue.shift();
    apiPost("/api/questions", { text: text, category: "", source: "expanded" })
      .then(function () {
        done += 1;
        next();
      })
      .catch(function () {
        errors.push(text);
        next();
      });
  }
  next();
}

/* ---------------- 占位符提示（{品类} 需替换后再监测） ---------------- */

function hasPlaceholderQuestion(list) {
  return (list || []).some(function (q) { return (q.text || "").indexOf("{品类}") >= 0; });
}

function placeholderHint() {
  return '<div class="small-note mt-8" style="margin-bottom:4px">含{品类}的问题，监测前请将{品类}替换为你的实际产品</div>';
}

/* ---------------- 分组（N6）与组标签行 ---------------- */

function loadGroups() {
  geoApi("/api/question-groups").then(function (items) {
    qbankGroups = items || [];
    renderGroupTabs();
    renderGroupHead();
    renderAddGroupSelect();
  }).catch(function () {});
}

function renderGroupTabs() {
  const area = document.getElementById("qbank-groups");
  area.innerHTML = "";
  const total = qbankGroups.reduce(function (s, g) { return s + (g.question_count || 0); }, 0);
  addGroupTab(area, "__all__", "全部", total, false);
  const ug = qbankGroups.find(function (g) { return g.name === ""; });
  if (ug) addGroupTab(area, "", "未分组", ug.question_count || 0, true);
  qbankGroups.forEach(function (g) {
    if (g.name) addGroupTab(area, g.name, g.name, g.question_count || 0, false);
  });
  const link = document.createElement("span");
  link.className = "gt-new";
  link.textContent = "＋ 新建组";
  link.addEventListener("click", openCreateGroupModal);
  area.appendChild(link);
}

function addGroupTab(area, name, label, count, isUngrouped) {
  const tab = document.createElement("span");
  tab.className = "gt" + (qbankFilter === name ? " active" : "") + (isUngrouped ? " gt-locked" : "");
  tab.appendChild(document.createTextNode(label + "（" + count + "）"));
  if (isUngrouped) {
    const tip = document.createElement("span");
    tip.className = "gt-tip";
    tip.textContent = "「未分组」是默认分组，不能改名或删除";
    tab.appendChild(tip);
  }
  tab.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest(".gm-btn")) return;
    qbankFilter = name;
    renderGroupTabs();
    renderGroupHead();
    renderQBank();
  });
  if (!isUngrouped) {
    const btn = document.createElement("span");
    btn.className = "gm-btn";
    btn.textContent = "管理";
    btn.title = "管理这个分组：重命名或删除";
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      openGroupMenu(btn, name);
    });
    tab.appendChild(btn);
  }
  area.appendChild(tab);
}

function renderGroupHead() {
  const head = document.getElementById("group-view-head");
  if (qbankFilter === "__all__") {
    head.classList.add("hidden");
    return;
  }
  const g = qbankGroups.find(function (x) { return x.name === qbankFilter; }) || { question_count: 0, enabled_count: 0 };
  document.getElementById("group-view-info").textContent =
    "本组共 " + (g.question_count || 0) + " 条 · 已参加 " + (g.enabled_count || 0) + " 条";
  head.classList.remove("hidden");
}

/* ---------------- 组菜单 / 组选择小菜单 ---------------- */

let groupMenuEl = null;
let groupPickerEl = null;

function closePopups() {
  if (groupMenuEl) { groupMenuEl.remove(); groupMenuEl = null; }
  if (groupPickerEl) { groupPickerEl.remove(); groupPickerEl = null; }
}

function positionPopup(menu, anchor) {
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth || 140;
  let left = r.left;
  if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
  menu.style.left = Math.max(8, left) + "px";
  menu.style.top = (r.bottom + 4) + "px";
}

function openGroupMenu(anchor, groupName) {
  closePopups();
  const menu = document.createElement("div");
  menu.className = "group-menu";
  [["重命名", function () { openRenameGroupModal(groupName); }],
   ["删除", function () { openDeleteGroupModal(groupName); }]
  ].forEach(function (it) {
    const row = document.createElement("div");
    row.className = "group-menu-item";
    row.textContent = it[0];
    row.addEventListener("click", function () {
      closePopups();
      it[1]();
    });
    menu.appendChild(row);
  });
  positionPopup(menu, anchor);
  document.body.appendChild(menu);
  groupMenuEl = menu;
}

function openGroupPicker(anchor, onPick) {
  closePopups();
  const menu = document.createElement("div");
  menu.className = "group-picker";
  const groups = [{ name: "", label: "未分组" }].concat(
    qbankGroups.filter(function (g) { return g.name; })
      .map(function (g) { return { name: g.name, label: g.name }; })
  );
  groups.forEach(function (it) {
    const row = document.createElement("div");
    row.className = "group-picker-item";
    row.textContent = it.label;
    row.addEventListener("click", function () {
      closePopups();
      onPick(it.name);
    });
    menu.appendChild(row);
  });
  positionPopup(menu, anchor);
  document.body.appendChild(menu);
  groupPickerEl = menu;
}

/* ---------------- 新建组 / 重命名 / 删除组（N7/N8/N9） ---------------- */

function openGroupNameModal(opts) {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML =
    '<div class="modal">' +
    '<div class="modal-head">' + esc(opts.title) + "</div>" +
    '<div class="modal-body">' +
    '<div class="field"><label class="field-label">分组名</label>' +
    '<input class="input" id="gm-name" maxlength="50" placeholder="' + esc(opts.placeholder) + '" value="' + esc(opts.initial || "") + '"></div>' +
    "</div>" +
    '<div class="modal-foot">' +
    '<button class="btn btn-secondary" data-act="cancel">取消</button>' +
    '<button class="btn btn-primary" data-act="ok">' + esc(opts.okText) + "</button>" +
    "</div></div>";
  document.body.appendChild(mask);
  const input = mask.querySelector("#gm-name");
  mask.addEventListener("click", function (e) {
    if (e.target === mask) close();
  });
  mask.querySelector('[data-act="cancel"]').addEventListener("click", close);
  mask.querySelector('[data-act="ok"]').addEventListener("click", function () {
    const btn = mask.querySelector('[data-act="ok"]');
    const name = input.value.trim().slice(0, 50);
    if (!name) {
      showToast("组名不能为空", "error");
      return;
    }
    btn.disabled = true;
    Promise.resolve(opts.onOk(name)).then(function () {
      close();
    }).catch(function () {
      btn.disabled = false;
    });
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") mask.querySelector('[data-act="ok"]').click();
  });
  input.focus();

  function close() {
    mask.remove();
  }
}

function openCreateGroupModal() {
  openGroupNameModal({
    title: "新建分组",
    placeholder: "比如：选购咨询",
    okText: "创建",
    onOk: function (name) {
      return apiPost("/api/question-groups", { name: name }).then(function (d) {
        showToast("已创建分组「" + (d && d.name ? d.name : name) + "」", "success");
        loadGroups();
        loadQBank();
      });
    },
  });
}

function openRenameGroupModal(oldName) {
  openGroupNameModal({
    title: "重命名分组",
    placeholder: "比如：选购咨询",
    initial: oldName,
    okText: "保存",
    onOk: function (newName) {
      return apiPut("/api/question-groups", { name: oldName, new_name: newName }).then(function (d) {
        showToast("分组已改名为「" + (d && d.name ? d.name : newName) + "」", "success");
        if (qbankFilter === oldName) qbankFilter = (d && d.name) || newName;
        loadGroups();
        loadQBank();
      });
    },
  });
}

function openDeleteGroupModal(groupName) {
  const g = qbankGroups.find(function (x) { return x.name === groupName; });
  const n = g ? (g.question_count || 0) : 0;
  confirmDialog("删除「" + groupName + "」后，里面的 " + n + " 条问题会回到「未分组」，一条都不会丢。确定要删除吗？", function () {
    apiDelete("/api/question-groups", { name: groupName, confirm: true }).then(function (d) {
      showToast("已删除分组「" + groupName + "」，" + (d && d.affected != null ? d.affected : 0) + " 条问题已回到「未分组」", "success");
      if (qbankFilter === groupName) qbankFilter = "__all__";
      loadGroups();
      loadQBank();
    }).catch(function () {});
  }, { title: "删除分组", okText: "确认删除", danger: true });
}

/* ---------------- 整组开关（N10，无 ids 按 group 过滤） ---------------- */

function groupToggleAll(action) {
  apiPost("/api/questions/batch", { action: action, group: qbankFilter }).then(function (d) {
    const n = d && d.affected != null ? d.affected : 0;
    showToast("已把 " + n + " 条问题设为" + (action === "enable" ? "参加监测" : "不参加监测"), "success");
    loadGroups();
    loadQBank();
  }).catch(function () {});
}

/* ---------------- 问题库列表 ---------------- */

function loadQBank() {
  geoApi("/api/questions").then(function (items) {
    qbank = items || [];
    document.getElementById("qbank-count").textContent = qbank.length;
    renderQBank();
  }).catch(function () {});
}

function currentViewList() {
  const search = document.getElementById("qbank-search").value.trim().toLowerCase();
  let list = qbank.slice();
  if (qbankFilter === "") list = list.filter(function (q) { return !q.category; });
  else if (qbankFilter !== "__all__") list = list.filter(function (q) { return q.category === qbankFilter; });
  if (search) list = list.filter(function (q) { return (q.text || "").toLowerCase().indexOf(search) >= 0; });
  return list;
}

function selectedIds() {
  return Object.keys(qbankSelected).map(Number);
}

function renderSelectAll() {
  const check = document.getElementById("qbank-select-all");
  const list = currentViewList();
  if (!list.length) {
    check.checked = false;
    check.disabled = true;
    return;
  }
  check.disabled = false;
  check.checked = list.every(function (q) { return !!qbankSelected[q.id]; });
}

function onSelectAll() {
  const check = document.getElementById("qbank-select-all");
  const list = currentViewList();
  list.forEach(function (q) {
    if (check.checked) qbankSelected[q.id] = true;
    else delete qbankSelected[q.id];
  });
  renderQBank();
}

function renderBatchBar() {
  const bar = document.getElementById("batch-bar");
  const n = selectedIds().length;
  if (n === 0) {
    bar.classList.add("hidden");
    return;
  }
  document.getElementById("batch-n").textContent = n;
  bar.classList.remove("hidden");
}

function clearSelection() {
  qbankSelected = {};
  renderQBank();
}

function renderQBank() {
  const area = document.getElementById("qbank-list");
  const search = document.getElementById("qbank-search").value.trim().toLowerCase();
  area.innerHTML = "";

  if (!qbank.length) {
    area.appendChild(emptyState(
      "问题库是空的",
      "用上面的关键词扩展问法，或点「手动添加」直接写一条，就能开始监测了。",
      "用关键词扩展问法",
      function () {
        const input = document.getElementById("exp-keywords");
        if (input) {
          input.scrollIntoView({ behavior: "smooth", block: "center" });
          input.focus();
        }
      },
      [{
        text: "手动添加",
        onClick: function () { openAddModal(); },
      }]
    ));
    renderBatchBar();
    renderSelectAll();
    return;
  }

  const list = currentViewList();

  if (qbankEditingId !== null && !list.some(function (q) { return q.id === qbankEditingId; })) {
    qbankEditingId = null;
  }

  if (!list.length) {
    if (!search) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.innerHTML =
        '<div class="empty-icon">?</div>' +
        '<div class="empty-title">这个组里还没有问题，可以把别的问题移进来。</div>';
      area.appendChild(empty);
    } else {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.innerHTML =
        '<div class="empty-icon">?</div>' +
        '<div class="empty-title">没有找到相关的问题</div>' +
        '<div class="empty-desc">换个搜索词试试，或换个组看看。</div>';
      area.appendChild(empty);
    }
    renderBatchBar();
    renderSelectAll();
    return;
  }

  if (hasPlaceholderQuestion(list)) {
    const hint = document.createElement("div");
    hint.innerHTML = placeholderHint();
    area.appendChild(hint.firstElementChild);
  }

  list.forEach(function (q) {
    const row = document.createElement("div");
    row.className = "list-row qb-row" + (qbankSelected[q.id] ? " selected" : "");
    if (!q.enabled) row.style.opacity = "0.55";

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "qb-check";
    check.checked = !!qbankSelected[q.id];
    check.addEventListener("change", function () {
      if (check.checked) qbankSelected[q.id] = true;
      else delete qbankSelected[q.id];
      row.classList.toggle("selected", check.checked);
      renderBatchBar();
      renderSelectAll();
    });
    row.appendChild(check);

    const main = document.createElement("div");
    main.className = "row-main";
    row.appendChild(main);
    renderRowMain(main, q);

    const act = document.createElement("span");
    act.className = "qb-actions";
    act.innerHTML =
      '<span style="font-size:13px;color:var(--text-sub)">参与监测</span>' +
      '<label class="switch"><input type="checkbox" data-qid="' + q.id + '" ' + (q.enabled ? "checked" : "") + ">" +
      '<span class="slider"></span></label>';
    row.appendChild(act);

    const editBtn = document.createElement("button");
    editBtn.className = "btn-text";
    editBtn.textContent = "编辑";
    editBtn.addEventListener("click", function () {
      qbankEditingId = qbankEditingId === q.id ? null : q.id;
      renderRowMain(main, q);
    });
    row.appendChild(editBtn);

    const moveBtn = document.createElement("button");
    moveBtn.className = "btn-text gray";
    moveBtn.textContent = "移动";
    moveBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openGroupPicker(moveBtn, function (g) { singleMove(q, g); });
    });
    row.appendChild(moveBtn);

    const delBtn = document.createElement("button");
    delBtn.className = "btn-text";
    delBtn.textContent = "删除";
    delBtn.addEventListener("click", function () {
      const summary = (q.text || "").length > 20 ? q.text.slice(0, 20) + "…" : q.text;
      confirmDialog("确定要删除「" + summary + "」吗？删除后无法找回。", function () {
        apiDelete("/api/questions/" + q.id).then(function () {
          showToast("已删除", "success");
          loadQBank();
          loadGroups();
        }).catch(function () {});
      }, { title: "删除问题", okText: "确认删除", danger: true });
    });
    row.appendChild(delBtn);

    act.querySelector('input[type="checkbox"]').addEventListener("change", function () {
      const newVal = this.checked;
      apiPut("/api/questions/" + q.id, { enabled: newVal }).then(function (updated) {
        q.enabled = updated ? updated.enabled : newVal;
        row.style.opacity = q.enabled ? "1" : "0.55";
        showToast(newVal ? "已开启参与监测" : "已关闭参与监测，它不会再出现在监测中心的勾选列表里", "success");
      }).catch(function () {
        this.checked = !newVal;
      }.bind(this));
    });

    area.appendChild(row);
  });

  renderBatchBar();
  renderSelectAll();
}

function renderRowMain(main, q) {
  main.innerHTML = "";
  if (qbankEditingId === q.id) {
    const wrap = document.createElement("div");
    wrap.className = "inline-edit-row";
    const input = document.createElement("input");
    input.className = "input inline-edit-input";
    input.value = q.text;
    const save = document.createElement("button");
    save.className = "btn btn-primary btn-sm";
    save.textContent = "保存";
    const cancel = document.createElement("button");
    cancel.className = "btn-text gray";
    cancel.textContent = "取消";
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") save.click();
      if (e.key === "Escape") cancel.click();
    });
    save.addEventListener("click", function () {
      const text = input.value.trim();
      if (!text) {
        showToast("问题内容不能为空", "error");
        return;
      }
      save.disabled = true;
      apiPut("/api/questions/" + q.id, { text: text }).then(function (updated) {
        q.text = (updated && updated.text) || text;
        qbankEditingId = null;
        showToast("问题已更新", "success");
        renderRowMain(main, q);
      }).catch(function () {
        save.disabled = false;
      });
    });
    cancel.addEventListener("click", function () {
      qbankEditingId = null;
      renderRowMain(main, q);
    });
    wrap.appendChild(input);
    wrap.appendChild(save);
    wrap.appendChild(cancel);
    main.appendChild(wrap);
    input.focus();
    return;
  }

  const textDiv = document.createElement("div");
  textDiv.style.fontSize = "14px";
  textDiv.textContent = q.text;
  main.appendChild(textDiv);

  const tags = document.createElement("div");
  tags.className = "mt-8";
  let html = "";
  if (qbankFilter === "__all__" && q.category) {
    html += '<span class="tag tag-primary">' + esc(q.category) + "</span> ";
  }
  html += '<span class="tag tag-gray">' + sourceText(q.source) + "</span>";
  tags.innerHTML = html;
  main.appendChild(tags);
}

/* ---------------- 批量操作（N10） ---------------- */

function batchMove(groupName) {
  const ids = selectedIds();
  if (!ids.length) return;
  apiPost("/api/questions/batch", { action: "move", ids: ids, group: groupName }).then(function (d) {
    showToast("已把 " + (d && d.affected != null ? d.affected : ids.length) + " 条问题移到「" + (groupName || "未分组") + "」", "success");
    clearSelection();
    loadGroups();
    loadQBank();
  }).catch(function () {});
}

function batchToggle(action) {
  const ids = selectedIds();
  if (!ids.length) return;
  apiPost("/api/questions/batch", { action: action, ids: ids }).then(function (d) {
    showToast("已把 " + (d && d.affected != null ? d.affected : ids.length) + " 条问题设为" + (action === "enable" ? "参加监测" : "不参加监测"), "success");
    clearSelection();
    loadQBank();
  }).catch(function () {});
}

function batchDelete() {
  const ids = selectedIds();
  if (!ids.length) return;
  confirmDialog("将删除 " + ids.length + " 条问题，删了找不回来。确定要删除吗？", function () {
    apiPost("/api/questions/batch", { action: "delete", ids: ids, confirm: true }).then(function (d) {
      showToast("已删除 " + (d && d.affected != null ? d.affected : ids.length) + " 条问题", "success");
      clearSelection();
      loadGroups();
      loadQBank();
    }).catch(function () {});
  }, { title: "删除问题", okText: "确认删除", danger: true });
}

/* ---------------- 单条移动（M5 category） ---------------- */

function singleMove(q, groupName) {
  apiPut("/api/questions/" + q.id, { category: groupName }).then(function () {
    q.category = groupName || null;
    showToast("已把这个问题移到「" + (groupName || "未分组") + "」", "success");
    loadGroups();
    renderQBank();
  }).catch(function () {});
}

/* ---------------- 手动添加（M4，含所属组下拉） ---------------- */

function renderAddGroupSelect() {
  const sel = document.getElementById("add-category");
  sel.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "未分组";
  sel.appendChild(empty);
  qbankGroups.forEach(function (g) {
    if (!g.name) return;
    const o = document.createElement("option");
    o.value = g.name;
    o.textContent = g.name;
    sel.appendChild(o);
  });
}

function openAddModal() {
  document.getElementById("add-text").value = "";
  document.getElementById("add-category").value = "";
  document.getElementById("add-mask").classList.remove("hidden");
  document.getElementById("add-text").focus();
}

function addToBank() {
  const text = document.getElementById("add-text").value.trim();
  const category = document.getElementById("add-category").value;
  if (!text) {
    showToast("问题内容不能为空", "error");
    return;
  }
  const btn = document.getElementById("add-save");
  btn.disabled = true;
  apiPost("/api/questions", { text: text, category: category })
    .then(function () {
      btn.disabled = false;
      document.getElementById("add-mask").classList.add("hidden");
      showToast("已添加 1 条问题", "success");
      loadQBank();
      loadGroups();
    })
    .catch(function () {
      btn.disabled = false;
    });
}

initNav("questions");
bindConcepts(document);
qInit();
