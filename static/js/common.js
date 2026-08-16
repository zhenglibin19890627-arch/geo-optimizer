/* ============================================================
   GEO 优化系统 - 公共组件（导航栏含预警红点、首次使用三步引导）
   ============================================================ */

const NAV_ITEMS = [
  { key: "index", label: "首页", href: "/static/index.html" },
  { key: "optimize", label: "内容优化", href: "/static/optimize.html" },
  { key: "monitor", label: "监测中心", href: "/static/monitor.html" },
  { key: "report", label: "报告", href: "/static/report.html" },
  { key: "questions", label: "问题库", href: "/static/questions.html" },
  { key: "settings", label: "设置", href: "/static/settings.html" },
];

function initNav(active) {
  const navbar = document.createElement("nav");
  navbar.className = "navbar";
  const items = NAV_ITEMS.map(function (it) {
    const cls = it.key === active ? "nav-item active" : "nav-item";
    const dot = it.key === "index" ? '<span class="nav-dot" id="nav-dot"></span>' : "";
    return '<a class="' + cls + '" href="' + it.href + '">' + it.label + dot + "</a>";
  }).join("");
  navbar.innerHTML =
    '<div class="navbar-inner">' +
    '<a class="nav-brand" href="/static/index.html">GEO 优化系统</a>' +
    '<div class="nav-links">' + items + "</div>" +
    '<div class="brand-switch-wrap" id="brand-switch-wrap"></div>' +
    "</div>";
  document.body.insertBefore(navbar, document.body.firstChild);

  /* /api/alerts 已支持 unread=true 参数（后端 04-9.2 已修复）；此处取全量后由前端筛选未读，未读逻辑与参数写法等效 */
  geoApi("/api/alerts").then(function (data) {
    const dot = document.getElementById("nav-dot");
    const unread = (data.items || []).filter(function (a) { return !a.is_read; }).length;
    if (dot && unread > 0) {
      dot.classList.add("show");
    }
  }).catch(function () {});

  initBrandSwitcher();
}

/* ============================================================
   顶栏品牌切换器（03b 第三章：常驻胶囊 + 下拉面板）
   数据源 N1 GET /api/brands；切换 = 写 localStorage + 刷新当前页
   ============================================================ */

let switcherBrands = [];
let switcherDropdownOpen = false;

function initBrandSwitcher() {
  const wrap = document.getElementById("brand-switch-wrap");
  if (!wrap) return;
  geoApi("/api/brands").then(function (items) {
    switcherBrands = items || [];
    validateCurrentBrand(switcherBrands);
    renderBrandSwitcher();
    showSwitchToastIfAny();
    if (window.onBrandsLoaded) window.onBrandsLoaded();
  }).catch(function () {
    wrap.innerHTML = "";
  });
}

function refreshBrandSwitcher() {
  const wrap = document.getElementById("brand-switch-wrap");
  if (!wrap) return;
  geoApi("/api/brands").then(function (items) {
    switcherBrands = items || [];
    validateCurrentBrand(switcherBrands);
    renderBrandSwitcher();
    if (window.onBrandsLoaded) window.onBrandsLoaded();
  }).catch(function () {});
}

function validateCurrentBrand(list) {
  const cur = getBrandId();
  const exists = list.some(function (b) { return b.id === cur; });
  if (!exists) {
    if (list.length) {
      let minId = list[0].id;
      list.forEach(function (b) { if (b.id < minId) minId = b.id; });
      setBrandId(minId);
    } else {
      clearBrandId();
    }
  }
}

function currentBrandName() {
  const cur = getBrandId();
  const b = switcherBrands.find(function (x) { return x.id === cur; });
  return b ? (b.brand_name || "未命名品牌") : "";
}

