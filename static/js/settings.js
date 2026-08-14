/* ============================================================
   设置页（UI 文档 4.6）：品牌信息 + 钥匙状态/测试 + 档位 + 定时 + 费用 + 数据本地说明
   ============================================================ */

const ENGINE_SITES = {
  deepseek: "https://platform.deepseek.com",
  kimi: "https://platform.moonshot.cn",
  doubao: "https://console.volcengine.com/ark",
  qwen: "https://dashscope.console.aliyun.com",
  yuanbao: "https://console.cloud.tencent.com/hunyuan",
};

let setKeys = [];
let setBrands = [];
let setBrandData = {};

let setBrandEditPending = false;

/* 从报告页竞品空引导跳来：#brand → 滚到品牌管理卡并自动打开当前品牌编辑弹窗（B3） */
function triggerBrandEdit() {
  const el = document.getElementById("brand");
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  const cur = setBrandData[getBrandId()];
  if (cur) {
    setTimeout(function () { openBrandEditModal(cur.id); }, 300);
  }
}

function setInit() {
  loadBrands();
  loadKeys();
  loadSchedule();

  document.getElementById("brand-new-btn").addEventListener("click", function () {
    openBrandCreateModal();
  });
  window.onBrandsChanged = function () {
    loadBrands();
  };

  document.getElementById("schedule-enabled").addEventListener("change", function () {
    document.getElementById("schedule-enabled-text").textContent =
      this.checked ? "定时监测已开启" : "定时监测已关闭";
    document.getElementById("schedule-time").disabled = !this.checked;
  });
  document.getElementById("schedule-web-mode").addEventListener("change", function () {
    document.getElementById("schedule-web-cost").classList.toggle("hidden", !this.checked);
  });
  document.getElementById("schedule-save").addEventListener("click", saveSchedule);

  const hash = location.hash;
  if (hash === "#brand-new") {
    setTimeout(function () {
      openBrandCreateModal();
    }, 300);
  } else if (hash === "#brand") {
    setBrandEditPending = true;
  } else if (hash === "#keys" || hash === "#schedule") {
    setTimeout(function () {
      const el = document.getElementById(hash.slice(1));
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 300);
  }
  /* 已在设置页时顶栏「＋ 新建品牌」再次点击（hash 不变不重载）兜底 */
  window.addEventListener("hashchange", function () {
    if (location.hash === "#brand-new") openBrandCreateModal();
    else if (location.hash === "#brand") triggerBrandEdit();
  });
}

/* ---------------- 我的品牌（N1~N5 品牌管理） ---------------- */

function loadBrands() {
  geoApi("/api/brands").then(function (items) {
    setBrands = items || [];
    setBrandData = {};
    setBrands.forEach(function (b) { setBrandData[b.id] = b; });
    renderBrandList();
    updateScheduleBrandCount();
    if (setBrandEditPending) {
      setBrandEditPending = false;
      triggerBrandEdit();
    }
  }).catch(function () {});
}

function renderBrandList() {
  const area = document.getElementById("brand-list");
  const count = setBrands.length;
  document.getElementById("brand-count").textContent = count;

  const newBtn = document.getElementById("brand-new-btn");
  const fullHint = document.getElementById("brand-full-hint");
  newBtn.disabled = count >= 5;
  fullHint.classList.toggle("hidden", count < 5);

  area.innerHTML = "";
  setBrands.forEach(function (b) {
    area.appendChild(renderBrandRow(b));
  });
}

function brandSubLine(b) {
  const product = (b.product_name || "").trim();
  const compCount = (b.competitors || []).length;
  if (product && compCount > 0) return product + " · 竞品 " + compCount + " 家";
  if (product) return product;
  if (compCount > 0) return "竞品 " + compCount + " 家";
  return "";
}

function renderBrandRow(b) {
  const row = document.createElement("div");
  row.className = "brand-row";
  const sub = brandSubLine(b);
  row.innerHTML =
    '<div class="br-main">' +
    '<div class="br-name">' + esc(b.brand_name || "未命名品牌") + "</div>" +
    (sub ? '<div class="br-sub">' + esc(sub) + "</div>" : "") +
    "</div>" +
    '<div class="br-actions">' +
    '<span class="br-auto">参加每日自动监测' +
    '<label class="switch"><input type="checkbox" data-auto="' + b.id + '" ' + (b.auto_monitor ? "checked" : "") + ">" +
    '<span class="slider"></span></label></span>' +
    '<button class="btn-text" data-edit="' + b.id + '">编辑</button>' +
    '<button class="btn-text gray" data-del="' + b.id + '">删除</button>' +
    "</div>";
  row.querySelector('[data-auto]').addEventListener("change", function () {
    const newVal = this.checked;
    saveBrandAutoMonitor(b.id, newVal);
  });
  row.querySelector('[data-edit]').addEventListener("click", function () {
    openBrandEditModal(b.id);
  });
  row.querySelector('[data-del]').addEventListener("click", function () {
    deleteBrand(b.id, b.brand_name || "未命名品牌");
  });
  return row;
}

function saveBrandAutoMonitor(id, newVal) {
  const base = setBrandData[id] || {};
  apiPut("/api/brands/" + id, {
    brand_name: base.brand_name || "",
    product_name: base.product_name || "",
    brand_aliases: base.brand_aliases || [],
    brand_description: base.brand_description || "",
    competitors: base.competitors || [],
    auto_monitor: newVal,
  }).then(function (saved) {
    setBrandData[id] = saved;
    updateScheduleBrandCount();
  }).catch(function () {
    loadBrands();
  });
}

function openBrandEditModal(id) {
  const b = setBrandData[id];
  if (!b) return;
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML =
    '<div class="modal">' +
    '<div class="modal-head">编辑品牌</div>' +
    '<div class="modal-body">' +
    '<div class="field"><label class="field-label">品牌名 <span class="required">*</span></label>' +
    '<input class="input" id="eb-name" placeholder="比如：某某母婴" value="' + esc(b.brand_name || "") + '"></div>' +
    '<div class="field"><label class="field-label">产品名</label>' +
    '<input class="input" id="eb-product" placeholder="比如：智慧教育/系统集成" value="' + esc(b.product_name || "") + '"></div>' +
    '<div class="field"><label class="field-label">品牌别名</label>' +
    '<input class="input" id="eb-aliases" placeholder="AI 回答里可能出现的其他叫法，多个用逗号分开" value="' + esc((b.brand_aliases || []).join(", ")) + '"></div>' +
    '<div class="field"><label class="field-label">一句话介绍</label>' +
    '<textarea class="textarea" id="eb-desc" style="min-height:80px" placeholder="告诉 AI 你是谁，最多 200 字">' + esc(b.brand_description || "") + "</textarea>" +
    '<div class="field-hint" id="eb-desc-count">' + ((b.brand_description || "").length) + "/200 字</div></div>" +
    '<div class="field"><label class="field-label">竞品名单</label>' +
    '<input class="input" id="eb-comp" placeholder="多个用逗号分开，比如：竞品A, 竞品B" value="' + esc((b.competitors || []).join(", ")) + '"></div>' +
    '<div class="field" style="display:flex;align-items:center;gap:10px">' +
    '<label class="switch"><input type="checkbox" id="eb-auto" ' + (b.auto_monitor ? "checked" : "") + "><span class=\"slider\"></span></label>" +
    '<span style="font-weight:600">参加每日自动监测</span></div>' +
    '<div class="small-note">打开：每天早上的自动监测会带上它；关闭：自动监测跳过它。</div>' +
    "</div>" +
    '<div class="modal-foot">' +
    '<button class="btn btn-secondary" data-act="cancel">取消</button>' +
    '<button class="btn btn-primary" data-act="save">保存</button>' +
    "</div></div>";
  document.body.appendChild(mask);

  const nameInput = mask.querySelector("#eb-name");
  const descInput = mask.querySelector("#eb-desc");
  nameInput.addEventListener("input", function () {
    if (this.value.length > 50) {
      this.value = this.value.slice(0, 50);
      showToast("品牌名太长了，请控制在 50 字以内", "error");
    }
  });
  descInput.addEventListener("input", function () {
    const n = this.value.length;
    mask.querySelector("#eb-desc-count").textContent = n + "/200 字";
    if (n > 200) {
      this.value = this.value.slice(0, 200);
      mask.querySelector("#eb-desc-count").textContent = "200/200 字";
    }
  });

  mask.addEventListener("click", function (e) {
    if (e.target === mask) close();
  });
  mask.querySelector('[data-act="cancel"]').addEventListener("click", close);
  mask.querySelector('[data-act="save"]').addEventListener("click", function () {
    const btn = mask.querySelector('[data-act="save"]');
    const name = nameInput.value.trim();
    if (!name) {
      showToast("请先填品牌名，这是监测的基础", "error");
      return;
    }
    if (name.length > 50) {
      showToast("品牌名太长了，请控制在 50 字以内", "error");
      return;
    }
    btn.disabled = true;
    apiPut("/api/brands/" + id, {
      brand_name: name,
      product_name: mask.querySelector("#eb-product").value.trim(),
      brand_aliases: mask.querySelector("#eb-aliases").value,
      brand_description: descInput.value.trim(),
      competitors: mask.querySelector("#eb-comp").value,
      auto_monitor: mask.querySelector("#eb-auto").checked,
    }).then(function (saved) {
      close();
      showToast("品牌「" + (saved.brand_name || name) + "」的信息已保存", "success");
      loadBrands();
      refreshBrandSwitcher();
    }).catch(function () {
      btn.disabled = false;
    });
  });

  nameInput.focus();

  function close() {
    mask.remove();
  }
}

function deleteBrand(id, name) {
  confirmDialog(
    "删除「" + name + "」后，它的问题、监测记录、报告、预警等全部数据会一起删除，删了就找不回来了。确定要删除吗？",
    function () {
      apiDelete("/api/brands/" + id, { confirm: true }).then(function () {
        if (getBrandId() === id) {
          /* 当前品牌被删：回落其余品牌后刷新，Toast 用 sessionStorage 标记刷新后展示 */
          const remaining = setBrands.filter(function (b) { return b.id !== id; });
          if (remaining.length) {
            let minId = remaining[0].id;
            remaining.forEach(function (b) { if (b.id < minId) minId = b.id; });
            setBrandId(minId);
          } else {
            clearBrandId();
          }
          sessionStorage.setItem("geo_brand_deleted_toast", name);
          location.reload();
        } else {
          showToast("已删除品牌「" + name + "」及其全部数据", "success");
          loadBrands();
          refreshBrandSwitcher();
        }
      }).catch(function () {});
    },
    { title: "删除品牌", okText: "确认删除", danger: true }
  );
}

/* ---------------- 各家 AI 的钥匙和档位（整合卡） ---------------- */

function loadKeys() {
  geoApi("/api/settings/keys").then(function (items) {
    setKeys = items || [];
    const engines = setKeys.filter(function (k) { return k.engine !== "analysis"; });
    const anyConfigured = engines.some(function (k) { return k.configured; });
    const banner = document.getElementById("keys-no-key-banner");
    banner.classList.toggle("hidden", anyConfigured);

    const area = document.getElementById("keys-list");
    area.innerHTML = "";
    setKeys.forEach(function (k) {
      area.appendChild(renderKeyTierRow(k));
    });
  }).catch(function () {});
}

function renderKeyTierRow(k) {
  const row = document.createElement("div");
  row.className = "engine-row kt-row";
  const masked = k.api_key_masked || "";
  const keyPlaceholder = k.configured
    ? "已填（" + masked + "），填新值可更换，留空保持原样"
    : "粘贴你的钥匙（API Key）";
  const statusHtml = k.configured
    ? '<span class="tag tag-green"><span class="key-status-dot" style="background:var(--success)"></span>钥匙已填 ✔</span>'
    : '<span class="tag tag-orange"><span class="key-status-dot" style="background:var(--warn)"></span>钥匙未填</span>';
  const testHtml = k.configured
    ? '<button class="btn-text" data-test="' + esc(k.engine) + '">测试一下</button>'
    : '<a class="btn-text" href="' + (ENGINE_SITES[k.engine] || "#") + '" target="_blank" rel="noopener">去平台拿钥匙 →</a>';
  const isAnalysis = k.engine === "analysis";
  const opts = k.model_options || [];
  const optionsHtml = opts.map(function (o) {
    return '<option value="' + esc(o.name) + '"' + (o.name === k.model ? " selected" : "") + ">" +
      esc(o.name) + "</option>";
  }).join("");
  const curDesc = (opts.find(function (o) { return o.name === k.model; }) || {}).desc || "";
  /* 折叠效果：已配置的默认收起，未配置的默认展开引导填钥匙 */
  const collapsed = !!k.configured;

  let tierHtml;
  if (isAnalysis) {
    /* 分析用模型：厂商可切换（复用各家引擎钥匙/地址/档位） */
    const vendors = k.vendors || [];
    const vendorOptions = vendors.map(function (v) {
      return '<option value="' + esc(v.engine) + '"' + (v.engine === k.vendor ? " selected" : "") + ">" +
        esc(v.display_name) + "</option>";
    }).join("");
    tierHtml =
      '<div class="kt-tier">' +
      '<span class="small-note" style="flex:none">模型厂商：</span>' +
      '<select class="select" data-avendor style="flex:1;min-width:180px">' + vendorOptions + "</select>" +
      "</div>" +
      '<div class="kt-tier">' +
      '<span class="small-note" style="flex:none">模型型号：</span>' +
      '<select class="select" data-tier="analysis" style="flex:1;min-width:260px">' + optionsHtml + "</select>" +
      '<span class="small-note" data-tier-desc style="flex:1">' + esc(curDesc || "") + "</span>" +
      "</div>";
  } else {
    /* 监测引擎：型号选择已移到监测中心（同 key 可多模型勾选） */
    tierHtml =
      '<div class="kt-tier">' +
      '<span class="small-note">模型档位在「监测中心」选择（同一把钥匙可同时勾选多个档位）</span>' +
      "</div>";
  }

  row.innerHTML =
    '<div class="kt-main kt-collapse-head" data-collapse="' + esc(k.engine) + '">' +
    '<span class="kt-arrow">' + (collapsed ? "▸" : "▾") + "</span>" +
    '<span class="en-name">' + esc(k.display_name) + "</span>" +
    statusHtml +
    '<span class="en-actions">' + testHtml + "</span>" +
    "</div>" +
    '<div class="kt-fields' + (collapsed ? " hidden" : "") + '">' +
    '<div class="kt-key">' +
    '<input type="password" class="input" data-key-input="' + esc(k.engine) + '"' +
    ' placeholder="' + esc(keyPlaceholder) + '" autocomplete="off" style="flex:1;min-width:220px">' +
    '<button class="btn btn-secondary" data-key-save="' + esc(k.engine) + '">保存钥匙</button>' +
    "</div>" +
    tierHtml +
    "</div>";

  const testBtn = row.querySelector("[data-test]");
  if (testBtn) {
    testBtn.addEventListener("click", function () {
      testKey(k.engine, testBtn);
    });
  }
  row.querySelector("[data-key-save]").addEventListener("click", function () {
    saveKey(k.engine, row.querySelector("[data-key-input]"), this);
  });

  if (isAnalysis) {
    const tierSel = row.querySelector("[data-tier]");
    const saveAnalysisTier = function () {
      apiPost("/api/settings", { analysis_model: tierSel.value }).then(function () {
        const vm = k.vendor_model_options || {};
        const pool = vm[k.vendor] || k.model_options || [];
        const desc = (pool.find(function (o) { return o.name === tierSel.value; }) || {}).desc || "";
        row.querySelector("[data-tier-desc]").textContent = desc;
        showToast("已切换分析模型为「" + tierSel.value + "」", "success");
      }).catch(function () { loadKeys(); });
    };
    tierSel.addEventListener("change", saveAnalysisTier);
    const vendorSel = row.querySelector("[data-avendor]");
    vendorSel.addEventListener("change", function () {
      apiPost("/api/settings", { analysis_vendor: vendorSel.value }).then(function () {
        k.vendor = vendorSel.value;
        const vm = k.vendor_model_options || {};
        const newOpts = vm[vendorSel.value] || [];
        tierSel.innerHTML = newOpts.map(function (o) {
          return '<option value="' + esc(o.name) + '">' + esc(o.name) + "</option>";
        }).join("");
        if (newOpts.length) {
          tierSel.value = newOpts[0].name;
          saveAnalysisTier();
        }
        showToast("已切换分析模型厂商，型号已同步为该厂商第一档", "success");
      }).catch(function () { loadKeys(); });
    });
  }

  /* 折叠交互：点击引擎名标题展开/收起配置区（点测试/链接不触发） */
  row.querySelector("[data-collapse]").addEventListener("click", function (e) {
    if (e.target.closest("button,a")) return;
    const fields = row.querySelector(".kt-fields");
    fields.classList.toggle("hidden");
    row.querySelector(".kt-arrow").textContent =
      fields.classList.contains("hidden") ? "▸" : "▾";
  });
  return row;
}

function saveKey(code, input, btn) {
  const key = (input.value || "").trim();
  if (!key) {
    showToast("先粘贴一把钥匙再点保存", "error");
    input.focus();
    return;
  }
  btn.disabled = true;
  apiPost("/api/settings/keys", { engine_code: code, api_key: key }).then(function () {
    input.value = "";
    btn.disabled = false;
    showToast("钥匙已保存，保存后立即生效", "success");
    loadKeys();
  }).catch(function () {
    btn.disabled = false;
  });
}

function testKey(code, btn) {
  const oldText = btn.textContent;
  btn.textContent = "测试中…";
  btn.disabled = true;
  apiPost("/api/settings/keys/test", { engine_code: code })
    .then(function (data) {
      if (data.ok) {
        showToast("✔ 通了！这把钥匙没问题", "success");
      } else {
        showToast("不通：" + (data.message || "可能钥匙填错了，或平台临时抽风，等几分钟再试"), "error");
      }
      btn.textContent = oldText;
      btn.disabled = false;
    })
    .catch(function () {
      btn.textContent = oldText;
      btn.disabled = false;
    });
}

document.addEventListener("DOMContentLoaded", function () {
  const jump = document.getElementById("keys-jump-first");
  if (jump) {
    jump.addEventListener("click", function () {
      const firstRow = document.querySelector("#keys-list .engine-row");
      if (firstRow) firstRow.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }
});

/* ---------------- 定时 ---------------- */

function updateScheduleBrandCount() {
  const el = document.getElementById("schedule-brand-count");
  if (!el) return;
  const x = setBrands.filter(function (b) { return b.auto_monitor; }).length;
  if (x >= 2) {
    el.textContent = "监测 " + x + " 个品牌，费用大约是单品牌的 " + x + " 倍。";
    el.classList.remove("hidden");
  } else {
    el.classList.add("hidden");
  }
}

function loadSchedule() {
  geoApi("/api/schedule").then(function (s) {
    document.getElementById("schedule-enabled").checked = !!s.enabled;
    document.getElementById("schedule-time").value = s.time || "08:30";
    document.getElementById("schedule-time").disabled = !s.enabled;
    document.getElementById("schedule-enabled-text").textContent =
      s.enabled ? "定时监测已开启" : "定时监测已关闭";
    const webMode = !!s.web_mode;
    document.getElementById("schedule-web-mode").checked = webMode;
    document.getElementById("schedule-web-cost").classList.toggle("hidden", !webMode);
    document.getElementById("schedule-next").textContent = s.next_run_time
      ? "下次自动监测：" + s.next_run_time.slice(0, 16).replace("T", " ")
      : "下次自动监测：定时未开启时不会自动监测";
  }).catch(function () {});
}

function saveSchedule() {
  const enabled = document.getElementById("schedule-enabled").checked;
  const time = document.getElementById("schedule-time").value || "08:30";
  const webMode = document.getElementById("schedule-web-mode").checked;
  const btn = document.getElementById("schedule-save");
  btn.disabled = true;
  apiPut("/api/schedule", { enabled: enabled, time: time, web_mode: webMode }).then(function (s) {
    btn.disabled = false;
    const hint = document.getElementById("schedule-saved-hint");
    hint.classList.remove("hidden");
    setTimeout(function () { hint.classList.add("hidden"); }, 3000);
    document.getElementById("schedule-next").textContent = s.next_run_time
      ? "下次自动监测：" + s.next_run_time.slice(0, 16).replace("T", " ")
      : "下次自动监测：定时未开启时不会自动监测";
    showToast("定时设置已保存", "success");
  }).catch(function () {
    btn.disabled = false;
    loadSchedule();
  });
}

/* ---------------- 本月费用 ---------------- */

function loadCost() {
  geoApi("/api/settings/cost").then(function (data) {
    const area = document.getElementById("cost-area");
    const total = data.month_cost_yuan || 0;
    const byEngine = data.by_engine || {};

    if (!Object.keys(byEngine).length) {
      area.appendChild(emptyState(
        "还没有监测记录",
        "跑一轮后这里会显示花费",
        "",
        null
      ));
      return;
    }

    area.innerHTML =
      '<div class="score-number" style="color:var(--primary)">¥' + total.toFixed(2) + "</div>" +
      '<div class="score-sub">本月合计（估算）</div>' +
      '<div class="mt-8 score-sub">' +
      Object.keys(byEngine).map(function (code) {
        return esc(byEngine[code].name || code) + " ¥" + (byEngine[code].cost || 0).toFixed(2);
      }).join(" · ") +
      "</div>";
  }).catch(function () {});
}

initNav("settings");
bindConcepts(document);
setInit();
loadCost();