function renderBrandSwitcher() {
  const wrap = document.getElementById("brand-switch-wrap");
  if (!wrap) return;
  wrap.innerHTML = "";

  if (!switcherBrands.length) {
    /* 无品牌（防御态）：主按钮「＋ 创建品牌」，点击直接打开新建品牌弹窗 */
    const btn = document.createElement("button");
    btn.className = "btn btn-primary brand-capsule";
    btn.textContent = "＋ 创建品牌";
    btn.addEventListener("click", function () {
      openBrandCreateModal();
    });
    wrap.appendChild(btn);
    return;
  }

  const capsule = document.createElement("button");
  capsule.className = "brand-capsule";
  capsule.id = "brand-capsule";
  capsule.innerHTML =
    '<span id="brand-capsule-name">' + esc(currentBrandName()) + "</span> <span class=\"bc-arrow\">▾</span>";
  capsule.addEventListener("click", function (e) {
    e.stopPropagation();
    if (switcherDropdownOpen) {
      closeBrandDropdown();
    } else {
      openBrandDropdown();
    }
  });
  wrap.appendChild(capsule);

  const dropdown = document.createElement("div");
  dropdown.className = "brand-dropdown hidden";
  dropdown.id = "brand-dropdown";
  dropdown.innerHTML =
    '<div id="bd-list"></div>' +
    '<div class="bd-divider"></div>' +
    '<div class="bd-new" id="bd-new">＋ 新建品牌</div>' +
    '<div class="bd-full hidden" id="bd-full">最多 5 个品牌，已经满了。</div>';
  wrap.appendChild(dropdown);

  const list = dropdown.querySelector("#bd-list");
  const cur = getBrandId();
  switcherBrands.forEach(function (b) {
    const name = b.brand_name || "未命名品牌";
    const row = document.createElement("div");
    row.className = "bd-row" + (b.id === cur ? " current" : "");
    row.textContent = (b.id === cur ? "✓ " : "") + name;
    row.addEventListener("click", function () {
      if (b.id === cur) {
        closeBrandDropdown();
        return;
      }
      sessionStorage.setItem("geo_brand_switched_toast", name);
      setBrandId(b.id);
      location.reload();
    });
    list.appendChild(row);
  });

  const newRow = dropdown.querySelector("#bd-new");
  const fullHint = dropdown.querySelector("#bd-full");
  if (switcherBrands.length >= 5) {
    newRow.classList.add("disabled");
    fullHint.classList.remove("hidden");
  }
  newRow.addEventListener("click", function () {
    if (switcherBrands.length >= 5) return;
    closeBrandDropdown();
    if (location.pathname.indexOf("settings.html") >= 0 && location.hash === "#brand-new") {
      /* 已停在设置页品牌新建落点（hash 不变不会重载）：直接打开弹窗 */
      openBrandCreateModal();
    } else {
      location.href = "/static/settings.html#brand-new";
    }
  });

  document.addEventListener("click", function (e) {
    if (switcherDropdownOpen && !wrap.contains(e.target)) {
      closeBrandDropdown();
    }
  });
  document.addEventListener("keydown", function escHandler(e) {
    if (e.key === "Escape") {
      closeBrandDropdown();
      document.removeEventListener("keydown", escHandler);
    }
  });
}

function openBrandDropdown() {
  const dropdown = document.getElementById("brand-dropdown");
  if (!dropdown) return;
  dropdown.classList.remove("hidden");
  switcherDropdownOpen = true;
}

function closeBrandDropdown() {
  const dropdown = document.getElementById("brand-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  switcherDropdownOpen = false;
}

/* 切换/删除品牌成功 Toast：sessionStorage 标记，刷新后只弹一次（03b 3.3） */
function showSwitchToastIfAny() {
  const name = sessionStorage.getItem("geo_brand_switched_toast");
  if (name) {
    sessionStorage.removeItem("geo_brand_switched_toast");
    showToast("已切换到「" + name + "」，下面看到的数据都是它的", "success");
    return;
  }
  const deleted = sessionStorage.getItem("geo_brand_deleted_toast");
  if (deleted) {
    sessionStorage.removeItem("geo_brand_deleted_toast");
    showToast("已删除品牌「" + deleted + "」及其全部数据", "success");
  }
}

/* ============================================================
   新建品牌弹窗（03b 4.1.4：N2 POST /api/brands，全站共用）
   顶栏「＋ 创建品牌」与设置页「＋ 新建品牌」均走这里
   ============================================================ */

function openBrandCreateModal() {
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML =
    '<div class="modal">' +
    '<div class="modal-head">新建品牌</div>' +
    '<div class="modal-body">' +
    '<div class="field"><label class="field-label">品牌名 <span class="required">*</span></label>' +
    '<input class="input" id="nb-name" placeholder="比如：某某母婴"></div>' +
    '<div class="field"><label class="field-label">产品名</label>' +
    '<input class="input" id="nb-product" placeholder="比如：智慧教育/系统集成"></div>' +
    '<div class="field"><label class="field-label">品牌别名</label>' +
    '<input class="input" id="nb-aliases" placeholder="AI 回答里可能出现的其他叫法，多个用逗号分开"></div>' +
    '<div class="field"><label class="field-label">一句话介绍</label>' +
    '<textarea class="textarea" id="nb-desc" style="min-height:80px" placeholder="告诉 AI 你是谁，最多 200 字"></textarea>' +
    '<div class="field-hint" id="nb-desc-count">0/200 字</div></div>' +
    '<div class="field" style="display:flex;align-items:center;gap:10px">' +
    '<label class="switch"><input type="checkbox" id="nb-auto" checked><span class="slider"></span></label>' +
    '<span style="font-weight:600">参加每日自动监测</span></div>' +
    '<div class="small-note">开启：纳入每天自动监测；关闭：自动监测跳过该品牌。</div>' +
    "</div>" +
    '<div class="modal-foot">' +
    '<button class="btn btn-secondary" data-act="cancel">取消</button>' +
    '<button class="btn btn-primary" data-act="save">保存</button>' +
    "</div></div>";
  document.body.appendChild(mask);

  const nameInput = mask.querySelector("#nb-name");
  const descInput = mask.querySelector("#nb-desc");
  nameInput.addEventListener("input", function () {
    if (this.value.length > 50) {
      this.value = this.value.slice(0, 50);
      showToast("品牌名太长了，请控制在 50 字以内", "error");
    }
  });
  descInput.addEventListener("input", function () {
    const n = this.value.length;
    mask.querySelector("#nb-desc-count").textContent = n + "/200 字";
    if (n > 200) {
      this.value = this.value.slice(0, 200);
      mask.querySelector("#nb-desc-count").textContent = "200/200 字";
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
    apiPost("/api/brands", {
      brand_name: name,
      product_name: mask.querySelector("#nb-product").value.trim(),
      brand_aliases: mask.querySelector("#nb-aliases").value,
      brand_description: descInput.value.trim(),
      auto_monitor: mask.querySelector("#nb-auto").checked,
    }).then(function (saved) {
      close();
      showToast("已创建品牌「" + (saved.brand_name || name) + "」，点右上角切换器就能切换过去", "success");
      refreshBrandSwitcher();
      if (window.onBrandsChanged) window.onBrandsChanged();
    }).catch(function () {
      btn.disabled = false;
    });
  });

  nameInput.focus();

  function close() {
    mask.remove();
  }
}

/* ============================================================
   首次使用引导（UI 文档 3.2：三步引导）
   触发条件：品牌未填 或 没有任何钥匙 或 没有任何监测轮次
   ============================================================ */

function maybeShowGuide() {
  return Promise.all([
    geoApi("/api/brands"),
    geoApi("/api/settings/keys"),
    geoApi("/api/overview"),
  ]).then(function (res) {
    const brands = res[0] || [];
    let brand = brands.find(function (b) { return b.id === getBrandId(); }) || brands[0] || {};
    const keys = res[1];
    const overview = res[2];

    const brandOk = brands.some(function (b) { return (b.brand_name || "").trim(); });
    const keyOk = keys.some(function (k) {
      return k.engine !== "analysis" && k.configured;
    });
    const roundOk = (overview.round_count || 0) > 0;

    if (brandOk && keyOk && roundOk) return;

    const steps = [
      { title: "创建你的第一个品牌", skipable: false, skipText: "", nextText: "保存并下一步" },
      { title: "去设置页填钥匙", skipable: true, skipText: "先跳过，以后填", nextText: "下一步" },
      { title: "发起第一次监测", skipable: true, skipText: "先跳过，以后填", nextText: "去发起监测" },
    ];

    let current = 0;

    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML =
      '<div class="modal guide-modal">' +
      '<div class="modal-body">' +
      '<div class="guide-head">三步开始用 GEO 优化系统</div>' +
      '<div class="guide-body">' +
      '<div class="guide-indicators">' +
      '<div class="guide-dot" data-g="0"><span class="gd-num">1</span>创建你的第一个品牌</div>' +
      '<div class="guide-dot" data-g="1"><span class="gd-num">2</span>去设置页填钥匙</div>' +
      '<div class="guide-dot" data-g="2"><span class="gd-num">3</span>发起第一次监测</div>' +
      "</div>" +
      '<div class="guide-content" id="guide-content"></div>' +
      "</div>" +
      "</div>" +
      '<div class="modal-foot">' +
      '<button class="btn btn-secondary" id="guide-skip">暂时跳过</button>' +
      '<button class="btn btn-primary" id="guide-next">下一步</button>' +
      "</div>" +
      "</div>";

    document.body.appendChild(mask);
    mask.addEventListener("click", function (e) {
      if (e.target === mask) closeGuide();
    });
    mask.querySelectorAll(".guide-dot").forEach(function (dot) {
      dot.addEventListener("click", function () {
        gotoStep(parseInt(dot.getAttribute("data-g"), 10));
      });
    });
    document.getElementById("guide-skip").addEventListener("click", function () {
      if (steps[current].skipable) closeGuide();
    });
    document.getElementById("guide-next").addEventListener("click", onNext);

    function render() {
      const step = steps[current];
      const content = document.getElementById("guide-content");
      const skipBtn = document.getElementById("guide-skip");
      const nextBtn = document.getElementById("guide-next");

      mask.querySelectorAll(".guide-dot").forEach(function (dot, i) {
        dot.classList.toggle("active", i === current);
      });

      skipBtn.textContent = step.skipText || "暂时跳过";
      skipBtn.style.display = step.skipable ? "" : "none";
      nextBtn.textContent = step.nextText;

      if (current === 0) {
        const b = brand || {};
        content.innerHTML =
          '<div class="guide-step-title">创建你的第一个品牌</div>' +
          '<div class="guide-step-desc">请先填写品牌信息，系统才能向 5 家 AI 查询答案。之后可随时在设置页新增品牌。</div>' +
          '<div class="field"><label class="field-label">品牌名 <span class="required">*</span></label>' +
          '<input class="input" id="g-brand-name" placeholder="比如：某某母婴" value="' + esc(b.brand_name || "") + '"></div>' +
          '<div class="field"><label class="field-label">产品名</label>' +
          '<input class="input" id="g-brand-product" placeholder="比如：婴儿推车" value="' + esc(b.product_name || "") + '"></div>' +
          '<div class="field"><label class="field-label">一句话介绍</label>' +
          '<textarea class="textarea" id="g-brand-desc" placeholder="让别人快速了解你，比如：做母婴用品的国产品牌" style="min-height:70px">' + esc(b.brand_description || "") + "</textarea>" +
          "</div>";
        document.getElementById("g-brand-name").addEventListener("input", function () {
          if (this.value.length > 50) {
            this.value = this.value.slice(0, 50);
            showToast("品牌名太长了，请控制在 50 字以内", "error");
          }
        });
      } else if (current === 1) {
        const engines = keys.filter(function (k) { return k.engine !== "analysis"; });
        const rows = engines.map(function (k) {
          const state = k.configured
            ? '<span class="tag tag-green">已填 ✓</span>'
            : '<span class="tag tag-gray">未填</span>';
          return '<div class="guide-key-row"><span>' + esc(k.display_name) + "</span>" +
            '<span class="guide-key-state">' + state + "</span></div>";
        }).join("");
        content.innerHTML =
          '<div class="guide-step-title">去设置页填钥匙</div>' +
          '<div class="guide-step-desc">系统将代表你向 5 家 AI 提问，需要每家 AI 的 API 钥匙。请在设置页填写，一次填写长期使用。</div>' +
          '<div class="guide-key-list">' + rows + "</div>" +
          '<button class="btn btn-primary" id="g-go-settings">去设置页填写</button>';
        document.getElementById("g-go-settings").addEventListener("click", function () {
          location.href = "/static/settings.html#keys";
        });
      } else {
        content.innerHTML =
          '<div class="guide-step-title">发起第一次监测</div>' +
          '<div class="guide-step-desc">系统将把问题库中的问题逐一发送给 5 家 AI（约 10-15 分钟，费用约 1-2.5 元），并分析 AI 是否提及你的品牌。</div>' +
          '<button class="btn btn-primary" id="g-go-monitor">去发起监测</button>';
        document.getElementById("g-go-monitor").addEventListener("click", function () {
          closeGuide();
          location.href = "/static/monitor.html";
        });
      }
    }

    function gotoStep(i) {
      current = i;
      render();
    }

    function onNext() {
      if (current === 0) {
        const name = document.getElementById("g-brand-name").value.trim();
        if (!name) {
          showToast("请先填品牌名，这是监测的基础", "error");
          return;
        }
        if (name.length > 50) {
          showToast("品牌名太长了，请控制在 50 字以内", "error");
          return;
        }
        const nextBtn = document.getElementById("guide-next");
        nextBtn.disabled = true;
        apiPost("/api/brands", {
          brand_name: name,
          product_name: document.getElementById("g-brand-product").value.trim(),
          brand_aliases: [],
          brand_description: document.getElementById("g-brand-desc").value.trim(),
        }).then(function (saved) {
          brand = saved;
          setBrandId(saved.id);
          refreshBrandSwitcher();
          showToast("品牌信息已保存", "success");
          nextBtn.disabled = false;
          gotoStep(1);
        }).catch(function () {
          nextBtn.disabled = false;
        });
        return;
      }
      if (current === 2) {
        closeGuide();
        location.href = "/static/monitor.html";
        return;
      }
      gotoStep(current + 1);
    }

    function closeGuide() {
      mask.remove();
    }

    render();
  }).catch(function () {});
}
